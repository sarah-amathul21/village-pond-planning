"""
Phase 2 API: accepts a contour map (KML/KMZ), analyzes terrain, and
returns catchment information for pond planning.

Run locally:
    uvicorn app.main:app --reload --port 8000

Then POST a file to /findCatchment, e.g.:
    curl -X POST "http://localhost:8000/findCatchment" \
         -F "file=@contours_1m.kml"
"""
from __future__ import annotations

import time

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .dem import build_dem
from .geometry import catchment_polygon_geojson
from .kml_parser import parse_contours
from .schemas import (
    CatchmentResponse,
    ElevationStats,
    GridMetadata,
    PondLocation,
)
from .terrain import analyze

app = FastAPI(
    title="Village Pond Planning API",
    description="Analyzes a contour map (KML/KMZ) and returns catchment information for pond siting.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = (".kml", ".kmz")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


@app.get("/")
def root():
    return {"status": "ok", "service": "pond-planning-api", "endpoints": ["/findCatchment"]}


@app.post("/findCatchment", response_model=CatchmentResponse)
async def find_catchment(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Expected one of {ALLOWED_EXTENSIONS}.",
        )

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB).")

    t0 = time.time()

    # 1. Parse contour lines -> scattered elevation points
    try:
        parsed = parse_contours(file.filename, raw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    n_points = len(parsed.points)

    # 2. Interpolate to a regular DEM grid
    dem = build_dem(parsed.points, target_cells_across=200)

    # 3. Run D8 terrain analysis: flow direction, accumulation, pond site, catchment
    result = analyze(dem)

    # 4. Package response
    pond_lon, pond_lat = dem.rowcol_to_lonlat(result.pond_row, result.pond_col)
    pond_elev = float(dem.elevation[result.pond_row, result.pond_col])

    catchment_area_m2 = float(result.catchment_mask.sum() * dem.cell_area_m2)
    catchment_elevs = dem.elevation[result.catchment_mask]

    boundary_geojson = catchment_polygon_geojson(dem, result.catchment_mask)

    response = CatchmentResponse(
        pond_location=PondLocation(lat=pond_lat, lon=pond_lon, elevation_m=pond_elev),
        catchment_area_m2=round(catchment_area_m2, 1),
        catchment_area_hectares=round(catchment_area_m2 / 10_000, 3),
        catchment_cell_count=int(result.catchment_mask.sum()),
        catchment_boundary_geojson=boundary_geojson,
        elevation_stats=ElevationStats(
            min_m=float(np.min(catchment_elevs)),
            max_m=float(np.max(catchment_elevs)),
            mean_m=float(np.mean(catchment_elevs)),
            relief_m=float(np.max(catchment_elevs) - np.min(catchment_elevs)),
        ),
        grid=GridMetadata(
            rows=dem.elevation.shape[0],
            cols=dem.elevation.shape[1],
            cell_size_m_x=round(dem.cell_size_m_x, 2),
            cell_size_m_y=round(dem.cell_size_m_y, 2),
            source_contour_points=n_points,
            source_contour_lines=parsed.n_lines,
        ),
    )

    elapsed = time.time() - t0
    response_dict = response.model_dump()
    response_dict["_processing_time_s"] = round(elapsed, 2)
    return response_dict
