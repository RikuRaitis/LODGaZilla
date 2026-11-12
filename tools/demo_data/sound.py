"""The camera soundtrack: footsteps, breathing, the forest, the stream and the watch's lap beeps."""
from __future__ import annotations


import numpy as np

from tools.demo_data.course import Scenery, Track

SAMPLE_RATE = 48_000


def band_noise(rng, count: int, low: float, high: float) -> np.ndarray:  # type: ignore[no-untyped-def]
    """White noise limited to a frequency band, normalised to unit RMS."""
    spectrum = np.fft.rfft(rng.normal(0, 1, count))
    frequencies = np.fft.rfftfreq(count, 1 / SAMPLE_RATE)
    spectrum[(frequencies < low) | (frequencies > high)] = 0
    noise = np.fft.irfft(spectrum, count)
    return noise / max(1e-9, float(np.sqrt(np.mean(noise ** 2))))


def soundtrack(track: Track, scenery: Scenery, start: float, end: float, seed: int = 3) -> np.ndarray:
    """Stereo float32 samples (2, N) for log times start..end."""
    rng = np.random.default_rng(seed)
    count = int(round((end - start) * SAMPLE_RATE))
    t = start + np.arange(count) / SAMPLE_RATE
    left, right = np.zeros(count), np.zeros(count)

    # The forest: wind with slow gusts, and birds.
    wind = band_noise(rng, count, 40, 900) * (0.012 + 0.008 * np.sin(2 * np.pi * t / 7.3) ** 2)
    left += wind
    right += np.roll(wind, 900)
    for chirp_start in np.sort(rng.uniform(start, end, int((end - start) * 0.8))):
        pan = rng.uniform(0, 1)
        for note in range(int(rng.integers(2, 5))):
            onset = chirp_start + note * 0.13
            length = 0.07
            index = int((onset - start) * SAMPLE_RATE)
            n = int(length * SAMPLE_RATE)
            if index < 0 or index + n >= count:
                continue
            local = np.arange(n) / SAMPLE_RATE
            frequency = rng.uniform(3000, 4800) + 1800 * local / length
            tone = 0.02 * np.sin(2 * np.pi * np.cumsum(frequency) / SAMPLE_RATE) * np.sin(np.pi * local / length)
            left[index:index + n] += tone * (1 - pan)
            right[index:index + n] += tone * pan

    # The stream gets louder as the runner approaches it.
    if scenery.streams:
        water = band_noise(rng, count, 400, 4000)
        position = np.column_stack((track.at("x", t[::480]), track.at("y", t[::480])))
        distance = np.min([np.hypot(position[:, 0] - x, position[:, 1] - y) for x, y, _ in scenery.streams], axis=0)
        level = np.interp(t, t[::480], 0.08 / (1 + distance / 4))
        left += water * level
        right += np.roll(water, 1500) * level

    # Breathing: in and out over four steps, stronger when running hard.
    speed = track.at("speed", t)
    breath_phase = track.at("gait", t) / 4.0
    breath = band_noise(rng, count, 300, 2500) * np.clip(np.sin(2 * np.pi * breath_phase), 0, None) ** 2
    breath *= 0.01 + 0.012 * np.clip(speed - 2, 0, 3)
    left += breath
    right += breath

    # Footsteps: a thud and leaf crunch, a hollow knock on the bridge, a heavy landing after a jump.
    landings = np.array([jump.landing for jump in track.spec.jumps])
    bridge = track.spec.bridge
    for time, foot in zip(track.step_times, track.step_feet):
        if time < start or time >= end - 0.3:
            continue
        index = int((time - start) * SAMPLE_RATE)
        n = int(0.25 * SAMPLE_RATE)
        local = np.arange(n) / SAMPLE_RATE
        landing = len(landings) and np.min(np.abs(landings - time)) < 0.02
        on_bridge = bridge is not None and bridge[0] <= time <= bridge[1]
        gain = 2.2 if landing else 1.0
        thud = np.sin(2 * np.pi * (85 - 30 * local) * local) * np.exp(-local / 0.05)
        if on_bridge:
            sound = 0.25 * (np.sin(2 * np.pi * 190 * local) + 0.6 * np.sin(2 * np.pi * 410 * local)) * np.exp(-local / 0.09)
        else:
            sound = 0.18 * thud + 0.07 * rng.normal(0, 1, n) * np.exp(-local / 0.03) * (local < 0.12)
        sound *= gain
        pan = 0.75 if foot == 1 else 0.25
        left[index:index + n] += sound * pan
        right[index:index + n] += sound * (1 - pan)

    # The watch, on the left wrist, beeps at each lap press: the cue for synchronising the video.
    for lap in track.spec.laps:
        index = int((lap - start) * SAMPLE_RATE)
        n = int(0.12 * SAMPLE_RATE)
        if 0 <= index and index + n < count:
            local = np.arange(n) / SAMPLE_RATE
            beep = 0.18 * np.sin(2 * np.pi * 2600 * local) * np.minimum(1, np.minimum(local, 0.12 - local) / 0.005)
            left[index:index + n] += beep
            right[index:index + n] += 0.5 * beep
    stereo = np.stack((left, right))
    peak = float(np.max(np.abs(stereo)))
    if peak > 0.95:
        stereo *= 0.95 / peak
    return stereo.astype(np.float32)

