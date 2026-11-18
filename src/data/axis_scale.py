"""Axis stretch for the 3D plot, and the factors that draw every axis at the same scale.

The 3D plot fits each axis into the same box, so a 2 km track that climbs 50 m looks as tall
as it is long. Equal scale works out a stretch per axis so that one metre is drawn the same
length on every axis. Latitude and longitude are measured in metres for this only; the data
and its labels stay in degrees.
"""
from __future__ import annotations

import math
import re
from typing import Sequence

# Axis roles found by the loader: a GNSS latitude or longitude series, or time.
ROLE_LATITUDE = "latitude"
ROLE_LONGITUDE = "longitude"
ROLE_TIME = "time"

# Each axis may be stretched this much either way.
MIN_STRETCH = 0.001
MAX_STRETCH = 20.0

# Metres per unit for the length units the importers use.
METRES_PER_UNIT = {
    "m": 1.0, "metre": 1.0, "metres": 1.0, "meter": 1.0, "meters": 1.0,
    "mm": 0.001, "cm": 0.01, "km": 1000.0, "ft": 0.3048, "feet": 0.3048,
}
# Metres per degree of latitude, and of longitude at the equator (WGS84, rounded).
METRES_PER_DEGREE_LATITUDE = 110_574.0
METRES_PER_DEGREE_LONGITUDE = 111_320.0
_DEGREE_UNIT = re.compile(r"(deg|degree|degrees|°|º)[nsew]?")


def is_degree_unit(unit: str) -> bool:
    """Degrees, however written: deg, degrees, °, º, also with N/S/E/W (deg N, °E); not degC."""
    return bool(_DEGREE_UNIT.fullmatch(unit.strip().casefold().replace(" ", "")))


def is_latitude_name(name: str) -> bool:
    return "latitude" in name.casefold() or name.casefold().endswith("/lat")


def is_longitude_name(name: str) -> bool:
    return "longitude" in name.casefold() or name.casefold().endswith("/lon")


def clamp_stretch(value: float) -> float:
    return max(MIN_STRETCH, min(MAX_STRETCH, float(value)))


def declared_coordinate_scale(*, unit: str = "", name: str = "", source: str = "") -> float | None:
    """The divisor that turns a stored GNSS coordinate into degrees, as its unit, name or source
    states; None when only its values can tell. Shared by the Map cards and Equal scale."""
    unit = unit.strip().casefold()
    leaf = name.rsplit("/", 1)[-1].casefold()
    if is_degree_unit(unit):
        return 1.0
    if unit in {"rad", "radian", "radians"} or (not unit and leaf.endswith("_rad")):
        return math.pi / 180.0
    # Fixed-point degrees, as some loggers store them: "raw" is degrees x 10^7.
    if unit in {"raw", "e7", "deg*1e7", "degrees_e7"} or (not unit and source == "aux.gps"):
        return 10_000_000.0
    if unit in {"deg*10", "degrees_x10"}:
        return 10.0
    if not unit and leaf.endswith("_deg"):
        return 1.0
    return None


def coordinate_scale(largest: float, maximum_degrees: float, *, unit: str = "", name: str = "",
                     source: str = "") -> float:
    """The one divisor that turns a whole coordinate series into degrees.

    A stated unit wins; otherwise the series' largest magnitude decides: within the
    coordinate's limit it is in degrees, within ten times it is degrees x 10, beyond that
    degrees x 10^7.
    """
    declared = declared_coordinate_scale(unit=unit, name=name, source=source)
    if declared is not None:
        return declared
    if not math.isfinite(largest) or largest <= maximum_degrees:
        return 1.0
    if largest <= maximum_degrees * 10:
        return 10.0
    return 10_000_000.0


def geographic_roles(units: Sequence[str], roles: Sequence[str]) -> list[str]:
    """The roles with latitude and longitude estimated from the units.

    Two axes in degrees with no other role are taken as a latitude and a longitude even
    when their names do not say so: a recognised name decides which is which, otherwise the
    later axis is the latitude (X longitude and Y latitude, as on a map). A lone recognised
    latitude or longitude in degrees keeps its role.
    """
    roles = list(roles)
    degrees = [index for index, unit in enumerate(units) if is_degree_unit(unit) and roles[index] != ROLE_TIME]
    if len(degrees) == 2:
        first, second = degrees
        if ROLE_LATITUDE in (roles[first], roles[second]):
            latitude = first if roles[first] == ROLE_LATITUDE else second
        elif ROLE_LONGITUDE in (roles[first], roles[second]):
            latitude = second if roles[first] == ROLE_LONGITUDE else first
        else:
            latitude = second
        roles[latitude] = ROLE_LATITUDE
        roles[first if latitude == second else second] = ROLE_LONGITUDE
    return roles


def equal_scale_factors(low: Sequence[float], high: Sequence[float], units: Sequence[str],
                        roles: Sequence[str]) -> tuple[tuple[float, float, float] | None, str]:
    """The stretch of X, Y and Z that draws one metre the same length on each axis.

    ``low`` and ``high`` are the axes' data ranges, ``units`` and ``roles`` what the loader
    found for each (the same on every trace). Latitude and longitude, recognised by name or
    estimated from two axes in degrees (``geographic_roles``), count in metres. Returns the
    factors and an empty reason, or None and the reason Equal scale cannot apply, naming the axis.
    """
    roles = geographic_roles(units, roles)
    latitude = next((index for index, role in enumerate(roles) if role == ROLE_LATITUDE), None)
    metres_per_unit = []
    for index, (name, unit, role) in enumerate(zip("XYZ", units, roles)):
        unit_key = unit.strip().casefold()
        if role in (ROLE_LATITUDE, ROLE_LONGITUDE):
            # Stored degrees may be fixed-point (raw = degrees x 10^7), as the Map cards read them.
            limit = 90.0 if role == ROLE_LATITUDE else 180.0
            degrees_per_unit = 1.0 / coordinate_scale(max(abs(low[index]), abs(high[index])), limit, unit=unit)
            if role == ROLE_LATITUDE:
                metres_per_unit.append(METRES_PER_DEGREE_LATITUDE * degrees_per_unit)
                continue
            if latitude is None:
                return None, f"{name} is a longitude, which needs a latitude axis"
            latitude_scale = coordinate_scale(max(abs(low[latitude]), abs(high[latitude])), 90.0,
                                              unit=units[latitude])
            mean_latitude = (low[latitude] + high[latitude]) / 2 / latitude_scale
            metres_per_unit.append(METRES_PER_DEGREE_LONGITUDE * math.cos(math.radians(mean_latitude))
                                   * degrees_per_unit)
        elif role == ROLE_TIME:
            return None, f"{name} is time, not a distance"
        elif unit_key in METRES_PER_UNIT:
            metres_per_unit.append(METRES_PER_UNIT[unit_key])
        else:
            return None, f"{name} is in {unit or 'no unit'}, not a distance"
    spans = [max(0.0, (high[index] - low[index]) * metres_per_unit[index]) for index in range(3)]
    largest = max(spans)
    if largest <= 0:
        return None, "The data has no extent"
    return tuple(clamp_stretch(span / largest) for span in spans), ""  # type: ignore[return-value]
