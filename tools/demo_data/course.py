"""Course, terrain and runner kinematics shared by the sensor data and the videos.

World coordinates are metres: x east, y north, z up. A heading is radians
clockwise from north. Times are seconds from the session's log start; the
simulation also covers a lead-in before it, while the cameras already record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

TRACK_RATE_HZ = 100
PHASE_LABELS = {"0": "Warm-up", "1": "Flat", "2": "Uphill", "3": "Downhill", "4": "Bridge", "5": "Sprint"}


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speed: float  # m/s
    phase: int


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    degrees: float  # positive turns right


@dataclass(frozen=True)
class Jump:
    takeoff: float
    airtime: float
    height: float
    obstacle: str = "log"  # "log" or "stream"

    @property
    def landing(self) -> float:
        return self.takeoff + self.airtime


@dataclass(frozen=True)
class Hill:
    """A Gaussian hill anchored to where the runner is at ``time``."""

    time: float
    amplitude: float  # m, negative for a dip
    sigma: float  # m
    lateral: float = 0.0  # m to the runner's left


@dataclass(frozen=True)
class CourseSpec:
    duration: float
    segments: tuple[Segment, ...]
    turns: tuple[Turn, ...] = ()
    jumps: tuple[Jump, ...] = ()
    hills: tuple[Hill, ...] = ()
    laps: tuple[float, ...] = ()
    bridge: tuple[float, float] | None = None
    gnss_loss: tuple[tuple[float, float], ...] = ()
    imu_gaps: tuple[tuple[float, float], ...] = ()
    start_heading_deg: float = 0.0
    origin: tuple[float, float] = (60.2930, 24.5560)
    base_altitude: float = 40.0
    lead_in: float = 5.0
    seed: int = 1


def main_course() -> CourseSpec:
    """The one-minute forest run of the demo session with videos."""
    return CourseSpec(
        duration=60.0,
        segments=(
            Segment(0, 8, 2.6, 0), Segment(8, 10, 3.4, 1), Segment(10, 24, 2.5, 2), Segment(24, 32, 3.9, 3),
            Segment(32, 41, 3.4, 1), Segment(41, 43, 3.0, 4), Segment(43, 52, 3.5, 1), Segment(52, 60, 4.6, 5),
        ),
        turns=(Turn(14, 17, 70), Turn(32, 35, -110), Turn(47, 49, 45)),
        jumps=(Jump(28.6, 0.42, 0.35, "log"), Jump(51.0, 0.5, 0.4, "stream")),
        hills=(Hill(24.0, 16.0, 17.0), Hill(42.0, -2.5, 3.5), Hill(51.25, -0.7, 1.8),
               Hill(5.0, 9.0, 25.0, 45.0), Hill(38.0, 12.0, 30.0, -60.0), Hill(56.0, 7.0, 20.0, 40.0)),
        laps=(1.0, 32.0, 52.0),
        bridge=(41.0, 43.0),
        gnss_loss=((34.0, 40.0),),
        imu_gaps=((12.300, 12.420), (29.050, 29.110), (45.500, 45.535)),
        start_heading_deg=40.0,
        base_altitude=42.0,
        seed=20261103,
    )


def random_course(duration: float, seed: int, *, origin=(60.2930, 24.5560), jumps_per_minute: float = 1.0,
                  sprint: bool = True) -> CourseSpec:
    """A plausible run of any length: varying pace, turns, hills and jumps."""
    rng = np.random.default_rng(seed)
    segments, turns, jumps, hills, laps = [], [], [], [], []
    t = 0.0
    while t < duration:
        length = float(min(duration - t, rng.uniform(6, 18) if duration <= 120 else rng.uniform(20, 90)))
        phase = int(rng.choice([1, 1, 2, 3]))
        speed = {1: rng.uniform(3.0, 3.7), 2: rng.uniform(2.3, 2.8), 3: rng.uniform(3.6, 4.1)}[phase]
        if not segments:
            phase, speed = 0, 2.6
        segments.append(Segment(t, t + length, float(speed), phase))
        if phase in (2, 3):
            # A hill whose crest is at the end of an uphill or the start of a downhill.
            crest = t + length if phase == 2 else t
            hills.append(Hill(crest, float(rng.uniform(6, 15)) * (1 if phase == 2 else 0.9),
                              float(rng.uniform(14, 26))))
        t += length
    if sprint and duration >= 30:
        last = segments[-1]
        cut = max(last.start, duration - min(8.0, duration / 6))
        segments[-1] = Segment(last.start, cut, last.speed, last.phase)
        segments.append(Segment(cut, duration, 4.5, 5))
        segments = [s for s in segments if s.end > s.start]
    t = float(rng.uniform(5, 12))
    while t < duration - 4:
        length = float(rng.uniform(1.5, 3.5))
        turns.append(Turn(t, t + length, float(rng.choice([-1, 1]) * rng.uniform(25, 100))))
        t += length + float(rng.uniform(6, 25) if duration <= 120 else rng.uniform(15, 120))
    jump_count = int(round(duration / 60 * jumps_per_minute))
    for takeoff in np.sort(rng.uniform(8, duration - 4, jump_count)):
        jumps.append(Jump(float(takeoff), float(rng.uniform(0.38, 0.5)), float(rng.uniform(0.3, 0.42)),
                          str(rng.choice(["log", "stream"]))))
    for _ in range(max(2, int(duration / 30))):
        hills.append(Hill(float(rng.uniform(0, duration)), float(rng.uniform(5, 14)), float(rng.uniform(18, 35)),
                          float(rng.choice([-1, 1]) * rng.uniform(35, 70))))
    laps = [1.0] + [float(x) for x in np.arange(300.0, duration, 300.0)] if duration > 300 else [1.0]
    gaps = tuple((float(a), float(a) + float(rng.uniform(0.03, 0.12)))
                 for a in np.sort(rng.uniform(3, duration - 2, max(2, int(duration / 25)))))
    loss_start = float(rng.uniform(duration * 0.4, duration * 0.7))
    return CourseSpec(
        duration=float(duration), segments=tuple(segments), turns=tuple(turns), jumps=tuple(jumps),
        hills=tuple(hills), laps=tuple(laps), gnss_loss=((loss_start, loss_start + float(rng.uniform(3, 7))),),
        imu_gaps=gaps, start_heading_deg=float(rng.uniform(0, 360)), origin=origin,
        base_altitude=float(rng.uniform(25, 60)), seed=seed,
    )


@dataclass
class Terrain:
    """Ground height: Gaussian hills plus a gentle undulation."""

    base: float
    centers: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    amplitudes: np.ndarray = field(default_factory=lambda: np.zeros(0))
    sigmas: np.ndarray = field(default_factory=lambda: np.ones(0))

    def height(self, x, y):  # type: ignore[no-untyped-def]
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        z = self.base + 1.2 * np.sin(x / 23.0 + 0.4) * np.cos(y / 31.0 - 0.2) + 0.6 * np.sin((x + y) / 11.0)
        for (cx, cy), amplitude, sigma in zip(self.centers, self.amplitudes, self.sigmas):
            z = z + amplitude * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma * sigma))
        return z


@dataclass
class Track:
    """The runner's path and gait, sampled at TRACK_RATE_HZ from -lead_in to the end."""

    spec: CourseSpec
    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    heading: np.ndarray
    yaw_rate: np.ndarray
    speed: np.ndarray
    distance: np.ndarray
    ground: np.ndarray
    lift: np.ndarray
    phase: np.ndarray
    cadence: np.ndarray
    gait: np.ndarray  # continuous step count; a step lands at each integer
    terrain: Terrain
    step_times: np.ndarray
    step_feet: np.ndarray  # 1 left, 2 right

    def at(self, name: str, times):  # type: ignore[no-untyped-def]
        return np.interp(times, self.t, getattr(self, name))

    def heading_at(self, times):  # type: ignore[no-untyped-def]
        return np.interp(times, self.t, np.unwrap(self.heading))

    def position(self, time: float) -> np.ndarray:
        return np.array([self.at("x", time), self.at("y", time), self.at("ground", time) + self.at("lift", time)])

    def airborne(self, times):  # type: ignore[no-untyped-def]
        times = np.asarray(times, dtype=float)
        result = np.zeros(times.shape, dtype=bool)
        for jump in self.spec.jumps:
            result |= (times >= jump.takeoff) & (times < jump.landing)
        return result


