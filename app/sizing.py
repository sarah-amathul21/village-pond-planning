"""
Runoff volume and pond sizing estimates, using the Rational Method.

    Runoff Volume = Rainfall (m) x Catchment Area (m^2) x Runoff Coefficient (C)

This is a bonus addition beyond Phase 2's required output (pond location +
catchment area). It depends on rainfall.py succeeding; if rainfall data is
unavailable, sizing is simply omitted from the response rather than the
request failing.

Runoff coefficient (C): Phase 2 has no land-use classification input yet
(that's a later phase per the assignment), so a single moderate default is
used, documented here rather than silently hardcoded. 0.35 sits in the
"agricultural / mixed vegetation" range (typical published ranges: forest
~0.1-0.3, agricultural ~0.3-0.5, barren/rocky ~0.5-0.7). This should be
replaced with a land-use-derived value once that data is available.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RUNOFF_COEFFICIENT = 0.35
POND_SIDE_SLOPE_FACTOR = 0.9  # crude trapezoidal-cross-section correction


@dataclass
class SizingEstimate:
    runoff_coefficient: float
    runoff_volume_m3: float
    recommended_depth_m: float
    storage_capacity_m3: float
    assumptions: str


def estimate_pond_sizing(
    catchment_area_m2: float,
    annual_rainfall_mm: float,
    runoff_coefficient: float = DEFAULT_RUNOFF_COEFFICIENT,
    max_depth_m: float = 4.0,
) -> SizingEstimate:
    rainfall_m = annual_rainfall_mm / 1000.0
    runoff_volume_m3 = rainfall_m * catchment_area_m2 * runoff_coefficient

    # Simple sizing heuristic: assume the pond's surface footprint is a
    # modest fraction of the catchment (ponds are much smaller than what
    # drains into them), then solve for the depth needed to hold the
    # estimated runoff volume, capped at a practical maximum depth.
    assumed_surface_fraction = 0.03
    surface_area_m2 = catchment_area_m2 * assumed_surface_fraction
    if surface_area_m2 <= 0:
        depth_m = 0.0
    else:
        depth_m = min(runoff_volume_m3 / (surface_area_m2 * POND_SIDE_SLOPE_FACTOR), max_depth_m)

    storage_capacity_m3 = surface_area_m2 * depth_m * POND_SIDE_SLOPE_FACTOR

    return SizingEstimate(
        runoff_coefficient=runoff_coefficient,
        runoff_volume_m3=round(runoff_volume_m3, 1),
        recommended_depth_m=round(depth_m, 2),
        storage_capacity_m3=round(storage_capacity_m3, 1),
        assumptions=(
            f"Runoff coefficient C={runoff_coefficient} assumed (no land-use "
            f"classification input yet, planned for a later phase); pond surface "
            f"assumed to be {assumed_surface_fraction:.0%} of catchment area; "
            f"depth capped at {max_depth_m} m for practicality."
        ),
    )
