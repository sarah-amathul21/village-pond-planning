"""Converts a boolean catchment mask (grid cells) into a lon/lat polygon."""
from __future__ import annotations

import numpy as np
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from .dem import DEM


def catchment_polygon_geojson(dem: DEM, mask: np.ndarray) -> dict:
    """Union the lon/lat boxes of every catchment cell into one polygon
    (possibly multi-part) and return it as GeoJSON geometry."""
    n_rows, n_cols = mask.shape
    half_dx = (dem.lons[1] - dem.lons[0]) / 2 if n_cols > 1 else 1e-5
    half_dy = (dem.lats[0] - dem.lats[1]) / 2 if n_rows > 1 else 1e-5  # lats descending

    boxes = []
    rows, cols = np.where(mask)
    for r, c in zip(rows, cols):
        lon = dem.lons[c]
        lat = dem.lats[r]
        boxes.append(box(lon - half_dx, lat - half_dy, lon + half_dx, lat + half_dy))

    merged = unary_union(boxes)
    return mapping(merged)
