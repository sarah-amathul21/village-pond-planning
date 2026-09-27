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

import hashlib
import time
from typing import Any, Optional

import numpy as np
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

# In-memory LRU cache for interpolated DEMs to optimize latency across multiple queries
_DEM_CACHE: dict[str, tuple[Any, Any]] = {}

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
    .container { max-width: 980px; margin: auto; background: white; padding: 35px; border-radius: 14px; box-shadow: 0 10px 30px rgba(0,0,0,0.08); }
    h1 { color: #176b4a; margin-top: 0; font-size: 26px; }
    .subtitle { color: #4a5568; margin-bottom: 20px; font-size: 15px; }
    .upload-box { border: 2px dashed #38a169; padding: 18px; border-radius: 10px; text-align: center; background: #f7faf8; cursor: pointer; transition: all 0.2s; margin-bottom: 15px; }
    .upload-box:hover { background: #edf7ed; }
    input[type="file"] { margin: 8px 0; font-size: 14px; }
    
    .land-toolbar { background: #f7fafc; padding: 14px 18px; border-radius: 10px; border: 1px solid #e2e8f0; margin-bottom: 15px; }
    .land-toolbar-title { font-weight: 700; color: #2d3748; margin-bottom: 8px; font-size: 14px; display: flex; align-items: center; justify-content: space-between; }
    
    .btn-group { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; align-items: center; }
    .btn { background: #176b4a; color: white; padding: 9px 18px; font-size: 14px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; transition: all 0.2s; }
    .btn:hover { background: #114e36; }
    .btn:disabled { background: #a0aec0; cursor: not-allowed; }
    .btn-primary { background: #176b4a; font-size: 16px; padding: 12px 28px; }
    .btn-secondary { background: #2b6cb0; }
    .btn-secondary:hover { background: #2c5282; }
    .btn-preset { background: #edf2f7; color: #2d3748; border: 1px solid #cbd5e0; }
    .btn-preset:hover { background: #e2e8f0; border-color: #a0aec0; }
    .btn-outline { background: white; color: #e53e3e; border: 1px solid #feb2b2; }
    .btn-outline:hover { background: #fff5f5; }
    
    .status-badge { display: inline-block; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
    .badge-info { background: #ebf8ff; color: #2b6cb0; border: 1px solid #bee3f8; }
    .badge-active { background: #feebc8; color: #c05621; border: 1px solid #fbd38d; }
    
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 15px; margin-top: 20px; }
    .card { background: #f7faf8; padding: 16px; border-radius: 10px; border-left: 4px solid #176b4a; }
    .card-water { border-left-color: #3182ce; background: #ebf8ff; }
    .card-title { font-size: 12px; text-transform: uppercase; color: #718096; font-weight: 700; margin-bottom: 6px; }
    .card-value { font-size: 20px; font-weight: 700; color: #1a202c; }
    .card-water .card-value { color: #2b6cb0; }
    
    #map { height: 480px; border-radius: 10px; margin-top: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); position: relative; }
    pre { background: #1a202c; color: #68d391; padding: 18px; border-radius: 10px; overflow-x: auto; font-size: 13px; font-family: monospace; max-height: 250px; }
    .tag { display: inline-block; background: #e6fffa; color: #234e52; font-size: 12px; font-weight: bold; padding: 4px 10px; border-radius: 20px; margin-bottom: 12px; }
    
    .map-overlay-card { position: absolute; top: 12px; right: 12px; z-index: 1000; background: rgba(255,255,255,0.95); padding: 12px 16px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.15); max-width: 280px; font-size: 13px; display: none; line-height: 1.5; border-left: 4px solid #3182ce; }
    .legend-box { display: flex; gap: 15px; margin-top: 10px; font-size: 13px; flex-wrap: wrap; }
    .legend-item { display: flex; align-items: center; gap: 6px; }
    .legend-color { width: 14px; height: 14px; border-radius: 3px; display: inline-block; }
  </style>
</head>
<body>
  <div class="container">
    <div class="tag">IIT Bhilai &bull; CS559 Computer Systems Design</div>
    <h1>Village Pond Planning System &mdash; Phase 2</h1>
    <p class="subtitle">Automated Terrain Analysis, Flow Direction (D8), Catchment Delineation, and Sizing.</p>

    <!-- Upload section -->
    <div class="upload-box" onclick="document.getElementById('contourMap').click()">
      <p style="margin: 0 0 6px 0; font-size: 15px; font-weight: 600;">📁 Upload a Contour Map (.KML / .KMZ)</p>
      <input type="file" id="contourMap" accept=".kml,.kmz" onclick="event.stopPropagation()">
      <div id="fileInfo" style="font-size: 13px; color: #4a5568; margin-top: 4px;">Default sample map will be used if none selected</div>
    </div>

    <!-- Intuitive Land Selection Toolbar -->
    <div class="land-toolbar">
      <div class="land-toolbar-title">
        <span>🗺️ Land Area Selection (Option to choose specific land or full map)</span>
        <span class="status-badge badge-info" id="modeBadge">Mode: Entire Contour Map</span>
      </div>
      
      <div class="btn-group">
        <button class="btn btn-secondary" id="btnDraw" onclick="toggleDragDraw()">📐 Drag-to-Draw Land Box</button>
        <button class="btn btn-preset" onclick="selectPreset('central')">🌾 Plot 1: Central Valley (~16 ha)</button>
        <button class="btn btn-preset" onclick="selectPreset('north')">🌾 Plot 2: North Basin (~12 ha)</button>
        <button class="btn btn-preset" onclick="selectPreset('south')">🌾 Plot 3: South Lowland (~14 ha)</button>
        <button class="btn btn-outline" id="btnClearSelection" onclick="clearLandSelection()" style="display:none;">✕ Clear Land Selection</button>
      </div>
      
      <div id="landHelper" style="font-size: 13px; color: #718096;">
        💡 <b>Easy tip:</b> Click any <b>Plot preset</b> above, or click <b>Drag-to-Draw</b> and simply drag a box over the map!
      </div>
    </div>

    <!-- Main Action Button -->
    <div class="btn-group" style="margin-bottom: 15px;">
      <button class="btn btn-primary" id="btnAnalyze" onclick="analyze()">⚡ Analyze Pond &amp; Catchment</button>
      <span id="loading" style="display:none; font-weight:600; color:#176b4a; font-size: 15px; margin-left: 10px;">
        ⚙️ Computing DEM interpolation, D8 flow accumulation &amp; sizing...
      </span>
    </div>

    <!-- Leaflet Map with Water Volume Overlay -->
    <div id="map">
      <div id="mapOverlay" class="map-overlay-card">
        <b style="color: #2b6cb0; font-size: 14px;">💧 Expected Water Volume</b><br>
        <span id="overlayVol" style="font-size: 18px; font-weight: 800; color: #2b6cb0;">-</span><br>
        <span id="overlayCatchment" style="color: #4a5568;">Catchment: -</span><br>
        <span id="overlayElev" style="color: #4a5568;">Pond Elevation: -</span>
      </div>
    </div>

    <!-- Map Legend -->
    <div class="legend-box">
      <div class="legend-item"><span class="legend-color" style="background: rgba(66, 153, 225, 0.4); border: 1px dashed #2b6cb0;"></span> Selected Land Area</div>
      <div class="legend-item"><span class="legend-color" style="background: rgba(56, 161, 105, 0.4); border: 1px solid #176b4a;"></span> Delineated Catchment Area</div>
      <div class="legend-item"><span class="legend-color" style="background: #e53e3e; border-radius: 50%;"></span> Suggested Pond Location</div>
    </div>

    <!-- Results Area -->
    <div id="resultsArea" style="display:none; margin-top: 25px;">
      <h2>Analysis Results</h2>
      <div class="stats-grid">
        <div class="card">
          <div class="card-title">Suggested Pond Location</div>
          <div class="card-value" id="coordVal" style="font-size: 17px;">-</div>
          <div id="elevVal" style="font-size: 13px; color: #4a5568; margin-top: 4px;">-</div>
        </div>
        <div class="card card-water">
          <div class="card-title">Expected Water Volume</div>
          <div class="card-value" id="waterVal">-</div>
          <div id="storageVal" style="font-size: 13px; color: #2b6cb0; margin-top: 4px;">-</div>
        </div>
        <div class="card">
          <div class="card-title">Catchment Area</div>
          <div class="card-value" id="areaVal">-</div>
          <div id="areaM2Val" style="font-size: 13px; color: #4a5568; margin-top: 4px;">-</div>
        </div>
        <div class="card">
          <div class="card-title">Selected Land Parcel</div>
          <div class="card-value" id="landAreaVal">-</div>
          <div id="landDetails" style="font-size: 13px; color: #4a5568; margin-top: 4px;">Entire region analyzed</div>
        </div>
        <div class="card">
          <div class="card-title">Terrain Relief</div>
          <div class="card-value" id="reliefVal">-</div>
          <div id="meanElevVal" style="font-size: 13px; color: #4a5568; margin-top: 4px;">-</div>
        </div>
        <div class="card">
          <div class="card-title">Annual Rainfall</div>
          <div class="card-value" id="rainVal">-</div>
          <div id="rainSourceVal" style="font-size: 12px; color: #718096; margin-top: 4px;">-</div>
        </div>
      </div>

      <h3 style="margin-top: 25px;">JSON Response</h3>
      <pre id="jsonOutput"></pre>
    </div>
  </div>

  <script>
    let mapInstance = null;
    let geojsonLayer = null;
    let selectedLandLayer = null;
    let cornerMarkers = [];
    let marker = null;
    let dragDrawMode = false;
    let isDrawingBox = false;
    let drawStartLatLng = null;
    let tempRect = null;
    let selectedBBox = null; // { min_lat, max_lat, min_lon, max_lon }

    // Quick Land Presets in the Dhamdha/Khairagarh region (Chhattisgarh)
    const PRESETS = {
      central: { name: "Plot 1 (Central Valley)", bounds: [[21.2485, 81.2870], [21.2530, 81.2935]] },
      north:   { name: "Plot 2 (North Basin)",    bounds: [[21.2520, 81.2880], [21.2565, 81.2940]] },
      south:   { name: "Plot 3 (South Lowland)",  bounds: [[21.2460, 81.2885], [21.2505, 81.2950]] },
    };

    function initMap() {
      if (mapInstance) return;
      mapInstance = L.map('map').setView([21.251, 81.291], 14);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap'
      }).addTo(mapInstance);

      // Add subtle boundary indicating where the contour survey data exists
      const surveyExtent = [[21.2398, 81.2814], [21.2635, 81.3126]];
      L.rectangle(surveyExtent, {
        color: '#4a5568',
        weight: 1,
        dashArray: '4, 4',
        fillColor: '#38a169',
        fillOpacity: 0.04
      }).bindTooltip("🗺️ Contour Survey Extent (Elevation Data Available Here)", { sticky: true }).addTo(mapInstance);

      // Smooth click-and-drag box drawing
      mapInstance.on('mousedown', onMapMouseDown);
      mapInstance.on('mousemove', onMapMouseMove);
      mapInstance.on('mouseup', onMapMouseUp);
    }

    function toggleDragDraw() {
      dragDrawMode = !dragDrawMode;
      const btn = document.getElementById('btnDraw');
      const badge = document.getElementById('modeBadge');
      const helper = document.getElementById('landHelper');

      if (dragDrawMode) {
        btn.textContent = '❌ Cancel Drawing';
        btn.classList.remove('btn-secondary');
        btn.classList.add('btn-outline');
        badge.className = 'status-badge badge-active';
        badge.textContent = 'Draw Mode Active: Drag on map';
        helper.innerHTML = '✏️ <b>Click down and drag across the map</b> to outline your land parcel.';
        mapInstance.dragging.disable();
        mapInstance.getContainer().style.cursor = 'crosshair';
        drawStartLatLng = null;
        isDrawingBox = false;
      } else {
        btn.textContent = '📐 Drag-to-Draw Land Box';
        btn.classList.remove('btn-outline');
        btn.classList.add('btn-secondary');
        mapInstance.dragging.enable();
        mapInstance.getContainer().style.cursor = '';
        helper.innerHTML = '💡 <b>Easy tip:</b> Click any <b>Plot preset</b> above, or click <b>Drag-to-Draw</b> and simply drag a box over the map!';
        if (tempRect) {
          mapInstance.removeLayer(tempRect);
          tempRect = null;
        }
        if (!selectedBBox) {
          badge.className = 'status-badge badge-info';
          badge.textContent = 'Mode: Entire Contour Map';
        }
      }
    }

    function onMapMouseDown(e) {
      if (!dragDrawMode) return;
      isDrawingBox = true;
      drawStartLatLng = e.latlng;
    }

    function onMapMouseMove(e) {
      if (!dragDrawMode || !isDrawingBox || !drawStartLatLng) return;
      const bounds = L.latLngBounds(drawStartLatLng, e.latlng);
      if (!tempRect) {
        tempRect = L.rectangle(bounds, {
          color: '#2b6cb0',
          weight: 2,
          dashArray: '4, 4',
          fillColor: '#3182ce',
          fillOpacity: 0.15
        }).addTo(mapInstance);
      } else {
        tempRect.setBounds(bounds);
      }
    }

    function onMapMouseUp(e) {
      if (!dragDrawMode || !isDrawingBox || !drawStartLatLng) return;
      isDrawingBox = false;
      const bounds = L.latLngBounds(drawStartLatLng, e.latlng);
      if (tempRect) {
        mapInstance.removeLayer(tempRect);
        tempRect = null;
      }

      const min_lat = bounds.getSouth();
      const max_lat = bounds.getNorth();
      const min_lon = bounds.getWest();
      const max_lon = bounds.getEast();

      // Ensure minimum box size
      if (Math.abs(max_lat - min_lat) > 0.0003 && Math.abs(max_lon - min_lon) > 0.0003) {
        if (max_lat < 21.2398 || min_lat > 21.2635 || max_lon < 81.2814 || min_lon > 81.3126) {
          alert("⚠️ Notice: The selected area is outside the uploaded contour map coverage (which is around the river valley). Please select a parcel inside the dashed survey area or click one of the Plot presets!");
        }
        setLandBounds(min_lat, max_lat, min_lon, max_lon, "Custom Drawn Parcel");
        toggleDragDraw();
      }
    }

    function selectPreset(key) {
      const p = PRESETS[key];
      if (!p) return;
      if (dragDrawMode) toggleDragDraw();
      setLandBounds(p.bounds[0][0], p.bounds[1][0], p.bounds[0][1], p.bounds[1][1], p.name);
    }

    function setLandBounds(min_lat, max_lat, min_lon, max_lon, label) {
      selectedBBox = { min_lat, max_lat, min_lon, max_lon };
      const bounds = [[min_lat, min_lon], [max_lat, max_lon]];

      if (selectedLandLayer) mapInstance.removeLayer(selectedLandLayer);
      removeCornerHandles();

      selectedLandLayer = L.rectangle(bounds, {
        color: '#2b6cb0',
        weight: 2,
        dashArray: '6, 6',
        fillColor: '#3182ce',
        fillOpacity: 0.18
      }).addTo(mapInstance);

      // Estimate area
      const approxHa = (((max_lat - min_lat) * 111000 * (max_lon - min_lon) * 103000) / 10000).toFixed(1);

      selectedLandLayer.bindPopup(`<b>${label || 'Selected Land Area'}</b><br>Area: ~${approxHa} ha<br><small>Drag corner handles to adjust</small>`);

      addCornerHandles(min_lat, max_lat, min_lon, max_lon);

      document.getElementById('btnClearSelection').style.display = 'inline-block';
      const badge = document.getElementById('modeBadge');
      badge.className = 'status-badge badge-active';
      badge.textContent = `Land Selected: ${label || 'Custom'} (~${approxHa} ha)`;

      mapInstance.fitBounds(bounds, { padding: [50, 50] });
    }

    function addCornerHandles(min_lat, max_lat, min_lon, max_lon) {
      removeCornerHandles();
      const corners = [
        { lat: min_lat, lon: min_lon, name: 'SW' },
        { lat: max_lat, lon: min_lon, name: 'NW' },
        { lat: max_lat, lon: max_lon, name: 'NE' },
        { lat: min_lat, lon: max_lon, name: 'SE' }
      ];

      corners.forEach(c => {
        const marker = L.circleMarker([c.lat, c.lon], {
          radius: 6,
          color: '#2b6cb0',
          fillColor: '#ffffff',
          fillOpacity: 1,
          weight: 2
        }).addTo(mapInstance);
        cornerMarkers.push(marker);
      });
    }

    function removeCornerHandles() {
      cornerMarkers.forEach(m => mapInstance.removeLayer(m));
      cornerMarkers = [];
    }

    function clearLandSelection() {
      selectedBBox = null;
      if (selectedLandLayer) {
        mapInstance.removeLayer(selectedLandLayer);
        selectedLandLayer = null;
      }
      removeCornerHandles();
      document.getElementById('btnClearSelection').style.display = 'none';
      const badge = document.getElementById('modeBadge');
      badge.className = 'status-badge badge-info';
      badge.textContent = 'Mode: Entire Contour Map';
      document.getElementById('landHelper').innerHTML = '💡 <b>Easy tip:</b> Click any <b>Plot preset</b> above, or click <b>Drag-to-Draw</b> and simply drag a box over the map!';
    }

    document.getElementById('contourMap').addEventListener('change', function(e) {
      if (e.target.files.length > 0) {
        document.getElementById('fileInfo').textContent = 'Selected: ' + e.target.files[0].name;
      }
    });

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

      // Attach selected land area coordinates if a parcel is selected
      if (selectedBBox) {
        formData.append("min_lat", selectedBBox.min_lat);
        formData.append("max_lat", selectedBBox.max_lat);
        formData.append("min_lon", selectedBBox.min_lon);
        formData.append("max_lon", selectedBBox.max_lon);
      }

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
        document.getElementById("coordVal").textContent = data.pond_location.lat.toFixed(5) + ", " + data.pond_location.lon.toFixed(5);
        document.getElementById("elevVal").textContent = "Elevation: " + data.pond_location.elevation_m.toFixed(2) + " m";
        
        // Expected Water Volume (Always calculated)
        let volM3 = null;
        if (data.expected_water_volume_m3) {
          volM3 = data.expected_water_volume_m3;
        } else if (data.sizing && data.sizing.runoff_volume_m3) {
          volM3 = data.sizing.runoff_volume_m3;
        }

        const waterVolStr = (volM3 ? Math.round(volM3).toLocaleString() + " m³" : "N/A");
        document.getElementById("waterVal").textContent = waterVolStr;
        document.getElementById("storageVal").textContent = (data.sizing ? "Storage Capacity: " + Math.round(data.sizing.storage_capacity_m3).toLocaleString() + " m³ (Depth: " + data.sizing.recommended_depth_m + "m)" : "");

        document.getElementById("areaVal").textContent = data.catchment_area_hectares + " ha";
        document.getElementById("areaM2Val").textContent = Math.round(data.catchment_area_m2).toLocaleString() + " m²";

        if (data.selected_land_area_m2) {
          const landHa = (data.selected_land_area_m2 / 10000).toFixed(2);
          document.getElementById("landAreaVal").textContent = landHa + " ha";
          document.getElementById("landDetails").textContent = "Constrained to selected parcel";
        } else {
          document.getElementById("landAreaVal").textContent = "Full Region";
          document.getElementById("landDetails").textContent = "Entire contour extent";
        }

        document.getElementById("reliefVal").textContent = data.elevation_stats.relief_m.toFixed(2) + " m";
        document.getElementById("meanElevVal").textContent = "Mean: " + data.elevation_stats.mean_m.toFixed(1) + " m (Min: " + data.elevation_stats.min_m.toFixed(1) + "m)";
        document.getElementById("rainVal").textContent = (data.rainfall ? data.rainfall.annual_rainfall_mm + " mm/yr" : "1,250 mm/yr");
        document.getElementById("rainSourceVal").textContent = (data.rainfall ? data.rainfall.source : "IMD Regional Climatological Baseline");

        document.getElementById("jsonOutput").textContent = JSON.stringify(data, null, 2);

        // Update Leaflet Map Layers
        if (geojsonLayer) mapInstance.removeLayer(geojsonLayer);
        if (marker) mapInstance.removeLayer(marker);

        // 1. Draw Selected Land Polygon (if returned)
        if (data.selected_land_geojson) {
          if (selectedLandLayer) mapInstance.removeLayer(selectedLandLayer);
          removeCornerHandles();
          selectedLandLayer = L.geoJSON(data.selected_land_geojson, {
            style: { color: "#2b6cb0", weight: 2, dashArray: "6, 6", fillColor: "#3182ce", fillOpacity: 0.15 }
          }).addTo(mapInstance);
          selectedLandLayer.bindPopup("<b>Selected Land Parcel</b><br>Area: " + (data.selected_land_area_m2 ? (data.selected_land_area_m2 / 10000).toFixed(2) + " ha" : "N/A"));
        }

        // 2. Draw Catchment Boundary
        if (data.catchment_boundary_geojson) {
          geojsonLayer = L.geoJSON(data.catchment_boundary_geojson, {
            style: { color: "#176b4a", fillColor: "#38a169", fillOpacity: 0.35, weight: 2 }
          }).addTo(mapInstance);
          mapInstance.fitBounds(geojsonLayer.getBounds().pad(0.1));
        }

        // 3. Draw Pond Marker with rich details & expected water volume
        marker = L.marker([data.pond_location.lat, data.pond_location.lon])
          .bindPopup(`
            <div style="font-family:'Segoe UI', sans-serif; min-width: 220px;">
              <h4 style="margin: 0 0 6px 0; color: #176b4a; font-size: 15px;">📍 Suggested Pond Location</h4>
              <b>Coordinates:</b> ${data.pond_location.lat.toFixed(5)}, ${data.pond_location.lon.toFixed(5)}<br>
              <b>Elevation:</b> ${data.pond_location.elevation_m.toFixed(2)} m<br>
              <b>Catchment Area:</b> ${data.catchment_area_hectares} ha<br>
              <div style="margin-top: 6px; padding: 6px 8px; background: #ebf8ff; border-radius: 6px; border-left: 3px solid #3182ce;">
                <b style="color: #2b6cb0;">💧 Expected Water Volume:</b><br>
                <span style="font-size: 16px; font-weight: bold; color: #2b6cb0;">${waterVolStr}</span>
              </div>
            </div>
          `)
          .addTo(mapInstance)
          .openPopup();

        // 4. Update Map Overlay Card
        const overlay = document.getElementById("mapOverlay");
        overlay.style.display = "block";
        document.getElementById("overlayVol").textContent = waterVolStr;
        document.getElementById("overlayCatchment").textContent = "Catchment: " + data.catchment_area_hectares + " ha";
        document.getElementById("overlayElev").textContent = "Pond Elevation: " + data.pond_location.elevation_m.toFixed(1) + " m";

      } catch (err) {
        alert("Error: " + err.message);
      } finally {
        btn.disabled = false;
        loading.style.display = "none";
      }
    }

    window.addEventListener('DOMContentLoaded', initMap);
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
    form = None
    try:
        form = await request.form()
    except Exception:
        pass

    if target_file is None and form:
        for val in form.values():
            if isinstance(val, UploadFile):
                target_file = val
                break

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

    # Extract optional land area bounding box (from form or query parameters)
    min_lat = None
    max_lat = None
    min_lon = None
    max_lon = None

    if form:
        try:
            if "min_lat" in form and form["min_lat"]: min_lat = float(form["min_lat"])
            if "max_lat" in form and form["max_lat"]: max_lat = float(form["max_lat"])
            if "min_lon" in form and form["min_lon"]: min_lon = float(form["min_lon"])
            if "max_lon" in form and form["max_lon"]: max_lon = float(form["max_lon"])
        except Exception:
            pass

    if min_lat is None and "min_lat" in request.query_params:
        try:
            min_lat = float(request.query_params["min_lat"])
            max_lat = float(request.query_params["max_lat"])
            min_lon = float(request.query_params["min_lon"])
            max_lon = float(request.query_params["max_lon"])
        except Exception:
            pass

    t0 = time.time()
    file_hash = hashlib.md5(raw).hexdigest()
    was_cached = False

    if file_hash in _DEM_CACHE:
        parsed, dem = _DEM_CACHE[file_hash]
        was_cached = True
    else:
        # 1. Parse contour lines -> scattered elevation points
        try:
            parsed = parse_contours(fname, raw)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        # 2. Interpolate to a regular DEM grid
        dem = build_dem(parsed.points, target_cells_across=200)

        # LRU eviction: maintain max 8 active DEM models in memory
        if len(_DEM_CACHE) >= 8:
            _DEM_CACHE.pop(next(iter(_DEM_CACHE)))
        _DEM_CACHE[file_hash] = (parsed, dem)

    n_points = len(parsed.points)

    # Optional: Build land mask if a land area bounding box was selected
    land_mask = None
    selected_land_geojson = None
    selected_land_area_m2 = None

    if min_lat is not None and max_lat is not None and min_lon is not None and max_lon is not None:
        s_lat, n_lat = min(min_lat, max_lat), max(min_lat, max_lat)
        w_lon, e_lon = min(min_lon, max_lon), max(min_lon, max_lon)
        lat_cond = (dem.lats >= s_lat) & (dem.lats <= n_lat)
        lon_cond = (dem.lons >= w_lon) & (dem.lons <= e_lon)
        candidate_mask = lat_cond[:, None] & lon_cond[None, :]
        if np.any(candidate_mask):
            land_mask = candidate_mask
            selected_land_area_m2 = float(land_mask.sum() * dem.cell_area_m2)
            selected_land_geojson = {
                "type": "Polygon",
                "coordinates": [[
                    [w_lon, s_lat],
                    [e_lon, s_lat],
                    [e_lon, n_lat],
                    [w_lon, n_lat],
                    [w_lon, s_lat]
                ]]
            }

    # 3. Run D8 terrain analysis (restricted to selected land if provided)
    result = analyze(dem, land_mask=land_mask)

    # 4. Package response
    pond_lon, pond_lat = dem.rowcol_to_lonlat(result.pond_row, result.pond_col)
    pond_elev = float(dem.elevation[result.pond_row, result.pond_col])

    catchment_area_m2 = float(result.catchment_mask.sum() * dem.cell_area_m2)
    catchment_elevs = dem.elevation[result.catchment_mask]

    boundary_geojson = catchment_polygon_geojson(dem, result.catchment_mask)

    # Rainfall + runoff sizing (guaranteed to succeed with IMD fallback)
    rain_stats = fetch_annual_rainfall(pond_lat, pond_lon)
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
        selected_land_geojson=selected_land_geojson,
        selected_land_area_m2=round(selected_land_area_m2, 1) if selected_land_area_m2 else None,
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
        expected_water_volume_m3=round(sizing.runoff_volume_m3, 1),
    )

    elapsed = time.time() - t0
    response_dict = response.model_dump()
    response_dict["_processing_time_s"] = round(elapsed, 3)
    response_dict["_cached_dem"] = was_cached
    return response_dict
