"""python -m app.imagery <command>: tools for people adding sources to the catalog (see catalog/README.md).

  verify [FILE ...] [--live] [--out DIR] [--key ID=KEY]  check source files (every bundled one by default)
  sheet [--out FILE]                                      every keyless source rendered at its landmark, one image
  table [--write | --check]                               catalog/SOURCES.md
  schema [--write | --check]                              catalog/schema.json
  coverage --country CC [--state NAME ...] [--clip=W,S,E,N] [--name REF]   an outline from Natural Earth (needs shapely)
  covers LAT LON                                          which bundled sources claim a place (offline, contacts none)

Nothing here goes near a home: --live renders each source at the public landmark in its `verified` block.
"""
import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from . import kinds
from .catalog import CATALOG_DIR, load, place_of, prepare, schema_doc, served_places, unknown_keys, validate
from .common import is_blank, is_image
from .coverage import covers
from .mercator import plan_size, square, tile_at, to_merc

UA = {"User-Agent": "yarbo-panel catalog verify (contributor tool)"}


# -- verify ---------------------------------------------------------------------------------------------------------------
class Report:
    def __init__(self, sid: str):
        self.sid, self.lines, self.failed, self.warned = sid, [], False, False

    def ok(self, msg):
        self.lines.append(("ok", msg))

    def warn(self, msg):
        self.lines.append(("WARN", msg))
        self.warned = True

    def fail(self, msg):
        self.lines.append(("FAIL", msg))
        self.failed = True


def _catalog_root(path: Path) -> Path:
    return next((p for p in path.resolve().parents if (p / "schema.json").exists()), CATALOG_DIR)


def _schema_validator():
    try:
        import jsonschema
    except ImportError:
        return None
    return jsonschema.Draft202012Validator(schema_doc())


def check_offline(path: Path, others: dict, validator) -> tuple[Report, dict | None]:
    r = Report(path.stem)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        r.fail(f"not readable JSON: {exc}")
        return r, None
    r.sid = str(data.get("id", path.stem))
    if validator is None:
        r.warn("schema not checked: pip install -r requirements-dev.txt")
    else:
        errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.path)))
        for e in errors[:6]:
            r.fail(f"schema: {'/'.join(map(str, e.path)) or '(top level)'}: {e.message}")
        if not errors:
            r.ok("matches the schema")
    root = _catalog_root(path)
    entry = prepare(data, [root / "coverage", CATALOG_DIR / "coverage"])
    for p in validate(entry):
        r.fail(p)
    extra = unknown_keys(entry)
    if extra:
        r.warn(f"unknown fields (ignored by the panel): {', '.join(extra)}")
    in_sources = path.resolve().parent.parent.name == "sources"
    if in_sources:
        if path.stem != r.sid:
            r.fail(f"the file must be named {r.sid}.json")
        want = "world" if entry.get("country") == "XX" else str(entry.get("country", "")).lower()
        if path.parent.name != want:
            r.fail(f"the file belongs in sources/{want}/")
        rel = os.path.relpath(root / "schema.json", path.parent).replace("\\", "/")
        if data.get("$schema") != rel:
            r.warn(f'"$schema": "{rel}" lets editors check the file as you type')
    ver = entry.get("verified")
    if not isinstance(ver, dict) or "landmark" not in ver:
        r.fail("no verified block: name the public landmark you checked it at")
        return r, entry
    lon, lat = ver["landmark"]
    regional = entry.get("coverage", {}).get("type") not in ("World", None)
    if regional and not covers(entry, lon, lat):
        r.fail(f"the landmark ({ver.get('place')}) is outside the source's own coverage")
    for other in others.values():
        if other["id"] == r.sid or other["coverage"]["type"] == "World" or not regional:
            continue
        if other.get("country") == entry.get("country"):
            continue                     # nested outlines within a country are fine (Maryland inside the lower 48)
        if covers(other, lon, lat):
            r.fail(f"{other['id']} ({other['country']}) also claims this landmark: one of the outlines is too generous")
        olm = (other.get("verified") or {}).get("landmark")
        if olm and covers(entry, *olm):
            r.fail(f"this outline contains {other['id']}'s landmark ({other['country']}): it is too generous")
    if not r.failed:
        r.ok(f"landmark inside its coverage, no other country's source claims it ({ver.get('place')})")
    return r, entry


