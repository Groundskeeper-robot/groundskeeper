"""Aerial imagery, fetched and cached by the server (the browser never sends the home location anywhere).

* The yard: one image covering the map's bounds plus padding, from the chosen source at that source's own
  resolution (capped at MAX_PX a side). Sources come from the catalog (catalog.py): public survey imagery that needs
  no key, worldwide services that need the user's own key (their terms limit caching, so their images are refetched
  daily and their credit is always shown), and the user's own sources.
* Context around it, as map tiles proxied and cached by the server:
  - from zoom 12 in, the chosen source itself, but only for tiles within CONTEXT_RADIUS_M of the yard and inside
    the source's coverage;
  - zoomed out, the catalog's worldwide context layer (Sentinel-2 cloudless by EOX by default), asked only for
    zoom <= its max_zoom, so it learns roughly which city is being viewed and never the yard. It can be switched
    off (and replaced; its default licence is non-commercial).

Privacy: every source has a coverage outline, and the panel decides LOCALLY which sources cover the yard. A source
is only ever contacted (probed or fetched) when its outline contains the yard and, for a keyed source, once the user
has given it a key. Probing every source everywhere would send the home location to all of them. Kinds (the
protocols) never make that decision; every request goes through the gates here first.
"""
import asyncio
import base64
from urllib.parse import urlsplit
import hashlib
import io
import math
import json
import logging
import time
from pathlib import Path

import httpx
from PIL import Image

from . import kinds
from .catalog import (CATALOG_DIR, ID_RE, WORLD_MAX_ZOOM, clean, custom_entry, load, migrate_v1, prepare,
                      resolve_coverage, served_places, validate, write_json)
from .common import OVERZOOMED, ImageryError, NoImagery, added_detail, is_blank
from .coverage import covers
from .discover import cap_size, same_host_only
from .share import USERINFO, strip_keys
from .mercator import HALF, from_merc, metres_to_box, plan_size, square, tile_at, tile_bounds, to_merc

log = logging.getLogger(__name__)

DEFAULT_SOURCE = "usgs_naip"
CONTEXT_MIN_ZOOM = 12        # the chosen source draws the surroundings from this zoom in...
CONTEXT_RADIUS_M = 5000      # ...but only this close to the yard
SAMPLE_HALF_M = 20           # the coverage check fetches a 40 m square at the middle of the yard...
SAMPLE_PX = 512              # ...at the source's own resolution, at most this many pixels a side, and keeps it
SAMPLE_MOVED_M = 25          # a sample taken further than this from today's yard centre is taken again
TRY_DEADLINE_S = 120          # the whole add-a-source preview, zoom walk included
RETRY_AFTER_S = 3600         # a check that failed (timeout, server error) is tried again after this


async def _note_cross_host(response) -> None:
    """Sources may redirect; one that sends the panel to another host is worth knowing about (the yard's box goes
    along), so it is logged."""
    if response.has_redirect_location:
        from urllib.parse import urljoin, urlsplit
        target = urlsplit(urljoin(str(response.request.url), response.headers["location"])).hostname
        if target != response.request.url.host:
            log.warning("imagery: %s redirected to %s", response.request.url.host, target)


