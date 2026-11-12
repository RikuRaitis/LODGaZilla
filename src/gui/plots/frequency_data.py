"""Bounded raw HDF5 reads for an on-demand spectrogram; no cache writes."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event

import h5py
import numpy as np

from src.data.frequency import FFTSettings, amplitude_spectrum, requested_window_size
from src.gui.plots.series import (
    TimeAxisMetadata, _attribute_text, _canonical_time_values, _canonical_to_native_range,
    _h5_searchsorted, _series_group_for_navigator_name, _time_axis_metadata_from_group,
    _visible_sample_bounds,
)


MAX_TIME_COLUMNS = 1024
MAX_IMAGE_CELLS = 2_097_152


@dataclass(frozen=True)
class FrequencyData:
    amplitude: np.ndarray
    # Image edges in the same canonical time coordinates as the other cards.
    time_edges: tuple[float, float]
    full_time_range: tuple[float, float]
    sample_rate: float
    window_size: int
    metadata: TimeAxisMetadata
    unit: str
    window_stride: int
    skipped_windows: int
    first_frequency_hz: float = 0.0
    frequency_range: tuple[float, float] | None = None

    def strongest_signal(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return time centres, peak-bin frequencies and linear amplitudes.

        Only bin centres inside the selected band participate. Missing or silent
        windows have no peak; NaNs keep their gaps in the overlay and readout.
        """
        rows, columns = self.amplitude.shape
        times = self.time_edges[0] + (np.arange(columns) + 0.5) * (
            (self.time_edges[1] - self.time_edges[0]) / columns
        )
        frequencies = self.first_frequency_hz + np.arange(rows) * self.sample_rate / self.window_size
        band = self.frequency_range or (0, self.sample_rate / 2)
        inside = (frequencies >= band[0]) & (frequencies <= band[1])
        peak_frequencies = np.full(columns, np.nan)
        peak_amplitudes = np.full(columns, np.nan)
        if np.any(inside):
            values = self.amplitude[inside]
            valid = np.isfinite(values) & (values > 0)
            indices = np.argmax(np.where(valid, values, -np.inf), axis=0)
            present = valid.any(axis=0)
            peak_frequencies[present] = frequencies[inside][indices[present]]
            peak_amplitudes[present] = values[indices[present], np.flatnonzero(present)]
        return times, peak_frequencies, peak_amplitudes