async def render_at(http, src: dict, key: str = "", world: bool = False) -> tuple[Image.Image, float, float]:
    """The source at its landmark: 120 m across (20 km for the world layer), at most 512 px a side."""
    lon, lat = src["verified"]["landmark"]
    bbox = square(lon, lat, 10000 if world else 60)
    half = 10000 if world else 60
    mpp, w, h = plan_size(bbox, max(src["native_m"], 2 * half / 512))
    box = (*to_merc(bbox[0], bbox[1]), *to_merc(bbox[2], bbox[3]))
    t0 = time.monotonic()
    try:
        img = await kinds.get(src["kind"]).fetch_box(http, src, key, box, w, h)
    except httpx.TransportError:                 # one retry: public servers drop the odd connection
        img = await kinds.get(src["kind"]).fetch_box(http, src, key, box, w, h)
    return img, mpp, time.monotonic() - t0


async def _status_without_key(http, src: dict) -> tuple[int, bool]:
    """(HTTP status, whether it answered with an image) for a request at the landmark with an empty key."""
    kind = kinds.get(src["kind"])
    lon, lat = src["verified"]["landmark"]
    if isinstance(kind, kinds.TileKind):
        z = min(kind.max_zoom(src, lat), 16)
        url, headers = kind.request(src, "", z, *tile_at(lon, lat, z))
        resp = await http.get(url, headers=headers or None)
    else:
        bbox = square(lon, lat, 50)
        url, params, headers = kind.request(src, "", (*to_merc(bbox[0], bbox[1]), *to_merc(bbox[2], bbox[3])), 256, 256)
        resp = await http.get(url, params=params, headers=headers or None)
    return resp.status_code, is_image(resp)


async def _links(http, src: dict, r: Report) -> None:
    bad = 0
    for label, url in (("homepage", src.get("homepage")), ("licence", (src.get("license") or {}).get("url")),
                       ("key sign-up", (src.get("auth") or {}).get("signup"))):
        if not url:
            continue
        try:
            status = (await http.get(url, headers=UA)).status_code
        except httpx.HTTPError as exc:
            r.warn(f"{label} {url}: {exc.__class__.__name__}: {str(exc)[:90]}")
            bad += 1
            continue
        if status >= 400:
            r.warn(f"{label} {url} answered HTTP {status}")
            bad += 1
    if not bad:
        r.ok("links answer")


async def _service_checks(http, src: dict, r: Report, world: bool) -> None:
    kind = kinds.get(src["kind"])
    lon, lat = src["verified"]["landmark"]
    if src["kind"] == "tiles":
        z = kind.max_zoom(src, lat)
        here = await kind.fetch_tile(http, src, "", z, *tile_at(lon, lat, z))
        if here is None or is_blank(here):
            r.fail(f"no tile at max_zoom {z} at the landmark")
        elif not world:
            deeper_m = 2 * math.pi * 6378137 * math.cos(math.radians(lat)) / (kind.tile_px(src) * 2 ** (z + 1))
            if deeper_m < src["native_m"] * 0.75:
                r.ok(f"max_zoom {z}: zoom {z + 1} ({deeper_m:.2f} m/px) would only be upsampled from {src['native_m']} m")
            else:
                deeper = await kind.fetch_tile(http, src, "", z + 1, *tile_at(lon, lat, z + 1))
                if deeper is not None and not is_blank(deeper):
                    r.warn(f"it also serves zoom {z + 1} ({deeper_m:.2f} m/px): raise max_zoom?")
                else:
                    r.ok(f"max_zoom {z} is the deepest it serves")
    elif src["kind"] == "wms":
        resp = await http.get(src["url"].replace("{key}", ""), params={"SERVICE": "WMS", "REQUEST": "GetCapabilities",
                                                                      "VERSION": src.get("version") or "1.3.0"})
        text = resp.text
        missing = [l for l in src["layers"].split(",")
                   if not re.search(rf"<(?:\w+:)?Name>\s*{re.escape(l.strip())}\s*</(?:\w+:)?Name>", text, re.I)]
        if missing:
            r.fail(f"GetCapabilities does not list layer(s) {', '.join(missing)}")
        m = re.search(r"<(?:\w+:)?MaxWidth>\s*(\d+)", text)
        max_px = int(kind.opt(src, "max_px"))
        if m and max_px > int(m.group(1)):
            r.fail(f"max_px {max_px} is above the service's MaxWidth {m.group(1)}")
        if not missing:
            r.ok("layer listed in GetCapabilities" + (f", MaxWidth {m.group(1)}" if m else ""))
    else:
        try:
            info = (await http.get(src["url"].rstrip("/"), params={"f": "json"})).json()
        except (ValueError, httpx.HTTPError) as exc:
            r.fail(f"?f=json did not answer: {exc}")
            return
        if "error" in info:
            r.fail(f"?f=json: {info['error'].get('message')}")
            return
        px = info.get("pixelSizeX")
        max_w = min(v for v in (info.get("maxImageWidth"), info.get("maxImageHeight"), 10 ** 9) if v)
        if px:
            ratio = src["native_m"] / px
            if 0.8 <= ratio <= 1.25:
                r.ok(f"pixel size {px:.4g} matches native_m")
            elif 0.8 <= ratio / 0.3048 <= 1.25:
                r.ok(f"pixel size {px:.4g} ft matches native_m (the service works in feet)")
            elif ratio > 1:
                r.ok(f"the service's finest pixel is {px:.4g}; this entry fetches at {src['native_m']} "
                     f"(a mosaic of mixed resolutions, or deliberate)")
            else:
                r.warn(f"native_m {src['native_m']} claims finer than the service's finest pixel {px:.4g} "
                       f"(its own units: feet for State Plane)")
        max_px = int(kind.opt(src, "max_px"))
        if max_w < 10 ** 9 and max_px > max_w:
            r.fail(f"max_px {max_px} is above the service's largest export ({max_w} px)")


