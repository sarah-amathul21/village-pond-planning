from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PondLocation(BaseModel):
    lat: float
    lon: float
    elevation_m: float


class ElevationStats(BaseModel):
    min_m: float
    max_m: float
    mean_m: float
    relief_m: float = Field(description="max - min within the catchment")


class GridMetadata(BaseModel):
    rows: int
    cols: int
    cell_size_m_x: float
    cell_size_m_y: float
    source_contour_points: int
    source_contour_lines: int


class CatchmentResponse(BaseModel):
    pond_location: PondLocation
    catchment_area_m2: float
    catchment_area_hectares: float
    catchment_cell_count: int
    catchment_boundary_geojson: dict[str, Any]
    elevation_stats: ElevationStats
    grid: GridMetadata
    method: str = "D8 flow direction + flow accumulation, DEM interpolated from contour vertices"
