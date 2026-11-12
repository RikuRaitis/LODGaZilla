"""Windowed, one-sided peak-amplitude spectra computed in memory with NumPy."""

from dataclasses import dataclass
import math

import numpy as np


FFT_WINDOW_SIZES = tuple(1 << power for power in range(6, 19))


@dataclass(frozen=True)
class FFTSettings:
    window_size: int = 2048
    overlap: float = 0.5
    window: str = "Hann"
    remove_mean: bool = True
    decibels: bool = True
    dynamic_range_db: int = 80
    frequency_min_hz: float = 0.0
    frequency_max_hz: float | None = None
    target_bin_hz: float | None = None

    def __post_init__(self):
        if self.window_size not in FFT_WINDOW_SIZES:
            raise ValueError("FFT window size must be a power of two from 64 to 262144.")
        if self.overlap not in (0.0, 0.5, 0.75, 0.875):
            raise ValueError("Unsupported FFT overlap.")
        if self.window not in ("Hann", "Hamming", "Rectangular"):
            raise ValueError("Unsupported FFT window function.")
        if not 20 <= self.dynamic_range_db <= 160:
            raise ValueError("FFT colour range must be between 20 and 160 dB.")
        if not math.isfinite(self.frequency_min_hz) or self.frequency_min_hz < 0:
            raise ValueError("Minimum frequency must be finite and non-negative.")
        if self.frequency_max_hz is not None and (
            not math.isfinite(self.frequency_max_hz) or self.frequency_max_hz <= self.frequency_min_hz
        ):
            raise ValueError("Maximum frequency must be greater than minimum frequency.")
        if self.target_bin_hz is not None and (
            not math.isfinite(self.target_bin_hz) or self.target_bin_hz <= 0
        ):
            raise ValueError("Target FFT bin spacing must be finite and positive.")


def requested_window_size(sample_rate: float, settings: FFTSettings) -> int:
    """Choose actual input samples, without zero-padding for cosmetic extra bins."""
    if settings.target_bin_hz is None:
        return settings.window_size
    return next((size for size in FFT_WINDOW_SIZES if sample_rate / size <= settings.target_bin_hz),
                FFT_WINDOW_SIZES[-1])


def amplitude_spectrum(values: np.ndarray, settings: FFTSettings) -> np.ndarray:
    """Return peak amplitude in the input units, correcting for window gain."""
    values = np.asarray(values, dtype=np.float64)
    size = values.shape[-1]
    if size < 2:
        raise ValueError("At least two samples are needed for an FFT.")
    if settings.window == "Hann":
        window = np.hanning(size + 1)[:-1]
    elif settings.window == "Hamming":
        window = np.hamming(size + 1)[:-1]
    else:
        window = np.ones(size)
    if settings.remove_mean:
        values = values - np.mean(values, axis=-1, keepdims=True)
    amplitude = np.abs(np.fft.rfft(values * window, axis=-1)) / window.sum()
    amplitude[..., 1:-1 if size % 2 == 0 else None] *= 2
    return amplitude


def colour_values(amplitude: np.ndarray, settings: FFTSettings) -> tuple[np.ndarray, tuple[float, float]]:
    """Convert amplitude to colour values; NaN windows remain transparent gaps."""
    if settings.decibels:
        values = 20 * np.log10(np.maximum(amplitude, 1e-15))
        upper = float(np.nanmax(values))
        if float(np.nanmax(amplitude)) <= 1e-15:
            upper = 0.0
        return values, (upper - settings.dynamic_range_db, upper)
    upper = max(float(np.nanmax(amplitude)), 1e-15)
    return amplitude, (0.0, upper)