async def check_live(http, src: dict, r: Report, out: Path | None, key: str, world: bool = False) -> None:
    await _links(http, src, r)
    if src.get("auth") and not key:
        try:
            status, image = await _status_without_key(http, src)
        except httpx.HTTPError as exc:
            r.fail(f"could not reach it: {exc.__class__.__name__}")
            return
        if status in (400, 401, 403, 498, 499) or (status == 200 and not image):
            r.ok(f"asks for a key (HTTP {status}{', an error body' if status == 200 else ''}); "
                 f"pass --key {src['id']}=... to render it")
        elif status == 200:
            r.warn("answered with an image without a key: does it need one?")
        else:
            r.fail(f"answered HTTP {status} without a key; expected 401 or 403")
        return
    try:
        img, mpp, secs = await render_at(http, src, key, world)
    except Exception as exc:
        r.fail(f"render at the landmark failed: {str(exc)[:200]}")
        return
    if is_blank(img):
        r.fail("rendered a blank image at the landmark: no coverage there, or the wrong layer")
    else:
        r.ok(f"rendered {img.width}x{img.height} px at {mpp:.2f} m/px in {secs:.1f} s")
    if out:
        out.mkdir(parents=True, exist_ok=True)
        img.save(out / f"{src['id']}.jpg", "JPEG", quality=88)
    try:
        await _service_checks(http, src, r, world)
    except httpx.HTTPError as exc:
        r.warn(f"service check failed: {exc.__class__.__name__}")


def cmd_verify(args) -> int:
    cat = load()
    files = [Path(f) for f in args.files] or sorted((CATALOG_DIR / "sources").rglob("[!_]*.json"))
    world_file = CATALOG_DIR / "context" / "world.json"
    if not args.files and world_file.exists():
        files.append(world_file)
    keys = dict(k.split("=", 1) for k in args.key)
    validator = _schema_validator()
    ids: dict[str, Path] = {}
    reports, live = [], []
    for path in files:
        r, entry = check_offline(path, cat.sources, validator)
        if r.sid in ids:
            r.fail(f"id {r.sid} is also used by {ids[r.sid]}")
        ids[r.sid] = path
        reports.append(r)
        if args.live and entry and not r.failed:
            live.append((r, entry, path == world_file))

    async def run_live():
        gate = asyncio.Semaphore(4)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=UA) as http:
            async def one(r, entry, world):
                async with gate:
                    await check_live(http, entry, r, Path(args.out) if args.out else None, keys.get(r.sid, ""), world)
            await asyncio.gather(*(one(*x) for x in live))

    if live:
        asyncio.run(run_live())
    width = max(len(r.sid) for r in reports)
    for r in reports:
        state = "FAIL" if r.failed else "warn" if r.warned else "ok"
        print(f"{r.sid:<{width}}  {state}")
        for level, msg in r.lines:
            if level != "ok" or args.verbose:
                print(f"{'':<{width}}    {level}: {msg}")
    failed = sum(r.failed for r in reports)
    print(f"\n{len(reports)} checked, {failed} failed" + ("" if args.live else " (offline; add --live to render each one)"))
    return 1 if failed else 0


