# Village Pond Planning System — Phase 2

Backend API that accepts a contour map (KML/KMZ), analyzes the terrain, identifies a suitable pond location, and estimates its catchment area.

## Features

- Upload contour maps in KML or KMZ format
- Extract contour elevations and coordinates automatically
- Build a Digital Elevation Model (DEM) from contour data
- Analyze terrain using the D8 flow-direction algorithm
- Calculate flow accumulation
- Automatically identify a suitable pond location
- Delineate the upstream catchment area
- Return structured results as JSON
- Return the catchment boundary as GeoJSON
- Interactive API documentation using Swagger UI
- Bonus: rainfall analysis and preliminary pond sizing

## Technology Used

- Python
- FastAPI
- NumPy
- SciPy
- Shapely
- lxml
- Pydantic

## Project Structure

```text
app/
├── main.py          # FastAPI application and API route
├── kml_parser.py    # KML/KMZ contour parsing
├── dem.py           # DEM interpolation and smoothing
├── terrain.py       # D8 terrain and catchment analysis
├── geometry.py       # Catchment boundary to GeoJSON
├── schemas.py        # API response schemas
├── rainfall.py       # Optional historical rainfall analysis
└── sizing.py         # Optional pond sizing estimate

contours_1m.kml      # Sample contour map
demo_output.png      # Terrain and flow accumulation visualization
requirements.txt
```

## Installation

```bash
git clone https://github.com/sarah-amathul21/village-pond-planning.git
cd village-pond-planning

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

## Running the API

```bash
uvicorn app.main:app --reload --port 8000
```

API server:

```text
http://127.0.0.1:8000
```

Interactive API documentation:

```text
http://127.0.0.1:8000/docs
```

## API Endpoint

### POST `/findCatchment`

Upload a contour map and receive terrain and catchment information.

Example:

```bash
curl -X POST "http://127.0.0.1:8000/findCatchment" \
  -F "file=@contours_1m.kml"
```

Accepted file formats:

- `.kml`
- `.kmz`

## Catchment Estimation Approach

The uploaded contour map is analyzed using the following workflow:

1. **Contour Parsing**  
   Contour line coordinates and elevation values are extracted from the uploaded KML/KMZ file.

2. **DEM Generation**  
   The contour vertices are interpolated into a regular Digital Elevation Model using linear interpolation. Light Gaussian smoothing is applied to reduce small interpolation artifacts.

3. **D8 Flow Direction**  
   For each DEM cell, water is routed toward the neighboring cell with the steepest downhill slope.

4. **Flow Accumulation**  
   Upstream cells are accumulated to identify locations receiving water from larger areas.

5. **Pond Site Selection**  
   Interior terrain depressions or sinks with high flow accumulation are considered as suitable pond locations.

6. **Catchment Delineation**  
   The flow network is traced upstream from the selected pond site to identify all cells draining toward it.

7. **Catchment Area Estimation**  
   The catchment area is calculated from the number of catchment cells and DEM cell area.

The implementation derives all terrain information from the uploaded file and does not hard-code coordinates or results from the sample contour map.

## Demonstration with Provided Sample Map

Using the provided `contours_1m.kml` sample map, the API successfully produced:

- **Estimated catchment area:** approximately **15 hectares**
- **Contour lines processed:** 2710
- **Contour points processed:** 160,468
- **Method:** DEM interpolation + D8 flow direction + flow accumulation + upstream catchment delineation

The exact result may vary slightly with DEM interpolation and smoothing parameters.

## API Response

The API returns structured JSON containing:

- Selected pond location
- Pond elevation
- Catchment area in square meters
- Catchment area in hectares
- Catchment boundary as GeoJSON
- Catchment elevation statistics
- DEM grid metadata
- Optional historical rainfall information
- Optional preliminary pond sizing estimate
- Terrain analysis method

## Demo Visualization

The generated visualization shows:

- **Left:** Interpolated and smoothed DEM with the delineated catchment and selected pond site
- **Right:** Flow accumulation/drainage network with the selected pond site

![Terrain Analysis Output](demo_output.png)

## Extensibility

The current terrain analysis pipeline can be extended with:

- Land-use classification
- Soil information
- Existing water bodies
- Rainfall-runoff modelling
- Suitability constraints
- Multiple pond-site ranking
- Higher-resolution DEM data
- Advanced hydrological analysis

## API Documentation

FastAPI automatically provides interactive API documentation at:

```text
http://127.0.0.1:8000/docs
```

The `/docs` interface allows direct file upload, API testing, and inspection of the request and response schemas.
