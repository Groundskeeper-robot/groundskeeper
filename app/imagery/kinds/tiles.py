import io
import re
from urllib.parse import quote, urlsplit

from PIL import Image

from ..common import ImageryError, is_image
from .base import Field, TileKind

KNOWN = re.compile(r"\{(?:z|x|y|-y|key)\}")


class Tiles(TileKind):
    """A tile template: XYZ, a WMTS REST/KVP address in the GoogleMapsCompatible grid, or TMS."""
    name = "tiles"
    label = "Tiles ({z}/{x}/{y} template)"
    url_hint = "https://…/{z}/{x}/{y}.jpg"
    url_pattern = r"^(?=.*\{z\})(?=.*\{x\})(?=.*\{-?y\})"
    url_rule = "a tiles url needs {z}, {x} and {y} (or {-y} for a flipped row)"
    fields = {
        "max_zoom": Field("integer", "The deepest zoom level the service has tiles for.", default=19, minimum=0, maximum=24, label="Deepest zoom"),
        "scheme": Field("string", "xyz: row 0 at the top (Google, OpenStreetMap, WMTS); tms: row 0 at the bottom.",
                        choices=("xyz", "tms"), default="xyz", label="Row order"),
        "tile_size": Field("integer", "Pixels per tile side: 256, or 512 for @2x tiles.", choices=(256, 512), default=256, label="Tile size (px)"),
    }

    def max_zoom(self, src, lat):
        return int(self.opt(src, "max_zoom"))

    def tile_px(self, src):
        return int(self.opt(src, "tile_size"))

    def request(self, src, key, z, x, y):
        flipped = 2 ** z - 1 - y
        row = flipped if self.opt(src, "scheme") == "tms" else y
        url = src["url"]
        for token, value in (("{z}", z), ("{x}", x), ("{-y}", flipped), ("{y}", row), ("{key}", quote(key or "", safe=""))):
            url = url.replace(token, str(value))
        return url, self.headers(src, key)

    @staticmethod
    def template(url: str) -> tuple[str | None, str]:
        """(a {z}/{x}/{y} template, how it was read) from a pasted tile address, or (None, "")."""
        if "{z}" in url and "{x}" in url and ("{y}" in url or "{-y}" in url):
            return url, "template"
        if all(re.search(rf"(?i)[?&]{k}=", url) for k in ("TileMatrix", "TileRow", "TileCol")):
            t = re.sub(r"(?i)([?&]TileMatrix=(?:[^&]*?:)?)\d+(?=&|$)", r"\1{z}", url)
            t = re.sub(r"(?i)([?&]TileRow=)\d+", r"\1{y}", t)
            t = re.sub(r"(?i)([?&]TileCol=)\d+", r"\1{x}", t)
            if "{z}" in t and "{x}" in t and "{y}" in t:
                return t, "wmts"
        m = re.search(r"(?i)/tile/(\d{1,2})/(\d+)/(\d+)(?=$|[/?.@#])", url)
        if m:
            return url[:m.start()] + "/tile/{z}/{y}/{x}" + url[m.end():], "arcgis cache"
        m = re.search(r"/(\d{1,2})/(\d+)/(\d+)(?=$|[@./?#])", url)
        if m:
            if "wmts" in urlsplit(url).path.lower():     # WMTS REST: TileMatrix / TileRow / TileCol
                return url[:m.start()] + "/{z}/{y}/{x}" + url[m.end():], "wmts rest"
            return url[:m.start()] + "/{z}/{x}/{y}" + url[m.end():], "numbers"
        return None, ""

    def claims(self, url):
        return self.template(url)[0] is not None

    async def discover(self, http, url, lat=None):
        from ..discover import get, guess_license, host, pretty, result, scrub_query
        template, how = self.template(url)
        if not template:
            return None
        template = scrub_query(template)
        if "{s}" in template:
            raise ImageryError("{s} sub-domains are not supported: put one sub-domain (a, b or c) in the address")
        notes = []
        if how == "wmts":
            notes.append("Read as a WMTS tile address. It must be a Web Mercator (GoogleMapsCompatible) tile set; "
                         "the preview shows whether it is.")
        elif how == "numbers":
            notes.append("Read the numbers in the address as zoom / column / row. If the preview is wrong, edit the address.")
        elif how == "wmts rest":
            notes.append("Read as a WMTS address: zoom / row / column, in a Web Mercator tile set; the preview shows whether it is.")
        extra = sorted(set(re.findall(r"\{[^}]*\}", template)) - set(KNOWN.findall(template)))
        if extra:
            notes.append(f"The panel does not fill {', '.join(extra)} in the address; replace it with a fixed value.")
        tile_px = None
        try:                                     # the zoom 0 tile is the whole world: it carries no location
            url0, headers = self.request({"url": template}, "", 0, 0, 0)
            r = await get(http, url0)
            if r.status_code == 200 and is_image(r):
                tile_px = Image.open(io.BytesIO(r.content)).width
        except Exception:
            pass
        notes.append("Its deepest zoom and resolution are found by the preview at your yard." if tile_px else
                     "It has no world-scale tile to check (usual for a regional service); the preview will tell.")
        site = host(template)
        service = re.search(r"/([^/]+)/(?:MapServer|ImageServer)/tile/", template, re.I)
        entry = {"name": pretty(service.group(1) if service else site.removeprefix("www.")), "kind": self.name, "url": template, "max_zoom": 21,
                 "native_m": 0.1, "tile_size": 512 if tile_px and tile_px >= 512 else None,
                 "attribution": f"Imagery: {site}", "license": guess_license()}
        return result(entry, notes, ["native_m", "max_zoom", "attribution"])
