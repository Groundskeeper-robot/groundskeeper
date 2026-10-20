"""The source catalog: one JSON file per source.

* Bundled: catalog/sources/<country>/<id>.json in the repository (contributed by pull request), with shared outlines
  in catalog/coverage/ and the zoomed-out world layer in catalog/context/world.json.
* Local: DATA_DIR/imagery/sources/*.json, written by the panel (a custom source) or by hand. Same format; a local id
  equal to a bundled one replaces it. Outlines may also live in DATA_DIR/imagery/coverage/.

Invalid entries are skipped and reported (Catalog.problems), never fatal. The format is catalog/schema.json, which is
generated from this file and the kinds (`python -m app.imagery schema --write`).
"""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import coverage as cov
from .common import ImageryError
from .kinds import ALIASES, REGISTRY

log = logging.getLogger(__name__)

CATALOG_DIR = Path(__file__).resolve().parents[2] / "catalog"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
REF_RE = re.compile(r"^[a-z0-9_]{2,40}$")
COMMON = ("$schema", "id", "name", "provider", "country", "region", "homepage", "kind", "url", "native_m", "vintage",
          "attribution", "license", "auth", "cache_days", "coverage", "verified")
RUNTIME = ("origin", "file", "coverage_ref")            # added by the loader, never in a file
UNKNOWN_LICENSE = {"id": "unknown", "name": "Not stated", "commercial": None, "credit": True}
WORLD_MAX_ZOOM = 12                                      # a z12 tile is several km across: a city, never a yard


@dataclass
class Catalog:
    sources: dict[str, dict] = field(default_factory=dict)
    world: dict | None = None
    problems: list[str] = field(default_factory=list)


# -- the first catalog's field names ------------------------------------------------------------------------------------
def upgrade(entry: dict) -> dict:
    """An entry in the first catalog's format (terms, needs_key, kind xyz/wmts) in today's. Today's pass through."""
    e = dict(entry)
    if isinstance(e.get("kind"), str) and e["kind"] in ALIASES:
        e["kind"] = ALIASES[e["kind"]]
    if "terms" in e:
        terms = e.pop("terms")
        e.setdefault("license", {"id": "provider-terms", "name": terms, "commercial": None, "credit": True}
                     if terms else dict(UNKNOWN_LICENSE))
    if "needs_key" in e:
        label = e.pop("needs_key")
        e.setdefault("auth", {"label": label} if label else None)
    return e


def _number(value, default, cast, name):
    try:
        return cast(value or default)
    except (TypeError, ValueError):
        raise ImageryError(f"{name} must be a number, not {value!r}") from None


def custom_entry(custom: dict) -> dict:
    """The custom source form (and the first panel's custom-source setting) as a local catalog entry. Raises
    ImageryError for values that are not numbers; everything else is left to validate()."""
    c = upgrade(custom)
    out = {"id": "custom", "name": c.get("name") or "Custom source", "region": "", "kind": c.get("kind"),
           "url": str(c.get("url") or "").rstrip("/"), "native_m": _number(c.get("native_m"), 0.15, float, "native_m"),
           "attribution": c.get("attribution") or "Imagery: custom source",
           "cache_days": _number(c.get("cache_days"), 30, int, "cache_days"),
           "license": dict(UNKNOWN_LICENSE), "coverage": {"type": "World"}}
    kind = REGISTRY.get(out["kind"]) if isinstance(out["kind"], str) else None
    for name in (kind.fields if kind else ()):
        if c.get(name) not in (None, ""):
            out[name] = c[name]
    if out["kind"] == "tiles":
        out.setdefault("max_zoom", 20)
    if kind and "max_px" in kind.fields:
        out.setdefault("max_px", 2048)
    return out


