"""
Distance computation for the property/campus join (ADR-002, ADR-006).

Two numbers, and they mean different things:

- ``straight_line_km`` is haversine, always present, computed on save. An honest
  lower bound.
- ``walking_distance_km`` and ``walking_minutes`` come **only** from a routing
  provider, and stay null until the routing job has run.

**Walking time is never derived from straight-line distance.** Not by dividing
by 5 km/h, not by a fudge factor. A null walking time renders as "—"; a
fabricated one erodes exactly the trust the platform is selling. There is no
code path in this module that produces one, which is deliberate.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

#: Mean Earth radius in kilometres.
EARTH_RADIUS_KM = 6371.0088

#: Kilometres per degree of latitude. Very nearly constant.
KM_PER_DEGREE_LATITUDE = 111.32


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def straight_line_km(lat1: float, lon1: float, lat2: float, lon2: float) -> Decimal:
    """Haversine distance, quantised to the column's two decimal places."""
    return Decimal(haversine_km(lat1, lon1, lat2, lon2)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def bounding_box(
    latitude: float, longitude: float, radius_km: float
) -> tuple[float, float, float, float]:
    """A (min_lat, max_lat, min_lon, max_lon) box enclosing a radius.

    The longitude term is ``cos(radians(latitude))``. The draft used
    ``abs(lat / 90)``, which is wrong twice: it works in statute miles, and the
    latitude correction is a linear ratio rather than a cosine — so it
    **divides by zero at the equator**, which is where Kenya is
    (docs/AUDIT.md §3, ADR-006).

    At Kenyan latitudes the cosine is around 0.996, so the correction is nearly
    a no-op. That is precisely why the bug survived: any plausible-looking test
    latitude produced a plausible-looking answer, and only the equator itself
    crashed.

    This is a box, not a circle. Corners are √2 times the stated radius, so
    callers filter the exact distance afterwards.
    """
    latitude_delta = radius_km / KM_PER_DEGREE_LATITUDE

    cos_latitude = math.cos(math.radians(latitude))
    if abs(cos_latitude) < 1e-12:
        # Within metres of a pole. Longitude is meaningless there; take the
        # whole range rather than dividing by ~0.
        longitude_delta = 180.0
    else:
        longitude_delta = radius_km / (KM_PER_DEGREE_LATITUDE * cos_latitude)

    return (
        max(latitude - latitude_delta, -90.0),
        min(latitude + latitude_delta, 90.0),
        max(longitude - longitude_delta, -180.0),
        min(longitude + longitude_delta, 180.0),
    )
