"""Aerial imagery under the robot's map. Start with service.py (what is fetched, when, and the privacy gates),
catalog.py (where sources come from) and kinds/ (the protocols). Contributors adding a source: catalog/README.md."""
from . import kinds
from .catalog import CATALOG_DIR, load as load_catalog
from .common import ImageryError, is_blank
from .coverage import covers
from .kinds import wms_params
from .mercator import (HALF, MAX_PX, R, chunks, from_merc, metres_to_box, plan_size, square, tile_at, tile_bounds,
                       tile_range, tile_zoom, to_merc)
from .service import CONTEXT_MIN_ZOOM, CONTEXT_RADIUS_M, DEFAULT_SOURCE, Imagery

__all__ = ["Imagery", "ImageryError", "kinds", "load_catalog", "CATALOG_DIR", "covers", "is_blank", "wms_params",
           "HALF", "MAX_PX", "R", "chunks", "from_merc", "metres_to_box", "plan_size", "square", "tile_at",
           "tile_bounds", "tile_range", "tile_zoom", "to_merc", "CONTEXT_MIN_ZOOM", "CONTEXT_RADIUS_M", "DEFAULT_SOURCE"]