def load_frequency_data(
    path: Path, series_name: str, settings: FFTSettings,
    time_range: tuple[float, float] | None = None, cancelled: Event | None = None,
) -> FrequencyData | None:
    """Read complete FFT windows, never the min/max LOD data or decimated samples.

    Overview images limit the number of windows, not the sampling rate within
    a window. A regular time grid keeps gaps in their true horizontal positions.
    Zooming requests denser windows. Missing or irregular windows stay blank.
    """
    with h5py.File(path, "r") as cache:
        group = _series_group_for_navigator_name(cache, series_name)
        metadata = _time_axis_metadata_from_group(cache, group)
        times = group[_attribute_text(group.attrs.get("time_dataset", "time_unix"))]
        values = group["value"]
        if len(times) < 16 or len(times) != len(values):
            raise ValueError("Frequency plots need at least 16 timestamped samples.")
        native_range = _canonical_to_native_range(time_range, metadata)
        start, stop = _visible_sample_bounds(times, native_range)
        if stop - start < 16:
            raise ValueError("Fewer than 16 samples in this time range. Zoom out.")
        probe = np.asarray(times[start:min(stop, start + 8192)], dtype=float)
        dt, quantum = _sample_interval(probe)
        if metadata.seconds_per_unit <= 0:
            raise ValueError("Invalid time unit for FFT.")
        sample_rate = 1.0 / (dt * metadata.seconds_per_unit)
        size = min(requested_window_size(sample_rate, settings), 2 ** ((stop - start).bit_length() - 1))
        df = sample_rate / size
        frequency_max = min(sample_rate / 2, settings.frequency_max_hz or sample_rate / 2)
        if settings.frequency_min_hz >= frequency_max:
            raise ValueError(f"Selected frequency band is above Nyquist ({sample_rate / 2:g} Hz).")
        # Include the edge bins covering the requested band, with their true
        # frequency coordinates. Only this band occupies the image budget.
        first_bin = max(0, int(np.floor(settings.frequency_min_hz / df)))
        last_bin = min(size // 2, int(np.ceil(frequency_max / df)))
        bin_count = last_bin - first_bin + 1
        hop = max(1, round(size * (1 - settings.overlap)))
        first, last = float(times[start]), float(times[stop - 1])
        if not np.isfinite(first + last) or last <= first:
            raise ValueError("Frequency plots require increasing, regularly sampled timestamps.")
        frames = max(1, (round((last - first) / dt) + 1 - size) // hop + 1)
        limit = max(1, min(MAX_TIME_COLUMNS, MAX_IMAGE_CELLS // bin_count))
        stride = max(1, int(np.ceil(frames / limit)))
        positions = np.arange(0, frames, stride, dtype=np.int64)
        amplitude = np.full((bin_count, len(positions)), np.nan, dtype=np.float32)
        skipped = 0
        # Account for timestamp quantization: float precision (particularly
        # Unix seconds) and integer timestamp steps such as 9/10 ms at 104 Hz.
        tolerance = max(dt * 0.02, min(quantum, dt * 0.5), abs(np.spacing(max(abs(first), abs(last)))) * 4)
        for column, position in enumerate(positions):
            if cancelled is not None and cancelled.is_set():
                return None
            expected = first + int(position) * hop * dt
            index = _nearest_sample(times, expected, start, stop)
            if index + size > stop:
                skipped += 1
                continue
            window_times = np.asarray(times[index:index + size], dtype=float)
            samples = np.asarray(values[index:index + size], dtype=float)
            if (
                abs(window_times[0] - expected) > max(dt * 0.6, tolerance)
                or not np.all(np.isfinite(window_times))
                or not np.all(np.isfinite(samples))
                or not np.all(np.diff(window_times) > 0)
                or np.any(np.abs(np.diff(window_times) - dt) > tolerance)
            ):
                skipped += 1
                continue
            amplitude[:, column] = amplitude_spectrum(samples, settings)[first_bin:last_bin + 1]
        if not np.any(np.isfinite(amplitude)):
            raise ValueError("No valid FFT windows: data contain gaps or are not regularly sampled.")
        step = hop * stride * dt
        center = first + (size - 1) * dt / 2
        edges = _canonical_time_values(np.array([center - step / 2, center + (len(positions) - 0.5) * step]), metadata)
        full = _canonical_time_values(np.array([times[0], times[-1]], dtype=float), metadata)
        return FrequencyData(
            amplitude, tuple(edges), tuple(full), sample_rate, size, metadata,
            _attribute_text(group.attrs.get("unit", "")), stride, skipped,
            first_bin * df, (settings.frequency_min_hz, frequency_max),
        )


def _sample_interval(probe: np.ndarray) -> tuple[float, float]:
    """Estimate the mean sample interval and the timestamp quantum.

    The median step of quantised timestamps (9, 10, 10, 9 … ms at 104 Hz) is
    one quantum off, so average the regular steps over the whole probe span
    instead. Steps far from the median are timing gaps and are excluded.
    """
    differences = np.diff(probe)
    positive = differences[np.isfinite(differences) & (differences > 0)]
    if not len(positive):
        raise ValueError("Frequency plots require increasing, regularly sampled timestamps.")
    median = float(np.median(positive))
    regular = positive[(positive > median * 0.5) & (positive < median * 1.5)]
    if not len(regular):
        regular = positive
    dt = float(regular.sum() / len(regular))
    steps = np.unique(regular)
    quantum = float(np.min(np.diff(steps))) if len(steps) > 1 else 0.0
    return dt, quantum


def _nearest_sample(times, expected: float, start: int, stop: int) -> int:
    """Return the sample index within [start, stop) nearest ``expected``."""
    index = min(max(start, _h5_searchsorted(times, expected, side="left")), stop - 1)
    if index > start and abs(float(times[index - 1]) - expected) <= abs(float(times[index]) - expected):
        return index - 1
    return index
