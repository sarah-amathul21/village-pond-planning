# Village Pond Planning System — Phase 2

Backend API that accepts a contour map (KML/KMZ), analyzes the terrain, and
returns catchment information for pond siting.

## Installation

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Running the API

```bash
uvicorn app.main:app --reload --port 8000
```

Interactive API docs (Swagger UI) are then available at
`http://localhost:8000/docs`.

## Demo — using the provided sample contour map

```bash
curl -X POST "http://localhost:8000/findCatchment" \
     -F "file=@contours_1m.kml"
```

or via Swagger UI: open `/docs`, expand `POST /findCatchment`,
upload `contours_1m.kml`, and execute.

---

## Methodology

### 1. Parsing (`app/kml_parser.py`)
The uploaded contour map is one Placemark per contour line, with the
elevation value stored in each Placemark's `<name>`. KMZ files are unzipped
in memory first (a KMZ is just a zip archive containing a `.kml`). Every
vertex of every contour line is extracted as a `(lon, lat, elevation)`
point. This produces a scattered point cloud — nothing here assumes a
specific village, coordinate range, or elevation range, so any contour KML
following the same Placemark/name convention parses the same way.

### 2. DEM construction (`app/dem.py`)
Terrain algorithms need elevation at every cell of a regular grid, but
contour lines only give elevation *along* the line. The scattered points
are interpolated onto a regular grid using linear (Delaunay-based)
interpolation — the standard approach for contour-to-DEM conversion.
Grid resolution is derived from the data's own bounding box (not hardcoded),
so a larger or smaller input area gets an appropriately scaled grid.

A light Gaussian smoothing pass is applied afterward. This addresses a
known artifact of interpolating a DEM purely from dense contour vertices:
near-duplicate points along each line otherwise create small "staircase"
steps between contour bands, which fragment single-cell flow decisions
into many spurious micro-sinks. Smoothing preserves the real valley/ridge
shape (which spans many cells) while removing single-cell noise.

### 3. Terrain analysis (`app/terrain.py`)
A self-contained D8 implementation (no external hydrology library, so every
step is inspectable):

- **Flow direction** — for each grid cell, find the neighbor (of its 8)
  with the steepest downhill slope; that's where its water flows. A cell
  with no downhill neighbor is a *sink* (local depression).
- **Flow accumulation** — process cells from highest to lowest elevation
  (a valid order since water only flows downhill) and propagate each
  cell's accumulated count to its downstream neighbor. A cell's final
  count = 1 + everything that drains through it — i.e. its upstream
  catchment size.
- **Pond site selection** — the sink with the largest flow accumulation,
  excluding a margin at the outer boundary (accumulation there is
  artificially truncated by the edge of the input data, not by real
  terrain).
- **Catchment delineation** — reverse-BFS over the flow-direction graph
  from the chosen pond cell, recovering every cell that drains into it.

### 4. Catchment geometry (`app/geometry.py`)
The boolean catchment mask (grid cells) is converted to a lon/lat polygon
by unioning each catchment cell's box footprint with Shapely, returned as
GeoJSON.

---

## API Documentation

### `POST /findCatchment`

Accepts a contour map and returns catchment information for the
best-identified pond site.

**Request:** `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | `.kml` or `.kmz` contour map |

**Response:** `200 OK`, `application/json`

```json
{
  "pond_location": {
    "lat": 21.250088,
    "lon": 81.290039,
    "elevation_m": 267.0
  },
  "catchment_area_m2": 149400.0,
  "catchment_area_hectares": 14.94,
  "catchment_cell_count": 568,
  "catchment_boundary_geojson": { "type": "Polygon", "coordinates": [...] },
  "elevation_stats": {
    "min_m": 267.0,
    "max_m": 286.0,
    "mean_m": 275.8,
    "relief_m": 19.0
  },
  "grid": {
    "rows": 163,
    "cols": 200,
    "cell_size_m_x": 16.21,
    "cell_size_m_y": 16.23,
    "source_contour_points": 160468,
    "source_contour_lines": 2710
  },
  "method": "D8 flow direction + flow accumulation, DEM interpolated from contour vertices"
}
```

**Error responses:**

| Status | Cause |
|---|---|
| 400 | File extension not `.kml`/`.kmz` |
| 413 | File exceeds 25 MB |
| 422 | File parses as XML but contains no elevation-labeled contour lines |

### `GET /`
Health check; lists available endpoints.

---

## Extensibility (for future phases)

The pipeline is split into independent, single-purpose modules so later
phases can extend it without rewriting earlier stages:

- **Land availability** (Phase 3?) plugs in as a mask applied to the DEM
  before pond-site selection, in the same way the boundary margin already
  excludes cells in `select_pond_site`.
- **Rainfall/runoff sizing** consumes `catchment_area_m2` directly —
  no changes needed upstream.
- **Different contour sources**: `kml_parser.py` is the only file that
  knows about KML structure; a future GeoTIFF or shapefile DEM source
  would only need a new parser producing the same `DEM` object that
  `terrain.py` already consumes.
- **Grid resolution** is a parameter (`target_cells_across`), not fixed,
  so performance/accuracy can be tuned per contour map without code changes.

## Known limitations / possible improvements

- The current D8 loop is plain Python (not vectorized), so runtime grows
  with grid size — fine at 200×163 cells (~3.5s) but should be vectorized
  or swapped for a library (e.g. `richdem`) for much larger contour maps.
- No explicit "fill sinks" pre-processing step is applied, so minor local
  noise still produces some small spurious sinks; the largest-catchment
  selection rule mitigates this but a dedicated depression-filling pass
  would make results more robust on noisier inputs.
