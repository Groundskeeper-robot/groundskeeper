"""What a kind is. A kind turns a catalog entry into requests; the service decides whether to make them.

A kind never decides privacy (which sources may be contacted for a yard): the service does that before calling it.
"""
import asyncio
import math
import re
from dataclasses import dataclass

from PIL import Image

from ..common import ImageryError, get_image, get_image_or_none
from ..mercator import HALF, chunks, from_merc, tile_bounds, tile_range, tile_zoom

Box = tuple[float, float, float, float]       # EPSG:3857 x0, y0, x1, y1


@dataclass(frozen=True)
class Field:
    """A setting a kind reads from a catalog entry. The schema (catalog/schema.json), the runtime validator and the
    add-a-source form are all generated from these, so they cannot disagree."""
    type: str                        # "string" | "integer" | "number"
    help: str
    required: bool = False
    choices: tuple = ()
    default: object = None
    minimum: float | None = None
    maximum: float | None = None
    label: str = ""                  # what a form calls it (default: the field's name)

    def problem(self, name: str, value) -> str | None:
        if self.type == "string" and not isinstance(value, str):
            return f"{name} must be text"
        if self.type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{name} must be a whole number"
        if self.type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            return f"{name} must be a number"
        if self.choices and value not in self.choices:
            return f"{name} must be one of {', '.join(map(str, self.choices))}"
        if self.minimum is not None and value < self.minimum:
            return f"{name} must be at least {self.minimum}"
        if self.maximum is not None and value > self.maximum:
            return f"{name} must be at most {self.maximum}"
        return None

    def schema(self) -> dict:
        out = {"type": self.type, "description": self.help, **({"title": self.label} if self.label else {})}
        if self.choices:
            out["enum"] = list(self.choices)
        if self.default is not None:
            out["default"] = self.default
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        return out


class Kind:
    """One protocol. Subclass ExportKind (a server renders any box) or TileKind (pre-cut tiles), declare `fields`,
    and register an instance in kinds/__init__.py."""
    name = ""
    label = ""                       # shown in the add-a-source form
    url_hint = ""
    url_pattern: str | None = None   # a regular expression the url must match (also written into the schema)
    url_rule = ""                    # what to tell someone whose url does not match
    fields: dict[str, Field] = {}

    def opt(self, src: dict, name: str):
        value = src.get(name)
        return self.fields[name].default if value is None else value

    def validate(self, src: dict) -> list[str]:
        out = []
        for name, f in self.fields.items():
            value = src.get(name)
            if value is None or (value == "" and f.required):
                if f.required:
                    out.append(f"{self.name} needs {name}")
                continue
            p = f.problem(name, value)
            if p:
                out.append(p)
        if self.url_pattern and not re.search(self.url_pattern, str(src.get("url", ""))):
            out.append(self.url_rule)
        return out

    def headers(self, src: dict, key: str) -> dict:
        """auth.header: send the key in a header ({"name": "Authorization", "format": "Bearer {key}"})."""
        h = (src.get("auth") or {}).get("header")
        return {h["name"]: h["format"].replace("{key}", key)} if h and key else {}

    def max_zoom(self, src: dict, lat: float) -> int:
        """How far in the map may draw this source as tiles."""
        raise NotImplementedError

    async def fetch_box(self, http, src: dict, key: str, box: Box, width: int, height: int) -> Image.Image:
        """Exactly this Mercator box at width x height pixels."""
        raise NotImplementedError

    async def fetch_tile(self, http, src: dict, key: str, z: int, x: int, y: int) -> Image.Image | None:
        """One Web Mercator tile (256 px or larger), or None where the service has none."""
        raise NotImplementedError

    def claims(self, url: str) -> bool:
        """Does the address look like this protocol? Decided from the address alone, with no request; it only sets
        the order discovery asks the kinds in."""
        return False

    async def discover(self, http, url: str, lat: float | None = None) -> dict | None:
        """Ask the address what it serves and draft a catalog entry. Contacts only that address, never with a
        location (lat, the yard's latitude, may be used here to convert Web Mercator sizes, and is never sent). Returns {"entry", "notes", "guessed", "choices", "extent"} (see discover.py), or None when the
        address is not this kind; raises ImageryError when it is but cannot be used."""
        return None

    def describe(self) -> dict:
        return {"name": self.name, "label": self.label, "url_hint": self.url_hint,
                "fields": {n: {"type": f.type, "label": f.label or n.replace("_", " "), "required": f.required,
                               "choices": list(f.choices), "default": f.default, "help": f.help}
                           for n, f in self.fields.items()}}


