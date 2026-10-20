"""What the kinds, the service and discovery share: errors, blank and upsample detection, fetching one image, and the
rules for keys and queries in addresses."""
import io
import re
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from PIL import Image, ImageChops, ImageStat


class ImageryError(Exception):
    pass


class NoImagery(ImageryError):
    """The service answered, with a blank image: it has no imagery there (not a passing failure)."""


# Query parameters that carry a credential. Exported files replace their values with {key}.
KEY_NAMES = ("token", "key", "apikey", "api_key", "api-key", "api", "access_token", "accesstoken", "access_key",
             "authkey", "auth", "subscription-key", "sig", "signature", "client_secret", "secret", "password",
             "passwd", "pwd", "x-api-key")


def host(url: str) -> str:
    """The host (and port) of an address, without any user name or password."""
    parts = urlsplit(url)
    return (parts.hostname or url) + (f":{parts.port}" if parts.port else "")


def merge_query(url: str, params: dict, key: str | None = None) -> tuple[str, dict]:
    """(the address without its query, its query merged under params). httpx replaces an address's own query when
    params are given, which silently dropped map=, token= and the like. {key} is filled in after the query is read,
    so a key with & or = in it stays one value."""
    parts = urlsplit(url)
    own = dict(parse_qsl(parts.query, keep_blank_values=True))
    path = parts.path
    if key is not None:
        own = {k: v.replace("{key}", key) for k, v in own.items()}
        path = path.replace("{key}", quote(key, safe=""))
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")), {**own, **params}


def is_blank(img: Image.Image) -> bool:
    """No imagery: a flat fill (NoData black, white, or a single colour). An ArcGIS export with no coverage answers
    HTTP 200 with a black JPEG, so this is the only way to tell."""
    stat = ImageStat.Stat(img.convert("L"))
    return stat.stddev[0] < 4 or stat.mean[0] < 3 or stat.mean[0] > 252


def _mean_diff(a: Image.Image, b: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def added_detail(tile: Image.Image, parent: Image.Image, qx: int, qy: int) -> float:
    """How much a tile adds over the matching quarter (qx, qy) of its parent tile one zoom out, enlarged, relative to
    the detail that quarter carries itself. About 1 for real imagery; a tile a service made by enlarging its parent
    (overzooming past its data) adds almost nothing. Measured on 15 catalog sources (2026-10-04): real 0.68-1.17,
    enlarged 0.10-0.24, both after JPEG. An absolute measure does not work: the two ranges overlap."""
    g, p = tile.convert("L"), parent.convert("L")
    half = p.width // 2
    q = p.crop((qx * half, qy * half, qx * half + half, qy * half + half))
    q_detail = _mean_diff(q, q.resize((max(1, half // 2),) * 2, Image.BOX).resize(q.size, Image.BILINEAR))
    return _mean_diff(g, q.resize(g.size, Image.BILINEAR)) / max(q_detail, 0.5)


OVERZOOMED = 0.4


def _fail(r, what: str) -> None:
    if r.status_code >= 400:          # the message names the host, never the address (it holds the box and any key)
        raise ImageryError(f"{what or host(str(getattr(r, 'url', '')))} answered HTTP {r.status_code}")


_MAGIC = (bytes.fromhex("ffd8ff"), bytes.fromhex("89504e470d0a1a0a"), b"GIF8")


def is_image(r) -> bool:
    """An image response: by its content type, or, for servers that label tiles application/octet-stream (MassGIS),
    by the file's own first bytes (JPEG, PNG, GIF, WebP)."""
    ctype = r.headers.get("content-type", "")
    if ctype.startswith("image/"):
        return True
    head = r.content[:12]
    return ctype.split(";")[0].strip() in ("", "application/octet-stream", "binary/octet-stream") and (
        head.startswith(_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


async def get_image(http, url: str, params: dict | None = None, headers: dict | None = None,
                    what: str = "") -> Image.Image:
    """GET an image; anything that is not one (an XML exception, an HTML error page) is an ImageryError."""
    r = await http.get(url, params=params, headers=headers or None)
    _fail(r, what or host(url))
    ctype = r.headers.get("content-type", "")
    if not is_image(r):
        text = re.sub(r"https?://\S+", "…", r.text[:160])
        raise ImageryError(f"{what or host(url)} answered {ctype or 'no content type'}: {text}")
    return Image.open(io.BytesIO(r.content)).convert("RGB")


async def get_image_or_none(http, url: str, headers: dict | None = None) -> Image.Image | None:
    """A tile: missing (404, 204, an error page) is normal at the edges of a service, so it is None, not an error."""
    r = await http.get(url, headers=headers or None)
    if r.status_code != 200 or not is_image(r):
        return None
    return Image.open(io.BytesIO(r.content)).convert("RGB")
