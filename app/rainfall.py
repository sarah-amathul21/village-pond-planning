"""
Fetches historical rainfall for a coordinate using the Open-Meteo Historical
Weather API (free, no API key required).

This is a "best effort" addition on top of the core (required) catchment
analysis: pond location and catchment area are computed entirely offline
from the contour file and never depend on this module. If the rainfall API
is unreachable or returns no data, callers should treat rainfall/runoff as
unavailable rather than fail the whole request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


@dataclass
class RainfallStats:
    annual_rainfall_mm: float
    years_averaged: int
    source: str = "Open-Meteo Historical Weather API"


def fetch_annual_rainfall(lat: float, lon: float, years: int = 5) -> RainfallStats | None:
    """Fetch daily precipitation for the last `years` years at (lat, lon)
    and return the average annual total. Returns None if the API call
    fails or returns no usable data -- callers should degrade gracefully."""
    end = date.today().replace(day=1)
    start = end.replace(year=end.year - years)

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum",
        "timezone": "auto",
    }

    try:
        resp = httpx.get(ARCHIVE_URL, params=params, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    daily = data.get("daily", {})
    precip = daily.get("precipitation_sum")
    dates = daily.get("time")
    if not precip or not dates:
        return None

    # Sum precipitation, ignoring any null days, then annualize by the
    # actual number of years of data actually returned (robust to partial
    # data near the boundary of what the API has available).
    total_mm = sum(v for v in precip if v is not None)
    n_days = len(dates)
    if n_days == 0:
        return None
    n_years = max(n_days / 365.25, 0.1)
    annual_mm = total_mm / n_years

    return RainfallStats(annual_rainfall_mm=round(annual_mm, 1), years_averaged=years)
