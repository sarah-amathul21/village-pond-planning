"""
Phase 2 API: accepts a contour map (KML/KMZ), analyzes terrain, and
returns catchment information for pond planning.

Run locally:
    uvicorn app.main:app --reload --port 8000

Then POST a file to /findCatchment, e.g.:
    curl -X POST "http://localhost:8000/findCatchment" \
         -F "contour_map=@contours_1m.kml"
"""
from __future__ import annotations

import time

import numpy as np
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from .dem import build_dem
from .geometry import catchment_polygon_geojson
from .kml_parser import parse_contours
from .rainfall import fetch_annual_rainfall
from .schemas import (
    CatchmentResponse,
    ElevationStats,
    GridMetadata,
    PondLocation,
    RainfallInfo,
    SizingInfo,
)
from .sizing import estimate_pond_sizing
from .terrain import analyze

app = FastAPI(
    title="Village Pond Planning API - Phase 2",
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

HTML_UI = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Village Pond Planning System - Phase 2</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f4f2; margin: 0; padding: 25px; color: #2d3748; }
    .container { max-width: 900px; margin: auto; background: white; padding: 35px; border-radius: 14px; box-shadow: 0 10px 30px rgba(0,0,0,0.08); }
    h1 { color: #176b4a; margin-top: 0; font-size: 26px; }
    .subtitle { color: #4a5568; margin-bottom: 25px; font-size: 15px; }
    .upload-box { border: 2px dashed #38a169; padding: 30px; border-radius: 10px; text-align: center; background: #f7faf8; cursor: pointer; transition: all 0.2s; }
    .upload-box:hover { background: #edf7ed; }
    input[type="file"] { margin: 15px 0; font-size: 15px; }
    .btn { background: #176b4a; color: white; padding: 12px 28px; font-size: 16px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; transition: background 0.2s; }
    .btn:hover { background: #114e36; }
    .btn:disabled { background: #a0aec0; cursor: not-allowed; }
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-top: 20px; }
    .card { background: #f7faf8; padding: 16px; border-radius: 10px; border-left: 4px solid #176b4a; }
    .card-title { font-size: 12px; text-transform: uppercase; color: #718096; font-weight: 700; margin-bottom: 6px; }
    .card-value { font-size: 20px; font-weight: 700; color: #1a202c; }
    #map { height: 420px; border-radius: 10px; margin-top: 25px; display: none; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
    pre { background: #1a202c; color: #68d391; padding: 18px; border-radius: 10px; overflow-x: auto; font-size: 13px; font-family: monospace; max-height: 250px; }
    .badge { display: inline-block; background: #e6fffa; color: #234e52; font-size: 12px; font-weight: bold; padding: 4px 10px; border-radius: 20px; margin-bottom: 15px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="badge">IIT Bhilai &bull; CS559 Computer Systems Design</div>
    <h1>Village Pond Planning System &mdash; Phase 2</h1>
    <p class="subtitle">Automated Terrain Analysis, Flow Direction (D8), Catchment Delineation, and Sizing.</p>

    <div class="upload-box" onclick="document.getElementById('contourMap').click()">
      <p style="margin: 0 0 10px 0; font-size: 16px; font-weight: 600;">Choose or drop a contour map (.KML / .KMZ)</p>
      <input type="file" id="contourMap" accept=".kml,.kmz" onclick="event.stopPropagation()">
    </div>
    <br>
    <button class="btn" id="btnAnalyze" onclick="analyze()">Analyze Catchment</button>
    <span id="loading" style="display:none; margin-left:15px; font-weight:600; color:#176b4a;">Processing DEM &amp; D8 analysis...</span>

    <div id="resultsArea" style="display:none; margin-top: 25px;">
      <h2>Analysis Results</h2>
      <div class="stats-grid">
        <div class="card"><div class="card-title">Pond Coordinates</div><div class="card-value" id="coordVal">-</div></div>
        <div class="card"><div class="card-title">Pond Elevation</div><div class="card-value" id="elevVal">-</div></div>
        <div class="card"><div class="card-title">Catchment Area</div><div class="card-value" id="areaVal">-</div></div>
        <div class="card"><div class="card-title">Terrain Relief</div><div class="card-value" id="reliefVal">-</div></div>
        <div class="card"><div class="card-title">Annual Rainfall</div><div class="card-value" id="rainVal">-</div></div>
        <div class="card"><div class="card-title">Storage Capacity</div><div class="card-value" id="storageVal">-</div></div>
      </div>

      <div id="map"></div>

      <h3 style="margin-top: 25px;">JSON Response</h3>
      <pre id="jsonOutput"></pre>
    </div>
  </div>

  <script>
    let mapInstance = null;
    let geojsonLayer = null;
    let marker = null;

    async function analyze() {
      const fileInput = document.getElementById("contourMap");
      const file = fileInput.files[0];
      if (!file) {
        alert("Please select a .kml or .kmz contour file first.");
        return;
      }

      const btn = document.getElementById("btnAnalyze");
      const loading = document.getElementById("loading");
      btn.disabled = true;
      loading.style.display = "inline";

      const formData = new FormData();
      formData.append("contour_map", file);
      formData.append("file", file);

      try {
        const resp = await fetch(window.location.pathname, {
          method: "POST",
          body: formData
        });

        const data = await resp.json();
        if (!resp.ok) {
          throw new Error(data.detail || "Analysis failed");
        }

        document.getElementById("resultsArea").style.display = "block";
        document.getElementById("coordVal").textContent = data.pond_location.lat.toFixed(4) + ", " + data.pond_location.lon.toFixed(4);
        document.getElementById("elevVal").textContent = data.pond_location.elevation_m.toFixed(2) + " m";
        document.getElementById("areaVal").textContent = data.catchment_area_hectares + " ha (" + Math.round(data.catchment_area_m2) + " m²)";
        document.getElementById("reliefVal").textContent = data.elevation_stats.relief_m.toFixed(2) + " m";
        document.getElementById("rainVal").textContent = (data.rainfall ? data.rainfall.annual_rainfall_mm + " mm" : "N/A");
        document.getElementById("storageVal").textContent = (data.sizing ? Math.round(data.sizing.storage_capacity_m3) + " m³" : "N/A");

        document.getElementById("jsonOutput").textContent = JSON.stringify(data, null, 2);

        // Render Leaflet Map
        const mapDiv = document.getElementById("map");
        mapDiv.style.display = "block";
        if (!mapInstance) {
          mapInstance = L.map('map');
          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap'
          }).addTo(mapInstance);
        }

        if (geojsonLayer) mapInstance.removeLayer(geojsonLayer);
        if (marker) mapInstance.removeLayer(marker);

        if (data.catchment_boundary_geojson) {
          geojsonLayer = L.geoJSON(data.catchment_boundary_geojson, {
            style: { color: "#176b4a", fillColor: "#38a169", fillOpacity: 0.4, weight: 2 }
          }).addTo(mapInstance);
          mapInstance.fitBounds(geojsonLayer.getBounds());
        }

        marker = L.marker([data.pond_location.lat, data.pond_location.lon])
          .bindPopup("<b>Recommended Pond Site</b><br>Elevation: " + data.pond_location.elevation_m.toFixed(2) + "m")
          .addTo(mapInstance)
          .openPopup();

      } catch (err) {
        alert("Error: " + err.message);
      } finally {
        btn.disabled = false;
        loading.style.display = "none";
      }
    }
  </script>
</body>
</html>
"""

@app.get("/")
@app.get("/findCatchment")
@app.get("/analyzeContour")
def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return HTMLResponse(content=HTML_UI)
    return {
        "status": "ok",
        "service": "pond-planning-api",
        "message": "Pond Catchment Analysis API is active. Submit contour map via POST multipart/form-data under 'contour_map' or 'file'.",
        "endpoints": ["/findCatchment", "/analyzeContour", "/api/analyzeContour", "/api/v1/analyzeContour"],
    }


@app.post("/findCatchment", response_model=CatchmentResponse)
@app.post("/analyzeContour", response_model=CatchmentResponse)
@app.post("/api/analyzeContour", response_model=CatchmentResponse)
@app.post("/api/v1/analyzeContour", response_model=CatchmentResponse)
@app.post("/", response_model=CatchmentResponse)
async def find_catchment(
    request: Request,
    contour_map: Optional[UploadFile] = File(None),
    file: Optional[UploadFile] = File(None),
):
    target_file = contour_map or file
    if target_file is None:
        try:
            form = await request.form()
            for val in form.values():
                if isinstance(val, UploadFile):
                    target_file = val
                    break
        except Exception:
            pass

    if target_file is None:
        raise HTTPException(
            status_code=400,
            detail="No contour map file provided. Please send a .kml or .kmz file under 'contour_map' or 'file'.",
        )

    raw = await target_file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB).")

    fname = target_file.filename or "contours.kml"
    if not fname.lower().endswith(ALLOWED_EXTENSIONS) and not (raw.startswith(b"PK") or b"<kml" in raw.lower()):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Expected one of {ALLOWED_EXTENSIONS}.",
        )

    t0 = time.time()

    # 1. Parse contour lines -> scattered elevation points
    try:
        parsed = parse_contours(fname, raw)
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

    # Bonus (not required this phase): historical rainfall + runoff/sizing
    # estimate. Best-effort -- if the rainfall API is unreachable, these
    # fields are simply omitted rather than failing the whole request.
    rainfall_info = None
    sizing_info = None
    rain_stats = fetch_annual_rainfall(pond_lat, pond_lon)
    if rain_stats is not None:
        rainfall_info = RainfallInfo(
            annual_rainfall_mm=rain_stats.annual_rainfall_mm,
            years_averaged=rain_stats.years_averaged,
            source=rain_stats.source,
        )
        sizing = estimate_pond_sizing(catchment_area_m2, rain_stats.annual_rainfall_mm)
        sizing_info = SizingInfo(
            runoff_coefficient=sizing.runoff_coefficient,
            runoff_volume_m3=sizing.runoff_volume_m3,
            recommended_depth_m=sizing.recommended_depth_m,
            storage_capacity_m3=sizing.storage_capacity_m3,
            assumptions=sizing.assumptions,
        )

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
        rainfall=rainfall_info,
        sizing=sizing_info,
    )

    elapsed = time.time() - t0
    response_dict = response.model_dump()
    response_dict["_processing_time_s"] = round(elapsed, 2)
    return response_dict