class Imagery:
    def __init__(self, data_dir: str, catalog_dir: Path = CATALOG_DIR):
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / "imagery"
        self.tiles = self.dir / "tiles"
        self.tiles.mkdir(parents=True, exist_ok=True)
        self.local_dir = self.dir / "sources"
        self.catalog_dir = catalog_dir
        notes, self.migration_problems = migrate_v1(self.data_dir)
        for note in notes:
            log.info("imagery: %s", note)
        self.reload()
        self.world_on = True
        self.gate = asyncio.Semaphore(6)
        self.http = httpx.AsyncClient(timeout=60, headers={"User-Agent": "yarbo-panel (self-hosted)"},
                                      follow_redirects=True, event_hooks={"response": [_note_cross_host]})
        # The add-a-source flow (discovery, try) may only reach the address the user gave: no redirect elsewhere.
        self.http_strict = httpx.AsyncClient(timeout=60, headers={"User-Agent": "yarbo-panel (self-hosted)"},
                                             follow_redirects=True, event_hooks={"response": [same_host_only, cap_size]})
        self.yard_meta: dict | None = None
        self.bbox: list[float] | None = None
        self.source_id = DEFAULT_SOURCE
        self.mode = "auto"
        self.keys: dict = {}
        self.previews = self.dir / "previews"
        self.previews.mkdir(exist_ok=True)
        self.lock = asyncio.Lock()       # held while the source or mode changes (requests and the upkeep task)
        self.wake = asyncio.Event()      # set when the yard's bounds become known or move: the upkeep runs at once
        meta = self.dir / "yard.json"
        if meta.exists():
            self.yard_meta = json.loads(meta.read_text())
            self.bbox = self.yard_meta.get("bbox")

    async def close(self) -> None:
        await self.http.aclose()
        await self.http_strict.aclose()

    # -- the catalog ---------------------------------------------------------------------------------------------
    def reload(self) -> None:
        cat = load(self.catalog_dir, self.dir)
        self.sources = list(cat.sources.values())
        self.places = served_places(self.sources)
        self.world = cat.world
        self.problems = self.migration_problems + cat.problems

    def adopt_custom(self, custom: dict | None) -> None:
        """The first panel kept its custom source in settings; it is now a local catalog entry (custom.json). Never
        fatal: a setting that cannot be carried over is reported in Settings."""
        if not isinstance(custom, dict) or not custom or (self.local_dir / "custom.json").exists():
            return
        try:
            write_json(self.local_dir / "custom.json", custom_entry(custom))
            log.info("imagery: moved the custom source to %s", self.local_dir / "custom.json")
        except Exception as exc:
            self.migration_problems.append(f"the old custom source setting could not be moved: {exc}")
            log.warning("imagery: the old custom source setting could not be moved: %s", exc)
        self.reload()

    def local_entry(self, sid: str) -> dict | None:
        path = self.local_dir / f"{sid}.json"
        if not ID_RE.fullmatch(sid) or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def snapshot_local(self, sid: str) -> bytes | None:
        """A local source file exactly as it is, to put back with restore_local (no validation either way)."""
        path = self.local_dir / f"{sid}.json"
        return path.read_bytes() if ID_RE.fullmatch(sid) and path.exists() else None

    def restore_local(self, sid: str, snapshot: bytes | None) -> None:
        if not ID_RE.fullmatch(sid):
            return
        path = self.local_dir / f"{sid}.json"
        if snapshot is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(snapshot)
        self.reload()

    def save_local(self, entry: dict) -> dict:
        """Validate and write a local source (adding, or replacing by id), then reload. Raises ImageryError."""
        sid = str(entry.get("id", ""))
        if not ID_RE.fullmatch(sid):
            raise ImageryError("a source id is 3 to 41 lower-case letters, digits or _, starting with a letter")
        check = {"license": {"id": "unknown", "name": "Not stated", "commercial": None, "credit": True}, **entry}
        problems = validate(resolve_coverage(check, [self.dir / "coverage", self.catalog_dir / "coverage"]))
        if problems:
            raise ImageryError("; ".join(problems))
        write_json(self.local_dir / f"{sid}.json", entry)
        self.reload()
        return self.source(sid)

    def add_local(self, entry: dict, replace: bool = False) -> dict:
        """Save a source of the user's own (the add-a-source flow). Its id comes from its name unless given; it is
        asked about the yard only (coverage World). Replacing an id, bundled or local, has to be asked for."""
        from .discover import suggest_id
        if not isinstance(entry, dict):
            raise ImageryError("no source to save")
        e = clean(entry)
        if "@" in urlsplit(str(e.get("url", ""))).netloc:
            raise ImageryError("addresses with a user name and password are not supported; a key in the address "
                               "(token=…) is")
        taken = {c["id"] for c in self.sources}
        sid = str(entry.get("id") or "") or suggest_id(e.get("name", ""), taken)
        if sid in taken and not replace:
            raise ImageryError(f"there is already a source called {sid!r}")
        e = {"id": sid, "coverage": {"type": "World"}, **e, "id": sid}
        e.setdefault("license", {"id": "unknown", "name": "Not stated", "commercial": None, "credit": True})
        e.setdefault("region", "")
        return self.save_local(e)

    def outlines(self) -> list[dict]:
        """The outline files an exported source can refer to (catalog/coverage/), with their names."""
        out = []
        for p in sorted((self.catalog_dir / "coverage").glob("*.geojson")):
            try:
                name = json.loads(p.read_text(encoding="utf-8")).get("properties", {}).get("name") or p.stem
            except (ValueError, OSError, AttributeError):
                continue
            out.append({"ref": p.stem, "name": name})
        return out

    async def try_entry(self, entry: dict, lon: float, lat: float) -> dict:
        """The add-a-source preview: a 40 m sample of the yard from an entry that is not saved. This is the first
        request that carries the yard to that service, made because the user asked. A tile source is first walked
        down from its deepest zoom to the one that has tiles at the yard, which also gives its resolution."""
        draft = prepare({**clean(entry if isinstance(entry, dict) else {}), "id": "draft_source",
                         "coverage": {"type": "World"}}, [self.dir / "coverage", self.catalog_dir / "coverage"])
        problems = validate(draft)
        if problems:
            raise ImageryError("; ".join(problems))
        kind = kinds.get(draft["kind"])
        t0 = time.monotonic()
        try:
            return await asyncio.wait_for(self._try(kind, draft, lon, lat, t0), TRY_DEADLINE_S)
        except asyncio.TimeoutError:
            return {"ok": False, "detail": f"no answer within {TRY_DEADLINE_S} s", "found": {}}

    async def _try(self, kind, draft: dict, lon: float, lat: float, t0: float) -> dict:
        found = {}
        try:
            if draft["kind"] == "tiles":
                found = await self._deepest_zoom(kind, draft, lon, lat)
                if not found.get("max_zoom"):
                    return {"ok": False, "detail": found["detail"], "found": {}}
                draft.update(found)
            img, mpp = await self.render(draft, square(lon, lat, SAMPLE_HALF_M),
                                         max(draft["native_m"], 2 * SAMPLE_HALF_M / SAMPLE_PX), http=self.http_strict)
        except NoImagery:
            return {"ok": False, "detail": "it answered with a blank image: no imagery at your yard", "found": found}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)[:200] or type(exc).__name__, "found": found}
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return {"ok": True, "image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
                "m_per_px": round(mpp, 4), "size": [img.width, img.height],
                "seconds": round(time.monotonic() - t0, 1), "found": found}

    async def _deepest_zoom(self, kind, draft: dict, lon: float, lat: float) -> dict:
        """Walk down from the claimed deepest zoom to the first with real tiles at the yard. A blank tile, an enlarged
        one (a service overzooming past its data) and a placeholder (the same picture at two zooms) do not count."""
        top = min(int(draft.get("max_zoom") or 21), 22)
        bottom = max(top - 12, 0)
        deeper = None
        for z in range(top, bottom - 1, -1):
            x, y = tile_at(lon, lat, z)
            img = await kind.fetch_tile(self.http_strict, draft, "", z, x, y)
            if img is None or is_blank(img):
                deeper = None
                continue
            placeholder = deeper is not None and deeper.size == img.size and deeper.tobytes() == img.tobytes()
            deeper = img
            if placeholder:
                continue                                  # the same picture one zoom in: a placeholder
            below = await kind.fetch_tile(self.http_strict, draft, "", z - 1, x // 2, y // 2) if z else None
            if below is not None and not is_blank(below) and below.size == img.size:
                if below.tobytes() == img.tobytes():
                    continue                              # the same picture one zoom out: a placeholder
                if added_detail(img, below, x % 2, y % 2) < OVERZOOMED:
                    continue                              # its parent, enlarged: the service has no more detail here
            return {"max_zoom": z, "tile_size": 512 if img.width >= 512 else 256,
                    "native_m": round(2 * HALF * math.cos(math.radians(lat)) / (img.width * 2 ** z), 4)}
        return {"detail": f"no real tiles at your yard from zoom {top} down to {bottom}"}

    def delete_local(self, sid: str) -> None:
        if ID_RE.fullmatch(sid):
            (self.local_dir / f"{sid}.json").unlink(missing_ok=True)
            self.reload()

    def configure(self, source: dict | None, keys: dict | None, context: dict | None = None) -> None:
        """source: {"provider": id, "mode": "auto" | "manual"}. A choice saved before automatic mode existed (a provider
        and no mode) stays the user's own; a fresh install starts automatic."""
        source = source or {}
        self.source_id = source.get("provider") or DEFAULT_SOURCE
        self.mode = source.get("mode") or ("manual" if source.get("provider") else "auto")
        self.set_keys(keys)
        if context is not None:
            self.world_on = context.get("world", True) is not False

    def _http_for(self, src: dict):
        """Bundled sources may redirect (logged when another host is involved); the user's own may not."""
        return self.http if src.get("origin") == "bundled" else self.http_strict

    def set_keys(self, keys: dict | None) -> None:
        self.keys = dict(keys or {})

    def source(self, sid: str | None = None) -> dict:
        sid = sid or self.source_id
        src = next((c for c in self.sources if c["id"] == sid), None)
        if src is None:
            raise ImageryError("no custom source is saved" if sid == "custom" else f"unknown imagery source {sid!r}")
        return src

    def usable_here(self, src: dict, lon: float | None, lat: float | None) -> bool:
        """May this source be contacted for a yard at (lon, lat)? Only if its outline contains the yard, and, for a
        keyed source, only once the user has given it a key."""
        if src.get("auth") and not self.keys.get(src["id"]):
            return False
        return lon is not None and covers(src, lon, lat)

    def catalog(self, lon: float | None = None, lat: float | None = None) -> list[dict]:
        out = []
        for c in self.sources:
            auth = c.get("auth") or None
            out.append({k: c.get(k) for k in ("id", "name", "provider", "country", "region", "homepage", "kind",
                                              "native_m", "vintage", "attribution", "license", "verified", "origin",
                                              "coverage_ref", "url")} |
                       {"url": strip_keys(c["url"])[0],             # a key in an address never goes back out
                        "name": USERINFO.sub("", c["name"]), "attribution": USERINFO.sub("", c["attribution"]),
                        "auth": {k: auth.get(k) for k in ("label", "signup")} if auth else None,
                        "has_key": bool(self.keys.get(c["id"])),
                        "covers": None if lon is None else covers(c, lon, lat),
                        "worldwide": c["coverage"]["type"] == "World",
                        "places": self.places.get(c["id"], []),     # the states or provinces it serves, by name
                        # the names the current Settings card reads
                        "needs_key": auth["label"] if auth else None, "terms": c["license"].get("name", "")})
        return out

    def kinds(self) -> list[dict]:
        return [k.describe() for k in kinds.REGISTRY.values()]

    def candidates(self, lon: float, lat: float) -> list[str]:
        """The sources that may be probed for a yard at (lon, lat)."""
        return [c["id"] for c in self.sources if self.usable_here(c, lon, lat)]

    def _key_for(self, src: dict) -> str:
        if not src.get("auth"):
            return ""
        key = self.keys.get(src["id"])
        if not key:
            raise ImageryError(f"{src['name']} needs {src['auth']['label']}")
        return key

    # -- the yard image ------------------------------------------------------------------------------------------
    async def render(self, src: dict, bbox: list[float], native_m: float | None = None,
                     http=None) -> tuple[Image.Image, float]:
        w, s, e, n = bbox
        mpp, width, height = plan_size(bbox, native_m or src["native_m"])
        box = (*to_merc(w, s), *to_merc(e, n))
        img = await kinds.get(src["kind"]).fetch_box(http or self._http_for(src), src, self._key_for(src), box, width, height)
        if is_blank(img):
            raise NoImagery(f"{src['name']} has no imagery here")
        return img, mpp

    def _allowed(self, src: dict, bbox: list[float]) -> None:
        lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        if not covers(src, lon, lat):
            raise ImageryError(f"{src['name']} does not cover this yard ({src.get('region') or 'elsewhere'})")

    async def ensure_yard(self, bbox: list[float] | None = None, force: bool = False,
                          strict: bool | None = None) -> dict | None:
        """bbox = [west, south, east, north]. Fetches when the source, the bounds or the resolution change, when the
        source's cache period has passed, or when forced. Keeps the previous image if a fetch fails; strict (default:
        the same as force) raises instead."""
        strict = force if strict is None else strict
        bbox = bbox or self.bbox
        if not bbox:
            return self.yard_meta
        if bbox != self.bbox:
            self.wake.set()
        self.bbox = bbox
        try:
            src = self.source()
            self._allowed(src, bbox)
        except ImageryError as exc:
            log.warning("imagery: %s", exc)
            if strict:
                raise
            return self.yard_meta
        mpp, width, height = plan_size(bbox, src["native_m"])
        ident = json.dumps([src["id"], src["url"], src.get("layers"), [round(v, 6) for v in bbox], round(mpp, 4)])
        image = self.dir / f"yard-{hashlib.sha256(ident.encode()).hexdigest()[:12]}.jpg"
        fresh = image.exists() and time.time() - image.stat().st_mtime < src["cache_days"] * 86400
        if force or not fresh:
            try:
                img, mpp = await self.render(src, bbox)
                img.save(image, "JPEG", quality=88, optimize=True)
                log.info("yard imagery from %s: %dx%d px at %.3f m/px", src["id"], img.width, img.height, mpp)
            except Exception as exc:
                log.warning("yard imagery from %s failed: %s", src["id"], exc)
                if src.get("auth") and image.exists() and not fresh:
                    image.unlink(missing_ok=True)        # a keyed service's terms limit how long it may be kept
                    self.forget_yard_from(src["id"])
                if strict:
                    raise ImageryError(str(exc)) from exc
                if not image.exists():
                    return self.yard_meta
        w, s, e, n = bbox
        self.yard_meta = {
            "url": f"/imagery/{image.name}", "bbox": bbox,
            "coordinates": [[w, n], [e, n], [e, s], [w, s]],
            "source": {"id": src["id"], "name": src["name"], "attribution": src["attribution"],
                       "native_m": src["native_m"], "m_per_px": round(mpp, 4), "size": [width, height],
                       "fetched": image.stat().st_mtime},
            "attribution": src["attribution"],
        }
        (self.dir / "yard.json").write_text(json.dumps(self.yard_meta))
        self._cleanup(keep=image.name)
        return self.yard_meta

    def forget_yard_from(self, sid: str) -> None:
        """Stop showing a yard image from this source (its key was removed, or the source was)."""
        if self.yard_meta and (self.yard_meta.get("source") or {}).get("id") == sid:
            (self.dir / self.yard_meta["url"].rsplit("/", 1)[-1]).unlink(missing_ok=True)
            (self.dir / "yard.json").unlink(missing_ok=True)
            self.yard_meta = None

    def _cleanup(self, keep: str) -> None:
        """Drop yard images from other sources or bounds after a while (a switch back within 30 days reuses one)."""
        for p in self.dir.glob("yard-*.jpg"):
            if p.name != keep and time.time() - p.stat().st_mtime > 30 * 86400:
                p.unlink(missing_ok=True)

    async def probe(self, sid: str, lon: float, lat: float) -> dict:
        """Is there real imagery at (lon, lat)? Fetches a 40 m square at the source's own resolution (at most
        SAMPLE_PX a side) and keeps it as the source's preview in the picker. Refuses, without any network request,
        for a source that does not cover the spot or still needs a key."""
        base = {"id": sid, "at": time.time(), "where": [round(lon, 6), round(lat, 6)]}
        try:
            src = self.source(sid)
        except ImageryError as exc:
            return {**base, "ok": False, "detail": str(exc)}
        base["ident"] = self.ident(src)
        if src.get("auth") and not self.keys.get(src["id"]):
            return {**base, "ok": None, "detail": f"needs {src['auth']['label']}"}
        if not covers(src, lon, lat):
            return {**base, "ok": None, "detail": f"does not cover this yard ({src.get('region') or 'elsewhere'})"}
        preview = self.previews / f"{sid}.jpg"
        try:
            img, mpp = await self.render(src, square(lon, lat, SAMPLE_HALF_M),
                                         max(src["native_m"], 2 * SAMPLE_HALF_M / SAMPLE_PX))
            img.save(preview, "JPEG", quality=88)
            return {**base, "ok": True, "native_m": src["native_m"], "m_per_px": round(mpp, 4),
                    "size": [img.width, img.height], "detail": "has imagery here"}
        except NoImagery as exc:
            preview.unlink(missing_ok=True)
            return {**base, "ok": False, "native_m": src["native_m"], "detail": str(exc)[:160]}
        except Exception as exc:                 # a timeout or a server error says nothing about the imagery
            return {**base, "ok": False, "at": 0, "native_m": src["native_m"], "error": str(exc)[:160] or type(exc).__name__,
                    "error_at": time.time(), "detail": f"could not check it: {str(exc)[:120]}"}

    def ident(self, src: dict) -> str:
        """A source's identity for its sample: a sample of something else (an edited custom source) is not reused."""
        return hashlib.sha256(json.dumps([src["id"], src["kind"], src["url"], src.get("layers"), src["native_m"]])
                              .encode()).hexdigest()[:10]

    def drop_sample(self, sid: str) -> None:
        if ID_RE.fullmatch(sid):
            (self.previews / f"{sid}.jpg").unlink(missing_ok=True)

    def preview_file(self, sid: str) -> Path | None:
        p = self.previews / f"{sid}.jpg"
        return p if ID_RE.fullmatch(sid) and p.exists() else None

    async def check(self, ids: list[str], lon: float, lat: float, previous: dict | None = None) -> dict:
        """Probe these sources at the yard; any that may not be contacted there are dropped first, whatever asked.
        A passing failure keeps what the previous check of the same source at the same spot found, noting the error."""
        allowed = set(self.candidates(lon, lat))
        results = await asyncio.gather(*(self.probe(i, lon, lat) for i in dict.fromkeys(ids) if i in allowed))
        out = {}
        for r in results:
            prev = (previous or {}).get(r["id"]) or {}
            if r.get("error") and prev.get("ok") in (True, False) and not prev.get("error_only") \
                    and prev.get("ident") == r.get("ident") and prev.get("where") == r.get("where"):
                r = {**prev, "error": r["error"], "error_at": r["error_at"]}
            elif r.get("error"):
                r = {**r, "error_only": True}
            out[r["id"]] = r
        return out

    def stale(self, probe: dict, lon: float, lat: float, background: bool = False) -> list[str]:
        """The sources that may be checked here and whose sample is missing, old, of a source since changed, from
        somewhere else, or failed more than RETRY_AFTER_S ago. In the background only free sources and the one in use
        are kept fresh: keyed services are re-checked when asked (each check is a request on the user's key)."""
        out = []
        for sid in self.candidates(lon, lat):
            src = self.source(sid)
            if background and src.get("auth") and sid != self.source_id:
                continue
            r = probe.get(sid) or {}
            where = r.get("where") or [None, None]
            moved = where[0] is None or metres_to_box(lon, lat, where[0], where[1], where[0], where[1]) > SAMPLE_MOVED_M
            days = min(src["cache_days"], 90) or 1
            failed = r.get("error") and time.time() - r.get("error_at", 0) > RETRY_AFTER_S
            if (moved or r.get("ident") != self.ident(src) or failed or r.get("ok") is None
                    or (not r.get("error") and time.time() - r.get("at", 0) > days * 86400)
                    or (r.get("ok") and not self.preview_file(sid))):
                out.append(sid)
        return out

    def auto_choice(self, probe: dict, lon: float, lat: float) -> str | None:
        """Automatic mode's pick: the sharpest source with real imagery at the yard among those that need no key
        (bundled or the user's own), catalog order breaking ties. Samples of a source since changed do not count."""
        free = [c for c in self.sources if not c.get("auth") and self.usable_here(c, lon, lat)
                and (probe.get(c["id"]) or {}).get("ok") is True and probe[c["id"]].get("ident") == self.ident(c)]
        return min(free, key=lambda c: c["native_m"])["id"] if free else None

    def fingerprint(self, lon: float, lat: float) -> str:
        """What automatic mode chose from: changes when an update adds, removes or alters a free source here."""
        free = [[c["id"], c["url"], c.get("layers"), c["native_m"]] for c in self.sources
                if not c.get("auth") and self.usable_here(c, lon, lat)]
        return hashlib.sha256(json.dumps(free).encode()).hexdigest()[:12]

    # -- context tiles (the map around the yard) -----------------------------------------------------------------
    def yard_centre(self) -> tuple[float, float] | None:
        if not self.bbox:
            return None
        w, s, e, n = self.bbox
        return (w + e) / 2, (s + n) / 2

    def _source_ident(self, src: dict) -> str:
        return hashlib.sha256(json.dumps([src["id"], src["url"], src.get("layers")]).encode()).hexdigest()[:10]

    def context_info(self) -> dict:
        info = {"world": None, "source": None}
        if self.world and self.world_on:
            info["world"] = {k: self.world.get(k) for k in ("name", "attribution", "max_zoom")} | \
                            {"terms": self.world["license"].get("name", "")}
        centre = self.yard_centre()
        try:
            src = self.source()
        except ImageryError:
            src = None
        if src and centre and self.usable_here(src, *centre):
            info["source"] = {"min_zoom": CONTEXT_MIN_ZOOM,
                              "max_zoom": max(CONTEXT_MIN_ZOOM, kinds.get(src["kind"]).max_zoom(src, centre[1])),
                              "version": self._source_ident(src), "radius_m": CONTEXT_RADIUS_M}
        return info

    async def _cached_tile(self, folder: str, z: int, x: int, y: int, days: float, fetch) -> bytes | None:
        """A tile from the disk cache, or fetched; blank tiles are remembered so they are not asked for again."""
        path = self.tiles / folder / str(z) / str(x) / f"{y}.jpg"
        blank = path.with_suffix(".blank")
        if path.exists() and time.time() - path.stat().st_mtime < days * 86400:
            return path.read_bytes()
        if blank.exists() and time.time() - blank.stat().st_mtime < days * 86400:
            return None
        async with self.gate:
            try:
                img = await fetch()
            except Exception as exc:
                log.debug("context tile %s/%s/%s/%s failed: %s", folder, z, x, y, exc)
                return None
        path.parent.mkdir(parents=True, exist_ok=True)
        if img is None or is_blank(img):
            blank.touch()
            return None
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        path.write_bytes(buf.getvalue())
        blank.unlink(missing_ok=True)
        return buf.getvalue()

    async def world_tile(self, z: int, x: int, y: int) -> bytes | None:
        w = self.world
        if not (w and self.world_on) or not 0 <= z <= min(int(w["max_zoom"]), WORLD_MAX_ZOOM):
            return None
        return await self._cached_tile(f"world-{w['id']}", z, x, y, w["cache_days"],
                                       lambda: kinds.get("tiles").fetch_tile(self.http, w, "", z, x, y))

    async def source_tile(self, z: int, x: int, y: int) -> bytes | None:
        """The chosen source as a map tile, only near the yard and inside the source's coverage."""
        centre = self.yard_centre()
        if not centre:
            return None
        try:
            src = self.source()
        except ImageryError:
            return None
        kind = kinds.get(src["kind"])
        if z < CONTEXT_MIN_ZOOM or z > kind.max_zoom(src, centre[1]):
            return None
        if not self.usable_here(src, *centre):    # the yard must be in its outline (and its key given), not just the tile
            return None
        x0, y0, x1, y1 = tile_bounds(z, x, y)
        (w, s), (e, n) = from_merc(x0, y0), from_merc(x1, y1)
        if metres_to_box(centre[0], centre[1], w, s, e, n) > CONTEXT_RADIUS_M:
            return None
        corners = ((w, s), (w, n), (e, s), (e, n), ((w + e) / 2, (s + n) / 2))
        if not any(covers(src, lo, la) for lo, la in corners):
            return None
        key = self.keys.get(src["id"], "") if src.get("auth") else ""
        return await self._cached_tile(f"src-{self._source_ident(src)}", z, x, y, src["cache_days"],
                                       lambda: kind.fetch_tile(self._http_for(src), src, key, z, x, y))

    def yard_file(self, name: str) -> Path | None:
        p = self.dir / name
        return p if p.name.startswith("yard-") and p.suffix == ".jpg" and p.exists() else None