# -- sheet ----------------------------------------------------------------------------------------------------------------
def cmd_sheet(args) -> int:
    cat = load()
    items = [s for s in cat.sources.values() if not s.get("auth")] + ([cat.world] if cat.world else [])
    cell, label, cols = 256, 40, 4

    async def run():
        gate = asyncio.Semaphore(4)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=UA) as http:
            async def one(src):
                async with gate:
                    try:
                        img, mpp, _ = await render_at(http, src, world=src is cat.world)
                        return src, img, None
                    except Exception as exc:
                        return src, None, str(exc)[:60]
            return await asyncio.gather(*(one(s) for s in items))

    got = asyncio.run(run())
    rows = math.ceil(len(got) / cols)
    sheet = Image.new("RGB", (cols * (cell + 8) + 8, rows * (cell + label + 8) + 8), (14, 19, 17))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    for i, (src, img, err) in enumerate(got):
        x, y = 8 + (i % cols) * (cell + 8), 8 + (i // cols) * (cell + label + 8)
        if img is not None:
            sheet.paste(img.resize((cell, cell)), (x, y))
        else:
            draw.rectangle([x, y, x + cell, y + cell], outline=(240, 138, 110))
            draw.text((x + 8, y + 8), err or "failed", fill=(240, 138, 110), font=font)
        draw.text((x, y + cell + 4), src["id"], fill=(230, 236, 232), font=font)
        draw.text((x, y + cell + 20), src["verified"]["place"][:36], fill=(163, 177, 170), font=font)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"{out}: {len(got)} sources, {sum(1 for _, i, _ in got if i is None)} failed")
    return 0


# -- generated files ------------------------------------------------------------------------------------------------------
def _res(m: float) -> str:
    return f"{m * 100:.1f} cm" if m < 0.1 else f"{round(m * 100)} cm" if m < 1 else f"{m:g} m"


def _lic(src: dict) -> str:
    lic = src.get("license") or {}
    name = {"public-domain": "Public domain", "provider-terms": "Provider terms", "unknown": "Not stated"}.get(
        lic.get("id"), lic.get("id", ""))
    if lic.get("commercial") is False:
        name += ", non-commercial"
    return f"[{name}]({lic['url']})" if lic.get("url") else name


def _cell(text) -> str:
    return str(text or "").replace("|", "\\|")


def table_text(cat=None) -> str:
    cat = cat or load()
    bundled = [s for s in cat.sources.values() if s["origin"] == "bundled"]
    places = served_places(bundled)
    # By country; within one, country-wide sources first, then by state or province, the whole state before its counties.
    rows = sorted(bundled, key=lambda s: (s["country"] == "XX", s["country"], places.get(s["id"]) == [place_of(s)],
                                          place_of(s), "," in s["region"], s["native_m"], s["name"]))
    lines = ["# Imagery sources", "",
             "Generated from `catalog/sources/` by `python -m app.imagery table --write`; do not edit by hand.",
             "Each source was checked by its contributor at the public landmark named here.", "",
             "| Country | Source | Region | Sharpest | Licence | Key | Checked at |", "|---|---|---|---|---|---|---|"]
    for s in rows:
        name = f"[{_cell(s['name'])}]({s['homepage']})" if s.get("homepage") else _cell(s["name"])
        key = _cell(s["auth"]["label"]) if s.get("auth") else ""
        ver = s.get("verified") or {}
        lines.append(f"| {s['country']} | {name} | {_cell(s['region'])} | {_res(s['native_m'])} | {_lic(s)} | {key} "
                     f"| {_cell(ver.get('place'))}, {ver.get('date', '')} |")
    if cat.world:
        w = cat.world
        lines += ["", "## Zoomed-out world layer", "",
                  f"Shown only up to zoom {w['max_zoom']}, so it never sees a yard: "
                  f"[{_cell(w['name'])}]({w.get('homepage', '')}), {_res(w['native_m'])}, {_lic(w)}."]
    return "\n".join(lines) + "\n"


