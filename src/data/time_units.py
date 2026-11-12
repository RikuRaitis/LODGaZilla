"""Time units of H5 time datasets: the one table the writer, the Navigator and the cards use."""

TIME_UNIT_SECONDS = {
    "ns": 1e-9, "nanosecond": 1e-9, "nanoseconds": 1e-9,
    "us": 1e-6, "µs": 1e-6, "microsecond": 1e-6, "microseconds": 1e-6,
    "ms": 1e-3, "millisecond": 1e-3, "milliseconds": 1e-3,
    "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
}


def seconds_per_time_unit(unit: str) -> float:
    """Seconds in one ``unit`` ("ms", "milliseconds" and so on); unknown units count as seconds."""
    return TIME_UNIT_SECONDS.get(str(unit).strip().casefold(), 1.0)
