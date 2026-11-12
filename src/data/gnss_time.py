"""Consistent timestamp outlier handling for GNSS importers."""

from typing import Any

import numpy as np


def filter_gnss_time_outliers(series: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Keep the largest timestamp cluster separated by gaps of at most 24h.

    Count distinct timestamps across all channels so repeated coordinates and
    differing channel rates do not give one epoch extra votes. Equal-size
    clusters prefer the earliest cluster. A continuous multi-day track stays
    intact. Apply the same bounds to every channel and return skipped sample
    counts per channel. Sort retained samples for HDF5 binary searches.
    """
    if not series:
        return {}
    timestamps = np.unique(np.concatenate([item["time"] for item in series.values()]))
    timestamps = timestamps[np.isfinite(timestamps)]
    if not timestamps.size:
        skipped = {key: len(item["time"]) for key, item in series.items()}
        series.clear()
        return skipped
    boundaries = np.r_[0, np.flatnonzero(np.diff(timestamps) > 24 * 60 * 60) + 1, len(timestamps)]
    largest = int(np.argmax(np.diff(boundaries)))
    lower = timestamps[boundaries[largest]]
    upper = timestamps[boundaries[largest + 1] - 1]
    skipped = {}
    for key, item in list(series.items()):
        times = np.asarray(item["time"])
        keep = np.flatnonzero((times >= lower) & (times <= upper))
        skipped[key] = len(times) - len(keep)
        if not len(keep):
            del series[key]
            continue
        keep = keep[np.argsort(times[keep], kind="stable")]
        item["time"] = times[keep]
        item["values"] = np.asarray(item["values"])[keep]
        item["time_sorted"] = True
    return skipped