def migrate_v1(data_dir: Path) -> tuple[list[str], list[str]]:
    """DATA_DIR/imagery_catalog.json (the first catalog's local file) becomes files in DATA_DIR/imagery/; the old file
    is renamed, not deleted, and only once everything in it was carried over. Returns (notes, problems)."""
    old = data_dir / "imagery_catalog.json"
    if not old.exists():
        return [], []
    root = data_dir / "imagery"
    (root / "sources").mkdir(parents=True, exist_ok=True)
    notes, problems = [], []
    try:
        data = json.loads(old.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (ValueError, OSError) as exc:
        return [], [f"{old.name}: could not be read to move it to imagery/sources/ ({exc})"]
    for e in data.get("sources") or []:
        sid = e.get("id") if isinstance(e, dict) else None
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            problems.append(f"{old.name}: entry {sid!r} was not moved (an id is 3 to 41 lower-case letters, digits "
                            f"or _); fix it there, or add it to imagery/sources/ by hand")
            continue
        path = root / "sources" / f"{sid}.json"
        if not path.exists():
            write_json(path, upgrade(e))
            notes.append(f"moved local source {sid} to {path}")
    context = data.get("context")
    if isinstance(context, dict) and "world" in context and not (root / "context.json").exists():
        world = context["world"]
        write_json(root / "context.json", {"world": upgrade(world) if isinstance(world, dict) else None})
        notes.append(f"moved the local world layer setting to {root / 'context.json'}")
    if problems:
        return notes, problems + [f"{old.name} is kept until every entry in it has moved"]
    old.rename(old.with_name(old.name + ".migrated-v1"))
    return notes + [f"renamed {old.name} to {old.name}.migrated-v1"], []


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# -- validation ---------------------------------------------------------------------------------------------------------
def _url(v) -> bool:
    return isinstance(v, str) and v.startswith(("http://", "https://"))


def validate(src: dict) -> list[str]:
    """What stops the panel from using an entry (coverage must already be resolved). Empty when usable."""
    p = [f"missing {k}" for k in ("id", "name", "kind", "url", "native_m", "attribution", "coverage") if k not in src]
    if "id" in src and not ID_RE.fullmatch(str(src["id"])):
        p.append("id must be 3 to 41 lower-case letters, digits or _, starting with a letter")
    kind = REGISTRY.get(src["kind"]) if isinstance(src.get("kind"), str) else None
    if "kind" in src and not kind:
        p.append(f"kind must be one of {', '.join(REGISTRY)}")
    if "url" in src and not _url(src["url"]):
        p.append("url must be http(s)")
    if "native_m" in src and (isinstance(src["native_m"], bool) or not isinstance(src["native_m"], (int, float))
                              or not 0 < src["native_m"] <= 1000):
        p.append("native_m must be a number of metres per pixel above 0")
    if not isinstance(src.get("name", ""), str) or not isinstance(src.get("attribution", ""), str):
        p.append("name and attribution must be text")
    c = src.get("coverage")
    if isinstance(c, dict) and c.get("type") == "missing":
        p.append(f"coverage ref {c['ref']!r} has no outline file (coverage/{c['ref']}.geojson)")
    elif isinstance(c, dict) and c.get("type") == "unreadable":
        p.append(f"coverage ref {c['ref']!r} could not be read: {c['error']}")
    elif "coverage" in src:
        p += cov.problems(c)
    if src.get("country") and not re.match(r"^[A-Z]{2}$", str(src["country"])):
        p.append("country must be an ISO 3166-1 alpha-2 code (XX for worldwide)")
    if "homepage" in src and not _url(src["homepage"]):
        p.append("homepage must be http(s)")
    if "cache_days" in src and (isinstance(src["cache_days"], bool) or not isinstance(src["cache_days"], (int, float))
                                or src["cache_days"] < 0):
        p.append("cache_days must be a number of days")
    lic = src.get("license")
    if lic is not None:
        if (not isinstance(lic, dict) or not isinstance(lic.get("name"), str) or lic.get("commercial") not in (True, False, None)
                or not isinstance(lic.get("credit", True), bool)):
            p.append("license needs a name, commercial (true, false or null) and credit (true or false)")
        elif "url" in lic and not _url(lic["url"]):
            p.append("license.url must be http(s)")
    auth = src.get("auth")
    if auth is not None:
        if not isinstance(auth, dict) or not isinstance(auth.get("label"), str):
            p.append("auth needs a label (what key the user must supply)")
        else:
            h = auth.get("header")
            if h is not None and (not isinstance(h, dict) or not h.get("name") or "{key}" not in str(h.get("format", ""))):
                p.append("auth.header needs a name and a format containing {key}")
            if "signup" in auth and not _url(auth["signup"]):
                p.append("auth.signup must be http(s)")
    ver = src.get("verified")
    if ver is not None:
        lm = ver.get("landmark") if isinstance(ver, dict) else None
        if (not isinstance(lm, list) or len(lm) != 2 or not all(isinstance(v, (int, float)) for v in lm)
                or not (-180 <= lm[0] <= 180 and -90 <= lm[1] <= 90)):
            p.append("verified.landmark must be [longitude, latitude]")
    if kind:
        p += kind.validate(src)
    return p


BUNDLED_REQUIRED = ("id", "name", "country", "region", "kind", "url", "native_m", "attribution", "license", "coverage",
                    "verified")


def bundled_problems(src: dict) -> list[str]:
    """validate(), plus what a shared catalog file must hold that a local one need not (the schema's required)."""
    p = validate(src)
    p += [f"missing {k}" for k in BUNDLED_REQUIRED if not src.get(k) and f"missing {k}" not in p]
    lic = src.get("license") if isinstance(src.get("license"), dict) else {}
    p += [f"license needs {k}" for k in ("id", "name", "commercial", "credit") if k not in lic]
    ver = src.get("verified") if isinstance(src.get("verified"), dict) else {}
    p += [f"verified needs {k}" for k in ("date", "place", "landmark") if k not in ver]
    return p


def clean(entry: dict) -> dict:
    """Only the fields a source file may hold (the common ones and its kind's), without empty values."""
    kind = REGISTRY.get(entry.get("kind")) if isinstance(entry.get("kind"), str) else None
    allowed = (set(COMMON) - {"$schema"}) | set(kind.fields if kind else ())
    return {k: v for k, v in entry.items() if k in allowed and v not in (None, "")}


def unknown_keys(src: dict) -> list[str]:
    kind = REGISTRY.get(src["kind"]) if isinstance(src.get("kind"), str) else None
    known = set(COMMON) | set(RUNTIME) | set(kind.fields if kind else ())
    return sorted(k for k in src if k not in known)


# -- loading ------------------------------------------------------------------------------------------------------------
def _read(path: Path, problems: list[str]) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        problems.append(f"{path.name}: not readable JSON ({exc})")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        problems.append(f"{path.name}: not a source (no id)")
        return None
    return data


def resolve_coverage(src: dict, outline_dirs: list[Path]) -> dict:
    """Replace coverage {"ref": name} with the outline it names (first found in outline_dirs)."""
    c = src.get("coverage")
    if isinstance(c, dict) and "ref" in c:
        ref = str(c["ref"])
        src = {**src, "coverage_ref": ref}
        path = next((d / f"{ref}.geojson" for d in outline_dirs if REF_RE.fullmatch(ref) and (d / f"{ref}.geojson").exists()), None)
        if path is None:
            return {**src, "coverage": {"type": "missing", "ref": ref}}
        try:
            return {**src, "coverage": cov.read_outline(path)}
        except Exception as exc:                 # any malformed outline file: reported, never fatal
            return {**src, "coverage": {"type": "unreadable", "ref": ref, "error": str(exc)}}
    return src


def prepare(entry: dict, outline_dirs: list[Path]) -> dict:
    """An entry as the loader would use it: outline resolved, defaults filled (for checking a single file)."""
    return _finish(resolve_coverage(entry, outline_dirs))


def _finish(src: dict) -> dict:
    src = dict(src)
    src.setdefault("license", dict(UNKNOWN_LICENSE))
    src.setdefault("cache_days", 30)
    src.setdefault("region", "")
    src.setdefault("country", "")
    return src


def load(bundled: Path = CATALOG_DIR, local_root: Path | None = None) -> Catalog:
    """Bundled sources, then local ones (adding, or replacing by id); invalid entries skipped and reported."""
    cat = Catalog()
    outline_dirs = ([local_root / "coverage"] if local_root else []) + [bundled / "coverage"]
    found: list[tuple[str, Path, dict]] = []
    for path in sorted((bundled / "sources").rglob("*.json")):
        if not path.name.startswith("_"):
            data = _read(path, cat.problems)
            if data:
                found.append(("bundled", path, data))
    bundled_ids = {d["id"] for _, _, d in found}
    if local_root and (local_root / "sources").is_dir():
        for path in sorted((local_root / "sources").glob("*.json")):
            data = _read(path, cat.problems)
            if data:
                data = upgrade(data)
                found.append(("override" if data["id"] in bundled_ids else "local", path, data))
    local_ids = {d["id"] for origin, _, d in found if origin != "bundled"}
    ordered = [f for f in found if f[0] != "bundled" or f[2]["id"] not in local_ids]
    entries = {}
    for origin, path, data in ordered:
        if data["id"] in entries:
            cat.problems.append(f"{path.name}: id {data['id']} is used twice; the first one wins")
            continue
        entries[data["id"]] = resolve_coverage({**data, "origin": origin, "file": str(path)}, outline_dirs)
    for sid, e in entries.items():           # the first catalog's coverage_from: borrow another entry's outline
        if "coverage_from" in e:
            other = entries.get(e.pop("coverage_from")) or {}
            if "coverage" not in e and "coverage" in other:
                e["coverage"] = other["coverage"]
    for sid, e in entries.items():
        e = _finish(e)
        problems = validate(e)
        if problems:
            cat.problems.append(f"{Path(e['file']).name}: skipped: {'; '.join(problems)}")
            continue
        extra = unknown_keys(e)
        if extra:
            log.warning("imagery: %s: ignoring unknown fields %s", Path(e["file"]).name, ", ".join(extra))
        cat.sources[sid] = e
    cat.world = _load_world(bundled, local_root, outline_dirs, cat.problems)
    for p in cat.problems:
        log.warning("imagery catalog: %s", p)
    return cat


def _load_world(bundled: Path, local_root: Path | None, outline_dirs: list[Path], problems: list[str]) -> dict | None:
    """The zoomed-out worldwide layer: bundled, or DATA_DIR/imagery/context.json's ("world": null removes it)."""
    path = bundled / "context" / "world.json"
    world = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    local = local_root / "context.json" if local_root else None
    if local and local.exists():
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "world" in data:
                world, path = (upgrade(data["world"]) if isinstance(data["world"], dict) else None), local
        except (ValueError, OSError) as exc:
            problems.append(f"{local.name}: not readable JSON ({exc})")
    if not world:
        return None
    world = _finish(resolve_coverage({"cache_days": 365, "max_zoom": 12, **world, "file": str(path)}, outline_dirs))
    p = validate(world)
    if world.get("kind") != "tiles":
        p.append("the world layer must be a tiles source")
    if world.get("auth"):
        p.append("the world layer cannot need a key")
    if isinstance(world.get("max_zoom"), int) and world["max_zoom"] > WORLD_MAX_ZOOM:
        problems.append(f"{path.name}: the world layer's max_zoom is capped at {WORLD_MAX_ZOOM}, so it never sees a yard")
        world["max_zoom"] = WORLD_MAX_ZOOM
    if p:
        problems.append(f"{path.name}: world layer skipped: {'; '.join(p)}")
        return None
    return world


# -- places, for finding sources by state -------------------------------------------------------------------------------
def place_of(src: dict) -> str:
    """The state or province a source's region names: the part after its last comma ("Ada County, Idaho" is Idaho)."""
    return str(src.get("region") or "").rsplit(",", 1)[-1].strip()


def served_places(sources: list[dict]) -> dict[str, list[str]]:
    """{id: [state or province, ...]}: where each source serves, by name, so the catalog can be searched by state.
    A source serves the place its region names. One whose outline holds the landmarks of three or more places of its
    country is country-wide and serves those places instead (USGS NAIP: the lower 48 and DC, not Alaska or Hawaii).
    Worldwide sources are left out: they serve everywhere. Offline: reads only the outlines."""
    by_country: dict[str, list[dict]] = {}
    for s in sources:
        if s["coverage"]["type"] != "World" and place_of(s):
            by_country.setdefault(s.get("country") or "", []).append(s)
    out: dict[str, list[str]] = {}
    for group in by_country.values():
        marks: dict[str, list[float]] = {}           # one landmark per place, a whole-place source's when there is one
        for s in sorted(group, key=lambda s: "," in str(s.get("region"))):
            lm = (s.get("verified") or {}).get("landmark")
            if isinstance(lm, list) and len(lm) == 2:
                marks.setdefault(place_of(s), lm)
        hits = {}
        for s in group:
            w, so, e, n = cov.bounds(s["coverage"])
            hits[s["id"]] = [p for p, (lon, lat) in marks.items()
                             if p != place_of(s) and w <= lon <= e and so <= lat <= n and cov.covers(s, lon, lat)]
        wide = {sid for sid, h in hits.items() if len(h) >= 3}
        real = {place_of(s) for s in group if s["id"] not in wide}
        for s in group:
            out[s["id"]] = sorted(p for p in hits[s["id"]] if p in real) if s["id"] in wide else [place_of(s)]
    return out


# -- the schema ---------------------------------------------------------------------------------------------------------
def schema_doc() -> dict:
    """catalog/schema.json: what a bundled source file must hold. Generated; the kinds' own fields come from them."""
    url = {"type": "string", "pattern": "^https?://"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Groundskeeper imagery source",
        "description": "One source in the imagery catalog: catalog/sources/<country>/<id>.json. Generated by "
                       "`python -m app.imagery schema --write`; see catalog/README.md.",
        "type": "object",
        "required": ["id", "name", "country", "region", "kind", "url", "native_m", "attribution", "license",
                     "coverage", "verified"],
        "properties": {
            "$schema": {"type": "string"},
            "id": {"type": "string", "pattern": ID_RE.pattern,
                   "description": "Unique, lower case, prefixed with the country code (nl_pdok_hr). The file is named after it."},
            "name": {"type": "string", "minLength": 3, "maxLength": 80,
                     "description": "The source's own name, without the country (the panel shows region and country)."},
            "provider": {"type": "string", "description": "Who publishes it."},
            "country": {"type": "string", "pattern": "^[A-Z]{2}$",
                        "description": "ISO 3166-1 alpha-2; XX for a worldwide service. The folder is its lower case (world for XX)."},
            "region": {"type": "string", "description": "What a user reads: Netherlands, Maryland, New South Wales."},
            "homepage": {**url, "description": "Where its terms and, for keyed sources, its keys are."},
            "kind": {"enum": list(REGISTRY), "description": "The protocol: see the kinds in app/imagery/kinds/."},
            "url": {**url, "description": "The service address. {key} is replaced by the user's key."},
            "native_m": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000,
                         "description": "Ground metres per pixel at full resolution."},
            "vintage": {"type": "string", "description": "Season and date, as far as known: \"Leaf-off spring flights, 2024\"."},
            "attribution": {"type": "string", "minLength": 3, "description": "The credit line shown on the map."},
            "license": {"$ref": "#/$defs/license"},
            "auth": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/auth"}]},
            "cache_days": {"type": "number", "minimum": 0,
                           "description": "How long a fetched image may be kept; 1 for services whose terms limit caching."},
            "coverage": {"$ref": "#/$defs/coverage"},
            "verified": {"$ref": "#/$defs/verified"},
        },
        "allOf": [_kind_rule(name, kind) for name, kind in REGISTRY.items()],
        "unevaluatedProperties": False,
        "$defs": {
            "license": {
                "type": "object", "required": ["id", "name", "commercial", "credit"], "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "description": "An SPDX id where one exists (CC-BY-4.0), public-domain, or provider-terms."},
                    "name": {"type": "string", "description": "One line a user reads."},
                    "url": url,
                    "commercial": {"type": ["boolean", "null"], "description": "May a commercial deployment use it? null: not known."},
                    "credit": {"type": "boolean", "description": "Must the attribution be shown?"},
                },
            },
            "auth": {
                "type": "object", "required": ["label"], "additionalProperties": False,
                "properties": {
                    "label": {"type": "string", "description": "What the user must supply: \"a Mapbox access token\"."},
                    "signup": {**url, "description": "Where to get one."},
                    "header": {"type": "object", "required": ["name", "format"], "additionalProperties": False,
                               "description": "Send the key in a header instead of the url.",
                               "properties": {"name": {"type": "string"},
                                              "format": {"type": "string", "pattern": "\\{key\\}"}}},
                },
            },
            "coverage": {
                "description": "Where it has imagery; generous on purpose. The panel never contacts a source whose "
                               "coverage does not contain the yard.",
                "oneOf": [
                    {"type": "object", "required": ["type"], "additionalProperties": False,
                     "properties": {"type": {"const": "World"}}},
                    {"type": "object", "required": ["ref"], "additionalProperties": False,
                     "properties": {"ref": {"type": "string", "pattern": REF_RE.pattern,
                                            "description": "An outline file: catalog/coverage/<ref>.geojson."}}},
                    {"type": "object", "required": ["type", "coordinates"],
                     "properties": {"type": {"enum": ["Polygon", "MultiPolygon"]}, "coordinates": {"type": "array", "minItems": 1}}},
                ],
            },
            "verified": {
                "type": "object", "required": ["date", "place", "landmark"], "additionalProperties": False,
                "description": "The public landmark the contributor checked the source at (never a home).",
                "properties": {
                    "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                    "place": {"type": "string"},
                    "landmark": {"type": "array", "minItems": 2, "maxItems": 2,
                                 "prefixItems": [{"type": "number", "minimum": -180, "maximum": 180},
                                                 {"type": "number", "minimum": -90, "maximum": 90}]},
                    "note": {"type": "string"},
                },
            },
        },
    }


def _kind_rule(name: str, kind) -> dict:
    then = {"properties": {n: f.schema() for n, f in kind.fields.items()}}
    if kind.url_pattern:
        then["properties"]["url"] = {"pattern": kind.url_pattern, "description": kind.url_rule}
    required = [n for n, f in kind.fields.items() if f.required]
    if required:
        then["required"] = required
    return {"if": {"properties": {"kind": {"const": name}}, "required": ["kind"]}, "then": then}
