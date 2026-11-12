"""Simulated watch and foot pod series for a track.

Times are seconds from the session's log start. The watch is on the left
wrist: sensor x points along the forearm toward the hand, z out of the watch
face and y completes the right-handed frame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from tools.demo_data.course import PHASE_LABELS, Track

GRAVITY = 9.80665
IMU_RATE_HZ = 1000
ACCEL_RANGE_G = 8.0
GYRO_RANGE_DPS = 1000.0
MAG_RANGE_UT = 100.0
# Earth's field in southern Finland (east, north, up), microtesla.
EARTH_FIELD_UT = np.array([1.5, 15.0, -49.5])
FOOT_POD_CLOCK_ERROR_S = 1.7
STEP_LABELS = {"1": "Left foot", "2": "Right foot"}
JUMP_LABELS = {"1": "Take-off", "2": "Landing"}
FIX_LABELS = {"0": "No fix", "1": "2D fix", "2": "3D fix"}


@dataclass
class Series:
    group: str
    name: str
    unit: str
    description: str
    time: np.ndarray  # seconds from log start
    values: np.ndarray
    kind: str = ""  # "event", "state" or "" for numeric data
    labels: dict[str, str] = field(default_factory=dict)  # meaning of event/state values
    max_interval_ms: float | None = None  # longer gaps are sample loss
    value_range: tuple[float, float] | None = None  # sensor limits; reaching them is clipping
    visible: bool = True
    attributes: dict[str, str] = field(default_factory=dict)  # sensor, nominal rate, axis...

    @property
    def key(self) -> str:
        return f"{self.group}/{self.name}"


def watch_series(track: Track, *, full: bool = True, seed: int = 1) -> list[Series]:
    """Every watch series; ``full=False`` keeps a lighter set for very long sessions."""
    rng = np.random.default_rng(seed)
    duration = track.spec.duration
    series = imu_series(track, rng, gyro_and_mag=full)
    series += compass_series(track, rng) if full else []
    series += gnss_series(track, rng)
    series += barometer_series(track, rng)
    series += heart_rate_series(track, rng, ppg=full)
    series += activity_series(track, rng, steps=True)
    series += system_series(duration, rng)
    return series


def imu_times(track: Track, rng) -> np.ndarray:  # type: ignore[no-untyped-def]
    t = np.arange(0, round(track.spec.duration * IMU_RATE_HZ)) / IMU_RATE_HZ
    keep = np.ones(len(t), dtype=bool)
    for start, end in track.spec.imu_gaps:
        keep &= ~((t >= start) & (t < end))
    t = t[keep]
    # Timestamps jitter slightly around the 1 ms grid; the first one is the log start.
    jitter = rng.uniform(-2e-5, 2e-5, len(t))
    jitter[0] = 0.0
    return t + jitter


def imu_series(track: Track, rng, *, gyro_and_mag: bool = True, chunk_seconds: float = 300.0) -> list[Series]:  # type: ignore[no-untyped-def]
    times = imu_times(track, rng)
    parts: dict[str, list[np.ndarray]] = {key: [] for key in ("acc", "gyro", "mag")}
    step = int(chunk_seconds * IMU_RATE_HZ)
    for start in range(0, len(times), step):
        chunk = times[start:start + step]
        if len(chunk):
            acc, gyro, mag = _imu_chunk(track, chunk, rng, gyro_and_mag)
            parts["acc"].append(acc)
            parts["gyro"].append(gyro)
            parts["mag"].append(mag)
    acc, gyro, mag = (np.concatenate(parts[key]) for key in ("acc", "gyro", "mag"))
    gaps = ", ".join(f"{a:.3f}-{b:.3f} s" for a, b in track.spec.imu_gaps)
    common = {"nominal_sample_rate_hz": str(IMU_RATE_HZ), "mounting": "left wrist",
              "axes": "x along the forearm toward the hand, z out of the watch face, y = z cross x",
              "known_sample_loss": f"FIFO overflows at {gaps}"}
    result = []
    for index, axis in enumerate("XYZ"):
        result.append(Series(
            "Accelerometer", axis, "g", f"Specific force along sensor {axis.lower()}", times, acc[:, index],
            max_interval_ms=1.5, value_range=(-ACCEL_RANGE_G, ACCEL_RANGE_G),
            attributes={**common, "sensor": "Demo IMU accelerometer", "measurement_range": f"+-{ACCEL_RANGE_G:g} g",
                        "noise_density": "0.008 g rms"}))
    if gyro_and_mag:
        for index, axis in enumerate("XYZ"):
            result.append(Series(
                "Gyroscope", axis, "dps", f"Angular rate about sensor {axis.lower()}", times, gyro[:, index],
                max_interval_ms=1.5, value_range=(-GYRO_RANGE_DPS, GYRO_RANGE_DPS),
                attributes={**common, "sensor": "Demo IMU gyroscope", "measurement_range": f"+-{GYRO_RANGE_DPS:g} dps",
                            "noise_density": "0.3 dps rms"}))
        for index, axis in enumerate("XYZ"):
            result.append(Series(
                "Magnetometer", axis, "uT", f"Magnetic field along sensor {axis.lower()}", times, mag[:, index],
                max_interval_ms=1.5, value_range=(-MAG_RANGE_UT, MAG_RANGE_UT),
                attributes={**common, "sensor": "Demo magnetometer", "measurement_range": f"+-{MAG_RANGE_UT:g} uT",
                            "hard_iron_offset_uT": "4, -2, 6",
                            "note": "A steel bridge saturates the magnetometer while crossing it"}))
    return result


def _imu_chunk(track: Track, t: np.ndarray, rng, gyro_and_mag: bool):  # type: ignore[no-untyped-def]
    n = len(t)
    heading = track.heading_at(t)
    speed = track.at("speed", t)
    yaw_rate = track.at("yaw_rate", t)
    gait = track.at("gait", t)
    sin_h, cos_h = np.sin(heading), np.cos(heading)
    forward = np.column_stack((sin_h, cos_h, np.zeros(n)))
    left = np.column_stack((-cos_h, sin_h, np.zeros(n)))
    up = np.tile([0.0, 0.0, 1.0], (n, 1))
    moving = speed > 0.4
    # Arm swing: one period per two steps, larger when running faster.
    amplitude = np.where(moving, 0.42 + 0.08 * (speed - 3.0), 0.0)
    swing = 0.25 + amplitude * np.sin(np.pi * gait)
    swing_rate = np.gradient(swing, t)
    swing_acc = np.gradient(swing_rate, t)
    ca, sa = np.cos(swing)[:, None], np.sin(swing)[:, None]
    x_axis = ca * forward + sa * up
    z0 = -sa * forward + ca * up
    roll = 0.5
    z_axis = math.cos(roll) * z0 + math.sin(roll) * left
    y_axis = np.cross(z_axis, x_axis)
    rotation = np.stack((x_axis, y_axis, z_axis), axis=1)  # rows are sensor axes in world

    # Vertical body acceleration from the footstrikes; free fall in flight.
    steps = track.step_times
    index = np.clip(np.searchsorted(steps, t, side="right") - 1, 0, max(0, len(steps) - 1))
    since = t - steps[index] if len(steps) else np.full(n, 10.0)
    period = np.where(moving, 60.0 / np.maximum(track.at("cadence", t), 1.0), 1.0)
    stance = 0.38 * period
    landing_times = np.array([jump.landing for jump in track.spec.jumps])
    near_landing = np.zeros(n, dtype=bool)
    for landing in landing_times:
        near_landing |= (t >= landing) & (t < landing + 0.3)
    peak = np.where(near_landing, 7.0, math.pi / 0.76)
    vertical = np.where(since < stance, GRAVITY * (peak * np.sin(np.pi * since / stance) - 1.0), -GRAVITY)
    vertical = np.where(moving | track.airborne(t), 0.6 * vertical, 0.0)
    acceleration = vertical[:, None] * up
    acceleration += np.gradient(speed, t)[:, None] * forward
    acceleration -= (speed * yaw_rate)[:, None] * left
    arm = 0.33
    acceleration += arm * ((-swing_acc * np.sin(swing) - swing_rate ** 2 * np.cos(swing))[:, None] * forward
                           + (swing_acc * np.cos(swing) - swing_rate ** 2 * np.sin(swing))[:, None] * up)
    specific_force = acceleration + GRAVITY * up
    acc = np.einsum("nij,nj->ni", rotation, specific_force) / GRAVITY
    impact = np.where(moving & (since < 0.12), 0.5 * np.exp(-since / 0.02) * np.sin(2 * np.pi * 28 * since), 0.0)
    acc[:, 2] += impact
    gyro = np.zeros((n, 3))
    mag = np.zeros((n, 3))
    for landing in landing_times:
        tau = t - landing
        pulse = np.where((tau >= 0) & (tau < 0.4), np.exp(-np.clip(tau, 0, None) / 0.035)
                         * np.cos(2 * np.pi * 22 * np.clip(tau, 0, None)), 0.0)
        acc += 14.0 * pulse[:, None] * np.array([0.9, -0.75, 1.0])
        flail = np.where((tau >= -0.05) & (tau < 0.35), np.exp(-np.abs(tau) / 0.06), 0.0)
        gyro += 1400.0 * flail[:, None] * np.array([1.0, -0.85, 0.9])
    if track.spec.bridge is not None:
        start, end = track.spec.bridge
        on_bridge = (t >= start) & (t < end)
        acc[:, 2] += np.where(on_bridge, 0.6 * np.sin(2 * np.pi * 50.0 * t), 0.0)
        acc[:, 0] += np.where(on_bridge, 0.2 * np.sin(2 * np.pi * 50.0 * t + 0.7), 0.0)
    acc += rng.normal(0.0, 0.008, (n, 3)) + np.array([0.012, -0.006, 0.009])
    acc = np.clip(acc, -ACCEL_RANGE_G, ACCEL_RANGE_G)
    if gyro_and_mag:
        # A clockwise heading change is a rotation about -up; the swing rotates about -left.
        omega = (-yaw_rate)[:, None] * up - swing_rate[:, None] * left
        gyro += np.degrees(np.einsum("nij,nj->ni", rotation, omega))
        gyro += rng.normal(0.0, 0.3, (n, 3)) + np.array([0.4, -0.2, 0.15])
        gyro = np.clip(gyro, -GYRO_RANGE_DPS, GYRO_RANGE_DPS)
        mag = np.einsum("nij,j->ni", rotation, EARTH_FIELD_UT) + np.array([4.0, -2.0, 6.0])
        mag += bridge_disturbance(track, t)[:, None] * np.array([130.0, -125.0, 115.0])
        mag += rng.normal(0.0, 0.15, (n, 3))
        mag = np.clip(mag, -MAG_RANGE_UT, MAG_RANGE_UT)
    return acc.astype(np.float32), gyro.astype(np.float32), mag.astype(np.float32)


def bridge_disturbance(track: Track, t: np.ndarray) -> np.ndarray:
    if track.spec.bridge is None:
        return np.zeros(len(t))
    middle = sum(track.spec.bridge) / 2
    return np.exp(-((t - middle) / 0.9) ** 2)


def compass_series(track: Track, rng) -> list[Series]:  # type: ignore[no-untyped-def]
    t = np.arange(0, round(track.spec.duration * 25)) / 25
    heading = np.degrees(track.heading_at(t))
    heading += 3.0 * np.sin(np.pi * track.at("gait", t)) + rng.normal(0.0, 1.0, len(t))
    heading += 35.0 * bridge_disturbance(track, t)
    return [Series("Compass", "Heading", "deg", "Tilt-compensated heading, clockwise from magnetic north",
                   t, np.mod(heading, 360.0), max_interval_ms=60,
                   attributes={"nominal_sample_rate_hz": "25", "source": "magnetometer and accelerometer fusion",
                               "range": "0-360 deg, wraps at north"})]


def gnss_series(track: Track, rng) -> list[Series]:  # type: ignore[no-untyped-def]
    t = np.arange(0.0, math.floor(track.spec.duration) + 1.0)
    n = len(t)
    accuracy = np.full(n, 2.5)
    fix = np.full(n, 2)
    lost = np.zeros(n, dtype=bool)
    for start, end in track.spec.gnss_loss:
        lost |= (t >= start) & (t < end)
        degraded = ((t >= start - 3) & (t < start)) | ((t >= end) & (t < end + 1.5))
        accuracy[degraded] = 9.0
        fix[degraded] = 1
    fix[lost] = 0
    accuracy += rng.normal(0, 0.3, n)
    satellites = np.where(fix == 2, rng.integers(14, 21, n), np.where(fix == 1, rng.integers(5, 9, n), rng.integers(0, 4, n)))
    # The position error wanders slowly and grows with the reported accuracy.
    error = np.zeros((n, 3))
    for i in range(1, n):
        error[i] = 0.85 * error[i - 1] + rng.normal(0, 1, 3) * accuracy[i] * np.array([0.25, 0.25, 0.5])
    x = track.at("x", t) + error[:, 0]
    y = track.at("y", t) + error[:, 1]
    lat0, lon0 = track.spec.origin
    latitude = lat0 + y / 111_320.0
    longitude = lon0 + x / (111_320.0 * math.cos(math.radians(lat0)))
    altitude = track.at("ground", t) + error[:, 2]
    speed = np.maximum(0.0, track.at("speed", t) + rng.normal(0, 0.12, n))
    valid = ~lost
    common = {"nominal_sample_rate_hz": "1", "receiver": "Demo GNSS, multi-constellation",
              "datum": "WGS 84"}
    loss = ", ".join(f"{a:g}-{b:g} s" for a, b in track.spec.gnss_loss)
    return [
        Series("GNSS", "Latitude", "deg", "WGS 84 latitude", t[valid], latitude[valid], max_interval_ms=1500,
               attributes={**common, "known_sample_loss": f"No fix under dense canopy at {loss}"}),
        Series("GNSS", "Longitude", "deg", "WGS 84 longitude", t[valid], longitude[valid], max_interval_ms=1500,
               attributes={**common, "known_sample_loss": f"No fix under dense canopy at {loss}"}),
        Series("GNSS", "Altitude", "m", "Height above mean sea level", t[valid], altitude[valid], max_interval_ms=1500,
               attributes=common),
        Series("GNSS", "Speed", "m/s", "Doppler ground speed", t[valid], speed[valid], max_interval_ms=1500,
               attributes=common),
        Series("GNSS", "Horizontal accuracy", "m", "Estimated horizontal position accuracy (1 sigma)",
               t, np.maximum(0.8, accuracy), attributes=common),
        Series("GNSS", "Satellites", "count", "Satellites used in the solution", t, satellites.astype(float),
               attributes=common),
        Series("GNSS", "Fix", "", "Fix type", t, fix.astype(float), kind="state", labels=FIX_LABELS,
               attributes={**common, "values": "0 no fix, 1 2D fix, 2 3D fix"}),
    ]


def barometer_series(track: Track, rng) -> list[Series]:  # type: ignore[no-untyped-def]
    t = np.arange(0, round(track.spec.duration * 10)) / 10
    altitude = track.at("ground", t) + track.at("lift", t)
    sea_level = 1009.4
    pressure = sea_level * (1 - 2.25577e-5 * altitude) ** 5.25588 + rng.normal(0, 0.012, len(t))
    # The sensor's driver repeats one timestamp: two samples share a time.
    duplicate = int(len(t) * 0.62)
    t = np.insert(t, duplicate, t[duplicate])
    pressure = np.insert(pressure, duplicate, pressure[duplicate] + 0.01)
    raw = (1 - (pressure / sea_level) ** (1 / 5.25588)) / 2.25577e-5
    smoothed = np.empty_like(raw)
    smoothed[0] = raw[0]
    for i in range(1, len(raw)):
        smoothed[i] = smoothed[i - 1] + 0.15 * (raw[i] - smoothed[i - 1])
    common = {"nominal_sample_rate_hz": "10", "sensor": "Demo barometer",
              "known_timing_issue": f"Duplicated timestamp at {t[duplicate]:.1f} s"}
    return [
        Series("Barometer", "Pressure", "hPa", "Static air pressure", t, pressure, max_interval_ms=150,
               attributes={**common, "measurement_range": "300-1100 hPa"}),
        Series("Barometer", "Altitude", "m", f"Barometric altitude, sea-level pressure {sea_level} hPa",
               t, smoothed, max_interval_ms=150, attributes=common),
    ]


def heart_rate_series(track: Track, rng, *, ppg: bool = True) -> list[Series]:  # type: ignore[no-untyped-def]
    dt = 0.1
    fine = np.arange(0.0, track.spec.duration + dt, dt)
    speed = track.at("speed", fine)
    climb = np.gradient(track.at("ground", fine), fine)
    target = np.minimum(184.0, 70 + 24 * speed + 45 * np.clip(climb, 0, None))
    heart_rate = np.empty_like(fine)
    heart_rate[0] = 104.0
    for i in range(1, len(fine)):
        heart_rate[i] = heart_rate[i - 1] + dt / 12.0 * (target[i] - heart_rate[i - 1])
    t = np.arange(0.0, math.floor(track.spec.duration) + 1.0)
    bpm = np.round(np.interp(t, fine, heart_rate) + rng.normal(0, 0.8, len(t)))
    result = [Series("Heart rate", "Heart rate", "bpm", "Optical heart rate", t, bpm,
                     attributes={"nominal_sample_rate_hz": "1", "sensor": "Demo optical heart rate sensor"})]
    if ppg:
        tp = np.arange(0, round(track.spec.duration * 25)) / 25
        jitter = rng.uniform(-0.003, 0.003, len(tp))
        jitter[0] = 0.0
        tp = tp + jitter
        gap = (8.0, 8.5) if track.spec.duration < 20 else (20.0, 20.5)
        tp = tp[~((tp >= gap[0]) & (tp < gap[1]))]
        beat = np.cumsum(np.interp(tp, fine, heart_rate) / 60.0 * np.gradient(tp))
        pulse = np.sin(2 * np.pi * beat) + 0.35 * np.sin(4 * np.pi * beat - 0.8)
        motion = 0.7 * np.sin(np.pi * track.at("gait", tp)) * (track.at("speed", tp) > 0.4)
        wander = 0.4 * np.sin(2 * np.pi * tp / 13.0)
        counts = 32_000 + 900 * pulse + 1300 * motion + 600 * wander + rng.normal(0, 40, len(tp))
        result.append(Series("Heart rate", "PPG", "counts", "Raw green photoplethysmogram", tp, np.round(counts),
                             max_interval_ms=60, attributes={
                                 "nominal_sample_rate_hz": "25", "sensor": "Demo optical heart rate sensor",
                                 "measurement_range": "0-65535 counts",
                                 "known_timing_issue": f"Timestamps jitter +-3 ms; samples lost at {gap[0]:g}-{gap[1]:g} s"}))
    return result


def activity_series(track: Track, rng, *, steps: bool = True) -> list[Series]:  # type: ignore[no-untyped-def]
    duration = track.spec.duration
    t = np.arange(0.0, math.floor(duration) + 1.0)
    step_times = track.step_times[track.step_times <= duration]
    count = np.searchsorted(step_times, t, side="right").astype(float)
    smooth = np.convolve(np.pad(track.ground, 50, mode="edge"), np.ones(101) / 101, "same")[50:-50]
    vertical = np.interp(t, track.t, np.gradient(smooth, track.t))
    jumps = track.spec.jumps
    jump_times = np.array([time for jump in jumps for time in (jump.takeoff, jump.landing)])
    jump_values = np.array([value for _ in jumps for value in (1.0, 2.0)])
    phase_changes = np.flatnonzero(np.diff(track.phase) != 0) + 1
    phase_times = np.concatenate(([0.0], track.t[phase_changes]))
    phase_values = np.concatenate(([track.at("phase", 0.0)], track.phase[phase_changes]))
    keep = (phase_times >= 0) & (phase_times <= duration)
    laps = np.array(track.spec.laps, dtype=float)
    common = {"nominal_sample_rate_hz": "1"}
    result = [
        Series("Activity", "Step count", "steps", "Steps since the start", t, count, attributes=common),
        Series("Activity", "Cadence", "spm", "Steps per minute", t, np.round(track.at("cadence", t)), attributes=common),
        Series("Activity", "Speed", "m/s", "Fused speed", t, track.at("speed", t), attributes=common),
        Series("Activity", "Distance", "m", "Distance since the start", t, np.maximum(0, track.at("distance", t)),
               attributes=common),
        Series("Activity", "Vertical speed", "m/s", "Rate of climb, from the barometer and GNSS", t, vertical,
               attributes=common),
        Series("Activity", "Phase", "", "Workout phase", phase_times[keep], phase_values[keep], kind="state",
               labels=PHASE_LABELS, attributes={"values": ", ".join(f"{k} {v}" for k, v in PHASE_LABELS.items())}),
        Series("Activity", "Jump", "", "Jump detection", jump_times, jump_values, kind="event",
               labels=JUMP_LABELS, attributes={"values": "1 take-off, 2 landing"}),
        Series("Buttons", "Lap", "", "Lap button presses; the value is the lap number", laps,
               np.arange(1, len(laps) + 1, dtype=float), kind="event",
               attributes={"values": "lap number", "note": "The watch beeps at each press"}),
    ]
    if steps:
        jitter = rng.normal(0, 0.004, len(step_times))
        result.append(Series("Activity", "Step", "", "Footstrikes from the wrist accelerometer",
                             np.sort(np.clip(step_times + jitter, 0.0, duration)), track.step_feet[:len(step_times)].astype(float),
                             kind="event", labels=STEP_LABELS, attributes={"values": "1 left foot, 2 right foot"}))
    return result


def system_series(duration: float, rng) -> list[Series]:  # type: ignore[no-untyped-def]
    t = np.arange(0.0, math.floor(duration / 10) * 10 + 1, 10.0)
    fraction = t / max(duration, 1.0)
    temperature = 27.4 + 1.6 * (1 - np.exp(-t / 600)) + rng.normal(0, 0.05, len(t)) + 0.2 * fraction
    battery = 3.912 - 0.0004 * t / 60 + rng.normal(0, 0.0008, len(t))
    common = {"nominal_sample_rate_hz": "0.1"}
    return [
        Series("System", "Temperature", "degC", "Internal temperature", t, temperature, visible=False,
               attributes=common),
        Series("System", "Battery", "V", "Battery voltage", t, battery, visible=False, attributes=common),
    ]


def foot_pod_series(track: Track, rng) -> list[Series]:  # type: ignore[no-untyped-def]
    """A second device on the right shoe, whose clock runs FOOT_POD_CLOCK_ERROR_S ahead."""
    duration = track.spec.duration
    t = np.arange(0.0, math.floor(duration * 2) / 2 + 0.5, 0.5)
    right = track.step_times[(track.step_feet == 2) & (track.step_times <= duration)]
    speed = track.at("speed", right)
    landing = np.isin(np.round(right, 2), np.round([jump.landing for jump in track.spec.jumps], 2))
    contact = 262 - 28 * (speed - 3.0) + rng.normal(0, 6, len(right)) + np.where(landing, 70, 0)
    clock = FOOT_POD_CLOCK_ERROR_S
    common = {"device": "Demo foot pod", "mounting": "right shoe laces",
              "clock": f"Device clock {clock:+.1f} s from the watch; use the card's Time offset to align"}
    return [
        Series("Foot pod", "Cadence", "spm", "Steps per minute from the foot pod", t + clock,
               np.round(track.at("cadence", t) + rng.normal(0, 1.0, len(t))),
               attributes={**common, "nominal_sample_rate_hz": "2"}),
        Series("Foot pod", "Speed", "m/s", "Speed from the foot pod (uncalibrated, about 3 % high)", t + clock,
               1.03 * track.at("speed", t) + rng.normal(0, 0.05, len(t)), attributes={**common, "nominal_sample_rate_hz": "2"}),
        Series("Foot pod", "Ground contact time", "ms", "Right foot ground contact per stride", right + clock, contact,
               attributes={**common, "nominal_sample_rate_hz": "per stride"}),
        Series("Foot pod", "Foot strike", "", "Right footstrikes; 2 = right foot as in the watch's Step events",
               right + clock, np.full(len(right), 2.0), kind="event", labels={},
               attributes={**common, "values": "2 right foot"}),
    ]


def coverage(series: list[Series]) -> tuple[int, int]:
    """How many series have both sample loss and clipping, and how many have a defined range."""
    both = 0
    for item in series:
        if item.value_range is None or item.max_interval_ms is None or len(item.time) < 2:
            continue
        loss = np.any(np.diff(item.time) * 1000 > item.max_interval_ms)
        low, high = item.value_range
        clipped = np.any((item.values <= low) | (item.values >= high))
        both += bool(loss and clipped)
    return both, len(series)
