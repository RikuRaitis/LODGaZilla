"""Point limits for cards that draw every Nth sample instead of LOD envelopes."""

from __future__ import annotations

import math

import numpy as np


# Zero keeps every sample ("All").
SAMPLE_LIMIT_ALL = 0
SAMPLE_LIMIT_CHOICES = (10_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000, SAMPLE_LIMIT_ALL)


def decimation_step(sample_count: int, maximum_points: int) -> int:
    """Return the stride that keeps at most ``maximum_points`` samples."""
    if maximum_points <= SAMPLE_LIMIT_ALL:
        return 1
    return max(1, int(np.ceil(sample_count / maximum_points)))


def sample_limit_label(maximum_points: int) -> str:
    if maximum_points <= SAMPLE_LIMIT_ALL:
        return "All points"
    if maximum_points % 1_000_000 == 0:
        return f"{maximum_points // 1_000_000}M points"
    if maximum_points % 1_000 == 0:
        return f"{maximum_points // 1_000}k points"
    return f"{maximum_points} points"


class SampledResult(tuple):
    """A loader's result tuple that also reports how many samples it kept."""

    def __new__(cls, values, *, shown_points: int, available_points: int):  # type: ignore[no-untyped-def]
        result = super().__new__(cls, values)
        result.shown_points = int(shown_points)
        result.available_points = int(available_points)
        return result


def sample_counts(result) -> tuple[int, int] | None:  # type: ignore[no-untyped-def]
    """Return ``(shown, available)`` samples for one loaded graph, trace or track."""
    if isinstance(result, dict):
        shown, available = result.get("shown_points"), result.get("available_points")
    else:
        shown, available = getattr(result, "shown_points", None), getattr(result, "available_points", None)
    if shown is None or available is None:
        return None
    return int(shown), int(available)


def coverage_percent_text(shown: int, available: int) -> str:
    """Format a drawn share without rounding a partial view up to 100%."""
    if available <= 0 or shown >= available:
        return "100%"
    share = shown / available
    if share < 0.01:
        return "<1%"
    return f"{min(99, math.floor(share * 100))}%"


def estimated_shown(available: list[int], maximum_points: int) -> int:
    """Samples a limit would draw for graphs with these available counts."""
    if maximum_points <= SAMPLE_LIMIT_ALL:
        return sum(available)
    return sum(min(count, maximum_points) for count in available)
