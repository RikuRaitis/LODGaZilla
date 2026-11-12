"""Cumulative track distances shared by the GNSS importers."""

from typing import Any, Mapping

import numpy as np

from src.data.parser_base import DataParser


EARTH_RADIUS_M = 6_371_008.8


def _unique_samples(series: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Sort finite samples, keeping the last report at each timestamp."""
    times = np.asarray(series["time"], dtype=np.float64)
    values = np.asarray(series["values"], dtype=np.float64)
    valid = np.isfinite(times) & np.isfinite(values)
    times, values = times[valid], values[valid]
    unique_times, reverse_indices = np.unique(times[::-1], return_index=True)
    return unique_times, values[::-1][reverse_indices]


def add_gnss_distance_series(
    parser: DataParser,
    series: dict[str, dict[str, Any]],
    *,
    segment_ids: Mapping[float, int] | None = None,
) -> None:
    """Add distance in metres after timestamp outliers have been removed.

    Use one position per epoch, matching latitude, longitude and altitude by
    timestamp. Horizontal legs use the haversine great-circle distance on a
    mean-radius Earth; 3D legs combine that distance with the elevation change.
    When either endpoint has no elevation, use only the horizontal distance
    for that leg. Segment boundaries contribute no distance to either total.
    """
    latitude = series.get("GNSS/latitude_deg")
    longitude = series.get("GNSS/longitude_deg")
    if latitude is None or longitude is None:
        return
    lat_times, latitudes = _unique_samples(latitude)
    lon_times, longitudes = _unique_samples(longitude)
    times, lat_indices, lon_indices = np.intersect1d(
        lat_times, lon_times, assume_unique=True, return_indices=True,
    )
    latitudes, longitudes = latitudes[lat_indices], longitudes[lon_indices]
    valid = (np.abs(latitudes) <= 90) & (np.abs(longitudes) <= 180)
    times, latitudes, longitudes = times[valid], latitudes[valid], longitudes[valid]
    if not times.size:
        return

    latitudes, longitudes = np.radians(latitudes), np.radians(longitudes)
    haversine = (
        np.sin(np.diff(latitudes) / 2) ** 2
        + np.cos(latitudes[:-1]) * np.cos(latitudes[1:])
        * np.sin(np.diff(longitudes) / 2) ** 2
    )
    horizontal = 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))

    elevations = np.full(times.shape, np.nan)
    altitude = series.get("GNSS/altitude_m")
    if altitude is not None:
        alt_times, altitudes = _unique_samples(altitude)
        _, track_indices, alt_indices = np.intersect1d(
            times, alt_times, assume_unique=True, return_indices=True,
        )
        elevations[track_indices] = altitudes[alt_indices]
    vertical = np.diff(elevations)
    vertical[~np.isfinite(vertical)] = 0
    spatial = np.hypot(horizontal, vertical)

    if segment_ids is not None:
        segments = np.array([segment_ids[timestamp] for timestamp in times])
        boundaries = segments[:-1] != segments[1:]
        horizontal[boundaries] = 0
        spatial[boundaries] = 0

    for name, steps, description in (
        ("distance_2d_m", horizontal, "Cumulative 2D distance; altitude ignored"),
        ("distance_3d_m", spatial,
         "Cumulative 3D distance; horizontal only where endpoint altitude is missing"),
    ):
        parser.add_series(
            series, name, "GNSS", times, np.r_[0.0, np.cumsum(steps)],
            source="GNSS track coordinates", description=description,
        )