def schema_text() -> str:
    return json.dumps(schema_doc(), indent=2, ensure_ascii=False) + "\n"


def _write_or_check(path: Path, text: str, args) -> int:
    if args.check:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != text:
            print(f"{path} is out of date: python -m app.imagery {args.cmd} --write")
            return 1
        print(f"{path} is up to date")
        return 0
    if args.write:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path}")
    else:
        sys.stdout.write(text)
    return 0


# -- coverage from Natural Earth ------------------------------------------------------------------------------------------
NATURAL_EARTH = {
    "country": ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson",
                "ISO_A2_EH", "Natural Earth 1:50m countries (public domain)"),
    "state": ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson",
              "iso_a2", "Natural Earth 1:10m states and provinces (public domain)"),
}


def _natural_earth(level: str) -> list[dict]:
    url = NATURAL_EARTH[level][0]
    cache = Path.home() / ".cache" / "yarbo-panel" / url.rsplit("/", 1)[1]
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {url} (once; cached in {cache.parent})")
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as resp:
            resp.raise_for_status()
            with cache.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    return json.loads(cache.read_text(encoding="utf-8"))["features"]


def cmd_coverage(args) -> int:
    try:
        from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
        from shapely.ops import transform, unary_union
    except ImportError:
        print("needs shapely: pip install -r requirements-dev.txt")
        return 2
    level = "state" if args.state or args.all_states else "country"
    _, iso_field, credit = NATURAL_EARTH[level]
    cc = args.country.upper()
    wanted = {s.lower() for s in args.state or []}
    pick = [f for f in _natural_earth(level) if str(f["properties"].get(iso_field) or "").upper() == cc and
            (not wanted or wanted & {str(f["properties"].get(k) or "").lower() for k in ("name", "name_en", "gn_name", "woe_name")})]
    if not pick:
        print(f"nothing in Natural Earth for {cc}" + (f" / {', '.join(args.state)}" if args.state else ""))
        return 1
    if wanted:
        found = {str(f["properties"].get("name")) for f in pick}
        print(f"matched {len(pick)} of {len(wanted)} name(s): {', '.join(sorted(found))}")
    geom = unary_union([shape(f["geometry"]) for f in pick])
    if args.clip:
        geom = geom.intersection(box(*(float(v) for v in args.clip.split(","))))
    lat0 = geom.centroid.y
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57          # km per degree, near enough for an outline
    km = transform(lambda x, y, z=None: (x * kx, y * ky), geom).buffer(args.buffer_km, resolution=4)
    # Simplify until small enough, but never by more than 0.6 x the buffer: Douglas-Peucker moves no point further
    # than the tolerance, so the land itself always stays inside the outline.
    tol, cap = 0.5, args.buffer_km * 0.6
    while True:
        simple = km.simplify(min(tol, cap), preserve_topology=True)
        parts = sorted(simple.geoms if simple.geom_type == "MultiPolygon" else [simple], key=lambda p: -p.area)[:args.max_parts]
        points = sum(len(p.exterior.coords) for p in parts)
        if points <= args.max_points or tol >= cap:
            break
        tol *= 1.4
    parts = [Polygon(p.exterior) for p in parts]                      # outlines only: holes are ignored anyway
    out = transform(lambda x, y, z=None: (round(x / kx, 3), round(y / ky, 3)),
                    parts[0] if len(parts) == 1 else MultiPolygon(parts))
    name = args.name or (cc.lower() if not args.state else f"{cc.lower()}_{re.sub(r'[^a-z0-9]+', '_', args.state[0].lower()).strip('_')}")
    path = Path(args.out) if args.out else CATALOG_DIR / "coverage" / f"{name}.geojson"
    if path.exists() and not args.force:
        print(f"{path} exists; --force to replace it")
        return 1
    label = args.label or (", ".join(args.state) if args.state else cc)
    command = " ".join(["python -m app.imagery coverage --country", cc] + [f'--state "{st}"' for st in args.state or []]
                       + (["--all-states"] if args.all_states else [])
                       + ([f"--clip={args.clip}"] if args.clip else []) + [f"--buffer-km {args.buffer_km:g}"]
                       + ([f"--max-points {args.max_points}"] if args.max_points != 200 else [])
                       + ([f"--max-parts {args.max_parts}"] if args.max_parts != 40 else []) + [f"--name {name}"])
    feature = {"type": "Feature",
               "properties": {"name": label, "source": credit,
                              "note": f"Buffered {args.buffer_km:g} km{' within ' + args.clip if args.clip else ''} and "
                                      f"simplified to {points} points; generous on purpose.", "command": command},
               "geometry": json.loads(json.dumps(mapping(out)))}
    text = json.dumps(feature, indent=2, ensure_ascii=False)
    text = re.sub(r"\[\s+(-?[\d.]+),\s+(-?[\d.]+)\s+\]", r"[\1, \2]", text) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {path}: {len(parts)} part(s), {points} points; reference it as \"coverage\": {{\"ref\": \"{name}\"}}")
    return 0


