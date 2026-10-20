"""Turning a pasted address into a draft catalog entry (the add-a-source flow).

Each kind knows its own protocol (Kind.claims, Kind.discover); this module holds what they share and the dispatcher
that asks them in turn. Discovery contacts only the pasted address (its capabilities, its own extent, perhaps one
world-scale tile), never sends a location, and refuses redirects to another host. Query parameters that look like a
place (a centre, a box, a lat/lon pair) are dropped from what it sends, in case the address was copied from a map
showing the yard. The first request that carries the yard is the "try" step, which the user starts on purpose.

A discovery result: {"entry": draft entry (no id; coverage World), "kind_label": str, "notes": [what the user should
know], "guessed": [fields that are guesses], "choices": {"layers": [{"name", "title", ...}]}, "extent": [w, s, e, n]
or None}.
"""
import asyncio
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from . import kinds
from .common import ImageryError, host

MAX_BYTES = 5_000_000
TIMEOUT_S = 20
YEAR_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
PLACE_PARAMS = {"bbox", "lat", "lon", "lng", "latitude", "longitude", "center", "centre", "ll", "point", "x", "y", "z",
                "zoom", "extent", "marker", "q", "query", "search", "address", "location", "loc", "pos", "position",
                "geometry", "coords", "coordinates", "mapextent", "view"}
PAIR = re.compile(r"-?\d{1,3}\.\d+\s*[,;/ ]\s*-?\d{1,3}\.\d+")


class Fetched:
    """A capped response: status, headers, at most MAX_BYTES of body."""

    def __init__(self, status_code: int, headers, content: bytes):
        self.status_code, self.headers, self.content = status_code, headers, content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content)


def check_url(url) -> str:
    url = str(url or "").strip()
    if not re.match(r"^https?://[^/\s]+", url):
        raise ImageryError("paste a full address starting with http:// or https://")
    if "@" in urlsplit(url).netloc:
        raise ImageryError("addresses with a user name and password are not supported; a key in the address "
                           "(token=…) is")
    return url


def scrub_query(url: str) -> str:
    """The address without query parameters that look like a place."""
    parts = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if "{" in v or (k.lower() not in PLACE_PARAMS and not PAIR.search(v))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep, safe="{}:/"), ""))


async def same_host_only(response) -> None:
    """An httpx response hook: refuse a redirect to another host (the address the user pasted is the only one the
    flow may contact, and the try step carries the yard)."""
    if response.has_redirect_location:
        target = urlsplit(urljoin(str(response.request.url), response.headers["location"])).hostname
        if target != response.request.url.host:
            raise ImageryError(f"{response.request.url.host} redirected to {target}; the panel only follows "
                               f"redirects within the address you gave")


async def cap_size(response) -> None:
    """An httpx response hook: refuse an answer that says it is larger than the add-a-source flow ever needs."""
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > 50_000_000:
        raise ImageryError(f"{response.request.url.host} answered with {int(length) // 1_000_000} MB")


async def get(http, url: str, params: dict | None = None) -> Fetched:
    """GET with a byte cap and a time limit for the whole request (a pasted download link must not fill memory)."""
    async def fetch():
        async with http.stream("GET", url, params=params, timeout=TIMEOUT_S) as r:
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ImageryError(f"{host(url)} answered with more than {MAX_BYTES // 1_000_000} MB")
            return Fetched(r.status_code, r.headers, bytes(body))
    try:
        return await asyncio.wait_for(fetch(), TIMEOUT_S * 1.5)
    except ImageryError:
        raise
    except asyncio.TimeoutError:
        raise ImageryError(f"{host(url)} took longer than {TIMEOUT_S * 1.5:g} s") from None
    except Exception as exc:
        raise ImageryError(f"{host(url)} did not answer: {type(exc).__name__}") from None


async def get_json(http, url: str, params: dict | None = None) -> dict:
    r = await get(http, url, params)
    if r.status_code >= 400:
        raise ImageryError(f"{host(url)} answered HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise ImageryError(f"{host(url)} did not answer with JSON") from None
    if not isinstance(data, dict):
        raise ImageryError(f"{host(url)} answered with something other than a JSON object")
    return data


def guess_year(*texts) -> str | None:
    years = [y for t in texts for y in YEAR_RE.findall(str(t or ""))]
    return f"{max(years)}" if years else None


def guess_license(*texts) -> dict:
    """Only what the service's own words make clear; everything else is "not stated" for the user to set."""
    t = " ".join(str(x or "") for x in texts).lower()
    if re.search(r"\bpublic domain\b", t) and not re.search(r"\bnot\b[^.]{0,30}public domain", t):
        return {"id": "public-domain", "name": "Public domain", "commercial": True, "credit": False}
    if re.search(r"\bcc[ -]?by[ -]?4\.0\b|creative commons attribution 4", t):
        return {"id": "CC-BY-4.0", "name": "Open data (CC BY 4.0); credit required",
                "url": "https://creativecommons.org/licenses/by/4.0/", "commercial": True, "credit": True}
    return {"id": "unknown", "name": "Not stated", "commercial": None, "credit": True}


def pretty(name: str) -> str:
    name = re.sub(r"[_]+", " ", str(name or "")).strip()
    return name[:1].upper() + name[1:] if name else ""


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def suggest_id(name: str, taken: set[str], prefix: str = "") -> str:
    base = slug(f"{prefix} {name}") or "my_source"
    if not base[0].isalpha():
        base = f"src_{base}"
    base = base[:34].rstrip("_")
    sid, n = base if len(base) >= 3 else f"{base}_src", 2
    while sid in taken:
        sid, n = f"{base}_{n}", n + 1
    return sid


def result(entry: dict, notes=None, guessed=None, choices=None, extent=None) -> dict:
    entry = {k: v for k, v in entry.items() if v not in (None, "")}
    entry.setdefault("coverage", {"type": "World"})
    return {"entry": entry, "kind_label": kinds.get(entry["kind"]).label, "notes": notes or [],
            "guessed": guessed or [], "choices": choices or {}, "extent": extent}


async def discover(http, url: str, lat: float | None = None) -> dict:
    """Ask every kind the address looks like; if none does, the others in turn (WMS GetCapabilities can answer at a
    bare address). Raises ImageryError with what each one found. lat (the yard's latitude) is only used locally."""
    url = check_url(url)
    claiming = [k for k in kinds.REGISTRY.values() if k.claims(url)]
    errors = []
    for kind in claiming or list(kinds.REGISTRY.values()):
        try:
            found = await kind.discover(http, url, lat)
        except ImageryError as exc:
            errors.append(f"{kind.label}: {exc}")
            continue
        except Exception as exc:                 # a malformed answer is that kind's failure, not the request's
            errors.append(f"{kind.label}: could not read its answer ({type(exc).__name__})")
            continue
        if found:
            return found
    raise ImageryError("could not tell what this address serves" + (f" ({'; '.join(errors)})" if errors else
                       ": expected an ArcGIS ImageServer or MapServer, a WMS, or a tile address with {z}, {x} and {y}"))