class ExportKind(Kind):
    """A server that renders any box on request (ArcGIS export, WMS GetMap). Boxes bigger than the server allows
    (`max_px`) are fetched in pieces and joined."""

    def request(self, src: dict, key: str, box: Box, width: int, height: int) -> tuple[str, dict, dict]:
        """(url, query parameters, headers) for one image of the box."""
        raise NotImplementedError

    async def fetch_one(self, http, src: dict, key: str, box: Box, width: int, height: int) -> Image.Image:
        url, params, headers = self.request(src, key, box, width, height)
        return await get_image(http, url, params, headers, src["name"])

    async def fetch_box(self, http, src, key, box, width, height):
        x0, y0, x1, y1 = box
        canvas = Image.new("RGB", (width, height))
        for left, top, w, h in chunks(width, height, int(self.opt(src, "max_px"))):
            cx0 = x0 + (x1 - x0) * left / width
            cx1 = x0 + (x1 - x0) * (left + w) / width
            cy1 = y1 - (y1 - y0) * top / height
            cy0 = y1 - (y1 - y0) * (top + h) / height
            canvas.paste(await self.fetch_one(http, src, key, (cx0, cy0, cx1, cy1), w, h), (left, top))
        return canvas

    async def fetch_tile(self, http, src, key, z, x, y):
        return await self.fetch_one(http, src, key, tile_bounds(z, x, y), 256, 256)

    def max_zoom(self, src, lat):
        return tile_zoom(src["native_m"], lat, 256, 22)


class TileKind(Kind):
    """Pre-cut tiles in the Web Mercator grid (GoogleMapsCompatible): boxes are stitched from them."""
    MAX_TILES = 400

    def request(self, src: dict, key: str, z: int, x: int, y: int) -> tuple[str, dict]:
        """(url, headers) for one tile."""
        raise NotImplementedError

    def tile_px(self, src: dict) -> int:
        return 256

    async def fetch_tile(self, http, src, key, z, x, y):
        url, headers = self.request(src, key, z, x, y)
        return await get_image_or_none(http, url, headers)

    def zoom_for(self, src: dict, box: Box, width: int) -> int:
        """The zoom whose tiles are at least as sharp as the box needs at width pixels."""
        x0, y0, x1, y1 = box
        lat = from_merc((x0 + x1) / 2, (y0 + y1) / 2)[1]
        mpp = (x1 - x0) / width * math.cos(math.radians(lat))      # Mercator metres to ground metres
        return tile_zoom(mpp, lat, self.tile_px(src), self.max_zoom(src, lat))

    async def fetch_box(self, http, src, key, box, width, height):
        x0, y0, x1, y1 = box
        z = self.zoom_for(src, box, width)
        tx0, ty0, tx1, ty1 = tile_range(x0, y0, x1, y1, z)
        count = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
        if count > self.MAX_TILES:
            raise ImageryError(f"{count} tiles is too many for one image; lower the resolution")
        gate = asyncio.Semaphore(8)

        async def one(tx, ty):
            async with gate:
                return tx, ty, await self.fetch_tile(http, src, key, z, tx, ty)

        got = await asyncio.gather(*(one(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)))
        size = next((im.width for _, _, im in got if im is not None), None)
        if size is None:
            raise ImageryError(f"{src['name']} returned no tiles here")
        canvas = Image.new("RGB", ((tx1 - tx0 + 1) * size, (ty1 - ty0 + 1) * size))
        for tx, ty, im in got:
            if im is not None:
                canvas.paste(im.resize((size, size)) if im.width != size else im, ((tx - tx0) * size, (ty - ty0) * size))
        span = 2 * HALF / 2 ** z
        left = ((x0 + HALF) / span - tx0) * size
        top = ((HALF - y1) / span - ty0) * size
        right = ((x1 + HALF) / span - tx0) * size
        bottom = ((HALF - y0) / span - ty0) * size
        return canvas.crop((round(left), round(top), round(right), round(bottom))).resize((width, height), Image.LANCZOS)