def simulate(spec: CourseSpec) -> Track:
    dt = 1.0 / TRACK_RATE_HZ
    t = np.arange(round(-spec.lead_in * TRACK_RATE_HZ), round(spec.duration * TRACK_RATE_HZ) + 1) * dt
    target = np.zeros_like(t)
    phase = np.zeros_like(t)
    for segment in spec.segments:
        mask = (t >= segment.start) & (t < segment.end)
        target[mask], phase[mask] = segment.speed, segment.phase
    phase[t >= spec.segments[-1].end] = spec.segments[-1].phase
    target[t >= spec.duration] = spec.segments[-1].speed
    # Accelerate and change pace smoothly (a 1.2 s moving average).
    window = int(1.2 * TRACK_RATE_HZ)
    speed = np.convolve(np.pad(target, (window, window), mode="edge"), np.ones(window) / window, "same")[window:-window]
    yaw_rate = np.zeros_like(t)
    for turn in spec.turns:
        length = turn.end - turn.start
        mask = (t >= turn.start) & (t < turn.end)
        yaw_rate[mask] += math.radians(turn.degrees) / length * (1 - np.cos(2 * np.pi * (t[mask] - turn.start) / length))
    heading = math.radians(spec.start_heading_deg) + np.cumsum(yaw_rate) * dt
    x = np.cumsum(speed * np.sin(heading)) * dt
    y = np.cumsum(speed * np.cos(heading)) * dt
    distance = np.cumsum(speed) * dt
    distance -= np.interp(0.0, t, distance)
    terrain = anchored_terrain(spec, t, x, y, heading)
    ground = terrain.height(x, y)
    if spec.bridge is not None:
        # The runner crosses the ditch on the level bridge deck.
        start, end = spec.bridge[0] - 0.35, spec.bridge[1] + 0.35
        deck = max(float(np.interp(start, t, ground)), float(np.interp(end, t, ground)))
        ground[(t >= start) & (t <= end)] = deck
    lift = np.zeros_like(t)
    for jump in spec.jumps:
        mask = (t >= jump.takeoff) & (t < jump.landing)
        u = (t[mask] - jump.takeoff) / jump.airtime
        lift[mask] = 4 * jump.height * u * (1 - u)
    cadence = np.where(speed > 0.4, 150.0 + 8.0 * speed, 0.0)
    airborne = lift > 0
    frequency = np.where(airborne, 0.0, cadence / 60.0)
    gait = np.cumsum(frequency) * dt
    # Landing is a footstrike: advance the gait to the next whole step there.
    for jump in spec.jumps:
        index = int(np.searchsorted(t, jump.landing))
        if 0 < index < len(gait):
            gait[index:] += math.ceil(gait[index]) - gait[index]
    crossings = np.flatnonzero(np.floor(gait[1:]) > np.floor(gait[:-1])) + 1
    step_times = t[crossings]
    keep = step_times >= 0.0
    step_times = step_times[keep]
    step_feet = (np.arange(len(step_times)) % 2 + 1).astype(np.int8)
    return Track(spec, t, x, y, heading, yaw_rate, speed, distance, ground, lift, phase, cadence, gait, terrain,
                 step_times, step_feet)


