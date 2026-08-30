"""
Parses contour maps supplied as KML or KMZ files.

A contour KML (as exported by common contour-generation tools) contains one
Placemark per contour line. Each Placemark's <name> holds the elevation
value for that line, and its <LineString><coordinates> holds the (lon, lat)
vertices along that elevation.

This module extracts every vertex as a labeled 3D point (lon, lat, elevation).
That scattered point cloud is the raw material the DEM interpolation step
(dem.py) turns into a regular elevation grid.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from lxml import etree

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


@dataclass
class ContourPoint:
    lon: float
    lat: float
    elevation: float


def _extract_kml_bytes(filename: str, raw: bytes) -> bytes:
    """KMZ is just a zip archive containing a .kml file (usually doc.kml).
    Detect by extension first, fall back to sniffing the zip magic bytes so
    a mislabeled upload still works."""
    is_kmz = filename.lower().endswith(".kmz") or raw[:2] == b"PK"
    if not is_kmz:
        return raw

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise ValueError("KMZ archive does not contain a .kml file")
        # Prefer doc.kml if present (the common convention), else first match
        preferred = [n for n in kml_names if n.lower().endswith("doc.kml")]
        target = preferred[0] if preferred else kml_names[0]
        return zf.read(target)


@dataclass
class ParsedContours:
    points: list[ContourPoint]
    n_lines: int  # number of contour Placemarks successfully parsed
    n_unique_elevations: int


def parse_contours(filename: str, raw: bytes) -> ParsedContours:
    """Parse a KML/KMZ contour file into a flat list of (lon, lat, elevation)
    points, plus line-count metadata. Works for any contour KML following
    the Placemark-per-line, name-as-elevation convention -- nothing here is
    specific to the sample file, so a differently-shaped contour map using
    the same convention will parse the same way."""
    kml_bytes = _extract_kml_bytes(filename, raw)

    parser = etree.XMLParser(recover=True)  # tolerate minor malformed XML
    root = etree.fromstring(kml_bytes, parser=parser)

    points: list[ContourPoint] = []
    placemarks = root.findall(".//kml:Placemark", KML_NS)

    n_lines = 0
    elevations_seen: set[float] = set()

    for pm in placemarks:
        name_el = pm.find("kml:name", KML_NS)
        if name_el is None or name_el.text is None:
            continue
        try:
            elevation = float(name_el.text.strip())
        except ValueError:
            # Not an elevation-labeled placemark (e.g. a pushpin/marker) -- skip
            continue

        coord_elements = pm.findall(".//kml:coordinates", KML_NS)
        line_had_points = False
        for coords_el in coord_elements:
            if not coords_el.text:
                continue
            for token in coords_el.text.strip().split():
                parts = token.split(",")
                if len(parts) < 2:
                    continue
                lon, lat = float(parts[0]), float(parts[1])
                points.append(ContourPoint(lon=lon, lat=lat, elevation=elevation))
                line_had_points = True

        if line_had_points:
            n_lines += 1
            elevations_seen.add(elevation)

    if not points:
        raise ValueError(
            "No elevation-labeled contour lines found. Expected each "
            "Placemark's <name> to hold the contour's elevation value."
        )
    return ParsedContours(points=points, n_lines=n_lines, n_unique_elevations=len(elevations_seen))
