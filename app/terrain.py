"""
Core terrain analysis: D8 flow direction, flow accumulation, pond-site
selection, and catchment delineation.

This is a self-contained D8 implementation (rather than an external
hydrology library) so every step is inspectable and explainable, and so the
module has no dependency beyond numpy -- keeping it portable for later
phases (e.g. swapping in a real land-availability mask).

Algorithm summary
------------------
1. Flow direction: for every DEM cell, find the neighbor (of its 8) with the
   steepest downhill slope. That neighbor is where the cell's water flows.
   A cell with no downhill neighbor is a "sink" (a pit / local minimum).
2. Flow accumulation: process cells from highest to lowest elevation
   (a valid topological order since water only flows downhill), and at each
   step add the cell's accumulated flow to whichever neighbor it drains
   into. A cell's final accumulation = 1 (itself) + everything that drains
   through it, i.e. the number of upstream cells -- a direct proxy for
   catchment size at that cell.
3. Pond site selection: candidate pond sites are sinks with high flow
   accumulation, excluding the domain's outer boundary (their accumulation
   is artificially truncated by the edge of the input data, not by real
   terrain).
4. Catchment delineation: starting from the chosen pond cell, walk the flow
   graph in reverse (BFS over "which cells drain into me") to recover every
   upstream cell -- this is the catchment.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .dem import DEM

# 8 neighbor offsets (row_delta, col_delta) and their direction codes
NEIGHBORS = [
    (-1, -1), (-1, 0), (-1, 1),
    (0, -1),           (0, 1),
    (1, -1),  (1, 0),  (1, 1),
]
SINK = -1


@dataclass
class TerrainAnalysis:
    flow_dir: np.ndarray       # index into NEIGHBORS, or SINK
    flow_accum: np.ndarray     # upstream cell count, including self
    pond_row: int
    pond_col: int
    catchment_mask: np.ndarray  # boolean, True = part of the catchment
    n_sinks_considered: int = field(default=0)


def _neighbor_distance_m(dr: int, dc: int, cell_x: float, cell_y: float) -> float:
    return float(np.hypot(dr * cell_y, dc * cell_x))


def compute_flow_direction(dem: DEM) -> np.ndarray:
    elev = dem.elevation
    n_rows, n_cols = elev.shape
    flow_dir = np.full((n_rows, n_cols), SINK, dtype=np.int8)

    for r in range(n_rows):
        for c in range(n_cols):
            best_slope = 0.0
            best_dir = SINK
            for i, (dr, dc) in enumerate(NEIGHBORS):
                nr, nc = r + dr, c + dc
                if not (0 <= nr < n_rows and 0 <= nc < n_cols):
                    continue
                dist = _neighbor_distance_m(dr, dc, dem.cell_size_m_x, dem.cell_size_m_y)
                drop = elev[r, c] - elev[nr, nc]
                slope = drop / dist if dist > 0 else 0.0
                if slope > best_slope:
                    best_slope = slope
                    best_dir = i
            flow_dir[r, c] = best_dir
    return flow_dir


def compute_flow_accumulation(dem: DEM, flow_dir: np.ndarray) -> np.ndarray:
    elev = dem.elevation
    n_rows, n_cols = elev.shape
    accum = np.ones((n_rows, n_cols), dtype=np.int64)

    # Process highest elevation first -- guarantees every cell's own
    # accumulation is finalized before it hands flow downstream.
    order = np.dstack(np.unravel_index(np.argsort(-elev.ravel()), elev.shape))[0]

    for r, c in order:
        d = flow_dir[r, c]
        if d == SINK:
            continue
        dr, dc = NEIGHBORS[d]
        nr, nc = r + dr, c + dc
        accum[nr, nc] += accum[r, c]
    return accum


def _is_boundary(r: int, c: int, n_rows: int, n_cols: int, margin: int) -> bool:
    return r < margin or c < margin or r >= n_rows - margin or c >= n_cols - margin


def select_pond_site(
    dem: DEM, flow_dir: np.ndarray, flow_accum: np.ndarray, boundary_margin: int = 2
) -> tuple[int, int, int]:
    """Pick the sink (local depression) with the largest catchment,
    excluding the outer boundary margin where flow accumulation is
    artificially truncated by the edge of the input contour data."""
    n_rows, n_cols = flow_dir.shape
    sink_mask = flow_dir == SINK

    best_score = -1
    best_rc = None
    n_considered = 0

    for r in range(n_rows):
        for c in range(n_cols):
            if not sink_mask[r, c]:
                continue
            if _is_boundary(r, c, n_rows, n_cols, boundary_margin):
                continue
            n_considered += 1
            score = int(flow_accum[r, c])
            if score > best_score:
                best_score = score
                best_rc = (r, c)

    if best_rc is None:
        # Fallback: no interior sink found (rare, e.g. very small/flat grid)
        # -- use the highest-accumulation interior cell instead.
        interior = flow_accum.copy()
        interior[:boundary_margin, :] = -1
        interior[-boundary_margin:, :] = -1
        interior[:, :boundary_margin] = -1
        interior[:, -boundary_margin:] = -1
        r, c = np.unravel_index(np.argmax(interior), interior.shape)
        best_rc = (int(r), int(c))

    return best_rc[0], best_rc[1], n_considered


def delineate_catchment(flow_dir: np.ndarray, pond_row: int, pond_col: int) -> np.ndarray:
    """Reverse-BFS over the flow-direction graph: find every cell that
    (directly or indirectly) drains into the pond cell."""
    n_rows, n_cols = flow_dir.shape

    # Build reverse adjacency: for each cell, which neighbors flow INTO it
    downstream_of = {}
    for r in range(n_rows):
        for c in range(n_cols):
            d = flow_dir[r, c]
            if d == SINK:
                continue
            dr, dc = NEIGHBORS[d]
            nr, nc = r + dr, c + dc
            downstream_of.setdefault((nr, nc), []).append((r, c))

    mask = np.zeros((n_rows, n_cols), dtype=bool)
    stack = [(pond_row, pond_col)]
    mask[pond_row, pond_col] = True
    while stack:
        cur = stack.pop()
        for up in downstream_of.get(cur, []):
            if not mask[up]:
                mask[up] = True
                stack.append(up)
    return mask


def analyze(dem: DEM) -> TerrainAnalysis:
    flow_dir = compute_flow_direction(dem)
    flow_accum = compute_flow_accumulation(dem, flow_dir)
    pond_row, pond_col, n_considered = select_pond_site(dem, flow_dir, flow_accum)
    catchment_mask = delineate_catchment(flow_dir, pond_row, pond_col)

    return TerrainAnalysis(
        flow_dir=flow_dir,
        flow_accum=flow_accum,
        pond_row=pond_row,
        pond_col=pond_col,
        catchment_mask=catchment_mask,
        n_sinks_considered=n_considered,
    )