def anchored_terrain(spec: CourseSpec, t, x, y, heading) -> Terrain:  # type: ignore[no-untyped-def]
    centers, amplitudes, sigmas = [], [], []
    for hill in spec.hills:
        px, py, h = (float(np.interp(hill.time, t, values)) for values in (x, y, heading))
        # Left of a heading clockwise from north is (-cos, sin) in (east, north).
        centers.append((px - hill.lateral * math.cos(h), py + hill.lateral * math.sin(h)))
        amplitudes.append(hill.amplitude)
        sigmas.append(hill.sigma)
    return Terrain(spec.base_altitude, np.array(centers, dtype=float).reshape(-1, 2),
                   np.array(amplitudes, dtype=float), np.array(sigmas, dtype=float))


@dataclass
class Scenery:
    """Trees and obstacles around the track, for the videos and the route picture."""

    trees: np.ndarray  # x, y, base z, height, crown radius, trunk radius, shade
    logs: list = field(default_factory=list)  # (centre xyz, along unit vector)
    streams: list = field(default_factory=list)  # (centre xy, heading)
    bridge: list = field(default_factory=list)  # [(xyz, heading)] plank centres


def make_scenery(track: Track, seed: int, margin: float = 70.0) -> Scenery:
    rng = np.random.default_rng(seed)
    px, py = track.x[::20], track.y[::20]
    lo_x, hi_x, lo_y, hi_y = px.min() - margin, px.max() + margin, py.min() - margin, py.max() + margin
    count = int((hi_x - lo_x) * (hi_y - lo_y) / 30.0)
    candidates = np.column_stack((rng.uniform(lo_x, hi_x, count), rng.uniform(lo_y, hi_y, count)))
    path = np.column_stack((track.x[::10], track.y[::10]))
    nearest = np.full(len(candidates), np.inf)
    for start in range(0, len(path), 256):
        block = path[start:start + 256]
        distances = np.hypot(candidates[:, None, 0] - block[None, :, 0], candidates[:, None, 1] - block[None, :, 1])
        nearest = np.minimum(nearest, distances.min(axis=1))
    candidates = candidates[(nearest > 2.4) & (nearest < margin)]
    n = len(candidates)
    trees = np.column_stack((
        candidates[:, 0], candidates[:, 1], track.terrain.height(candidates[:, 0], candidates[:, 1]),
        rng.uniform(9, 19, n), rng.uniform(1.6, 3.0, n), rng.uniform(0.14, 0.3, n), rng.uniform(0.75, 1.15, n),
    ))
    scenery = Scenery(trees)
    for jump in track.spec.jumps:
        middle = jump.takeoff + jump.airtime / 2
        x, y = float(track.at("x", middle)), float(track.at("y", middle))
        h = float(track.heading_at(middle))
        z = float(track.terrain.height(x, y))
        if jump.obstacle == "stream":
            scenery.streams.append((x, y, h))
        else:
            scenery.logs.append((np.array([x, y, z + 0.22]), np.array([math.cos(h), -math.sin(h), 0.0])))
    if track.spec.bridge is not None:
        start, end = track.spec.bridge
        start_distance, end_distance = (float(track.at("distance", value)) for value in (start, end))
        for distance in np.arange(start_distance - 1.0, end_distance + 1.0, 0.45):
            time = float(np.interp(distance, track.distance, track.t))
            x, y = float(track.at("x", time)), float(track.at("y", time))
            # The deck is level across the ditch, at the height of its ends.
            deck = max(float(track.terrain.height(*_point(track, start - 0.35))),
                       float(track.terrain.height(*_point(track, end + 0.35))))
            scenery.bridge.append((np.array([x, y, deck]), float(track.heading_at(time))))
    return scenery


def _point(track: Track, time: float) -> tuple[float, float]:
    return float(track.at("x", time)), float(track.at("y", time))