# -- covers -------------------------------------------------------------------------------------------------------------
def sources_at(lon: float, lat: float, bundled: Path = CATALOG_DIR) -> list[dict]:
    """Every bundled source whose outline contains (lon, lat), sharpest first. Offline: reads only the catalog."""
    cat = load(bundled, None)
    hits = [c for c in cat.sources.values() if covers(c, lon, lat)]
    return sorted(hits, key=lambda c: (bool(c.get("auth")), c["native_m"]))


def cmd_covers(args) -> int:
    lat, lon = args.lat, args.lon
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        print("LAT LON, in that order: -90..90 and -180..180")
        return 2
    hits = sources_at(lon, lat)
    for c in hits:
        key = f"  needs {c['auth']['label']}" if c.get("auth") else ""
        print(f"{c['id']:<24} {c['native_m'] * 100:>6.1f} cm  {c['region']}  [{c['kind']}]{key}")
    if not any(not c.get("auth") for c in hits):
        print("no free source in the catalog claims this place: see catalog/FINDING.md for where to look")
    return 0


# -- entry point ----------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.imagery", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="check source files")
    v.add_argument("files", nargs="*")
    v.add_argument("--live", action="store_true", help="also fetch from each source at its landmark")
    v.add_argument("--out", help="save each landmark render here")
    v.add_argument("--key", action="append", default=[], metavar="ID=KEY", help="render a keyed source with this key")
    v.add_argument("-v", "--verbose", action="store_true", help="show passed checks too")
    s = sub.add_parser("sheet", help="contact sheet of every keyless source at its landmark")
    s.add_argument("--out", default="out/sheet.png")
    for name in ("table", "schema"):
        t = sub.add_parser(name, help=f"catalog/{'SOURCES.md' if name == 'table' else 'schema.json'}")
        g = t.add_mutually_exclusive_group()
        g.add_argument("--write", action="store_true")
        g.add_argument("--check", action="store_true")
    c = sub.add_parser("coverage", help="an outline file from Natural Earth")
    c.add_argument("--country", required=True, help="ISO 3166-1 alpha-2")
    c.add_argument("--state", action="append", help="a state or province name (repeat for a union of several)")
    c.add_argument("--all-states", action="store_true",
                   help="build from every state or province of the country (finer; keeps small exclaves)")
    c.add_argument("--clip", metavar="W,S,E,N", help="keep only the land inside this lon/lat box")
    c.add_argument("--label", help="the outline's name (default: the country code or the state names)")
    c.add_argument("--name", help="the ref name (default: the country code, or cc_state)")
    c.add_argument("--buffer-km", type=float, default=10)
    c.add_argument("--max-points", type=int, default=200)
    c.add_argument("--max-parts", type=int, default=40)
    c.add_argument("--out")
    c.add_argument("--force", action="store_true")
    w = sub.add_parser("covers", help="which bundled sources claim a place (offline)")
    w.add_argument("lat", type=float)
    w.add_argument("lon", type=float)
    args = ap.parse_args(argv)
    if args.cmd == "covers":
        return cmd_covers(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "sheet":
        return cmd_sheet(args)
    if args.cmd == "table":
        return _write_or_check(CATALOG_DIR / "SOURCES.md", table_text(), args)
    if args.cmd == "schema":
        return _write_or_check(CATALOG_DIR / "schema.json", schema_text(), args)
    return cmd_coverage(args)
