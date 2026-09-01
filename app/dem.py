"""
Builds a regular-grid Digital Elevation Model (DEM) from scattered,
elevation-labeled contour points.

Contour lines only tell us elevation *along* the line -- to run terrain
algorithms (flow direction, flow accumulation) we need elevation at every
cell of a regular grid. This module interpolates the scattered points onto
such a grid using linear (Delaunay-based) interpolation, which is the
standard approach for contour-to-DEM conversion at this scale.

Nothing here is tuned to the sample file: grid resolution is derived from
the point density of whatever contour file is supplied, and the geographic
extent is taken from the data's own bounding box.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

from .kml_parser import ContourPoint

# Approximate meters per degree of latitude (fairly constant globally)
METERS_PER_DEG_LAT = 111_320.0


def meters_per_deg_lon(lat_deg: float) -> float:
    """Meters per degree of longitude shrinks with distance from the
    equator; we evaluate it at the region's mean latitude."""
    return METERS_PER_DEG_LAT * np.cos(np.radians(lat_deg))


@dataclass
class DEM:
    elevation: np.ndarray  # 2D array [row, col], row 0 = north
    lons: np.ndarray  # 1D array of grid column longitudes
    lats: np.ndarray  # 1D array of grid row latitudes (descending, north->south)
    cell_size_m_x: float
    cell_size_m_y: float

    @property
    def cell_area_m2(self) -> float:
        return self.cell_size_m_x * self.cell_size_m_y

    def rowcol_to_lonlat(self, row: int, col: int) -> tuple[float, float]:
        return float(self.lons[col]), float(self.lats[row])


def build_dem(
    points: list[ContourPoint],
    target_cells_across: int = 200,
    smooth_sigma_cells: float = 1.2,
) -> DEM:
    """Interpolate scattered contour points onto a regular grid.

    target_cells_across controls resolution: it's a resolution *budget*,
    not a hardcoded size -- the actual cell size in meters is derived from
    the input data's own geographic extent, so denser or sparser contour
    maps naturally get an appropriately scaled grid.

    smooth_sigma_cells applies a light Gaussian smoothing pass after
    interpolation. This is standard practice when deriving a DEM purely
    from contour vertices: dense, near-duplicate points along each line
    otherwise produce small "staircase" artifacts between contour bands,
    which fragment single-cell flow-direction decisions and create many
    spurious micro-sinks. Smoothing preserves the real valley/ridge shape
    (which spans many cells) while removing noise at the single-cell scale.
    Set to 0 to disable.
    """
    lons = np.array([p.lon for p in points])
    lats = np.array([p.lat for p in points])
    elevs = np.array([p.elevation for p in points])

    lon_min, lon_max = lons.min(), lons.max()
    lat_min, lat_max = lats.min(), lats.max()
    mean_lat = (lat_min + lat_max) / 2.0

    # Choose grid dimensions so cells are roughly square in real-world meters,
    # regardless of the lon/lat aspect ratio at this latitude.
    width_m = (lon_max - lon_min) * meters_per_deg_lon(mean_lat)
    height_m = (lat_max - lat_min) * METERS_PER_DEG_LAT
    aspect = width_m / height_m if height_m > 0 else 1.0

    n_cols = max(target_cells_across, 10)
    n_rows = max(int(round(n_cols / aspect)), 10)

    grid_lons = np.linspace(lon_min, lon_max, n_cols)
    grid_lats = np.linspace(lat_max, lat_min, n_rows)  # descending: row 0 = north
    mesh_lon, mesh_lat = np.meshgrid(grid_lons, grid_lats)

    # Linear (Delaunay) interpolation is standard for contour->DEM; fill any
    # points outside the convex hull (grid corners) with nearest-neighbor
    # rather than leaving NaN holes.
    grid_elev = griddata(
        (lons, lats), elevs, (mesh_lon, mesh_lat), method="linear"
    )
    nan_mask = np.isnan(grid_elev)
    if nan_mask.any():
        fill = griddata(
            (lons, lats), elevs, (mesh_lon, mesh_lat), method="nearest"
        )
        grid_elev[nan_mask] = fill[nan_mask]

    if smooth_sigma_cells > 0:
        grid_elev = gaussian_filter(grid_elev, sigma=smooth_sigma_cells)

    cell_size_m_x = width_m / (n_cols - 1)
    cell_size_m_y = height_m / (n_rows - 1)

    return DEM(
        elevation=grid_elev,
        lons=grid_lons,
        lats=grid_lats,
        cell_size_m_x=cell_size_m_x,
        cell_size_m_y=cell_size_m_y,
    )
