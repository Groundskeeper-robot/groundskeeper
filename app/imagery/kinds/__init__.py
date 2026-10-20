"""The protocols the panel can fetch imagery with. Adding one: a file here with a subclass of ExportKind or
TileKind, an instance in REGISTRY, and a request-shape case in tests/test_kinds.py (a test fails until there is one).
`python -m app.imagery schema --write` then regenerates catalog/schema.json."""
from ..common import ImageryError
from .arcgis import ArcGISImage, ArcGISMap
from .base import Box, ExportKind, Field, Kind, TileKind
from .tiles import Tiles
from .wms import WMS, wms_params

REGISTRY: dict[str, Kind] = {k.name: k for k in (ArcGISImage(), ArcGISMap(), WMS(), Tiles())}

# Names the first catalog used; old local files and the old custom-source form still say these.
ALIASES = {"xyz": "tiles", "wmts": "tiles"}


def get(name: str) -> Kind:
    kind = REGISTRY.get(ALIASES.get(name, name))
    if kind is None:
        raise ImageryError(f"unknown kind {name!r}; one of {', '.join(REGISTRY)}")
    return kind


__all__ = ["REGISTRY", "ALIASES", "get", "Kind", "ExportKind", "TileKind", "Field", "Box", "wms_params",
           "ArcGISImage", "ArcGISMap", "WMS", "Tiles"]
