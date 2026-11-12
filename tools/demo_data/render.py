"""Draw the forest run with QPainter: chase-camera frames, 360° fisheye lens pairs and the route picture.

Cameras use x right, y down and z forward. A camera's rotation holds its
right, down and forward axes as rows, in world coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import os

import numpy as np

from tools.demo_data.course import PHASE_LABELS, Scenery, Track

NEAR = 0.15
FOG = np.array([176.0, 190.0, 192.0])
SKY_TOP = np.array([96.0, 140.0, 196.0])
SKY_HORIZON = np.array([200.0, 214.0, 222.0])
GROUND_FAR = np.array([86.0, 104.0, 78.0])
TERRAIN_CELL = 3.0
TERRAIN_HALF = 66.0
TREE_RANGE = 80.0
# Sort offsets: ground cells draw before what lies on them, objects after nearby ground.
GROUND_BIAS = 2.2
DECAL_BIAS = 0.2
OBJECT_BIAS = 0.5
# The 360 player's measured lens model, used when an INSV file has no factory calibration.
LENS_FOCAL = 1044.389 / 3840
LENS_RADIAL = (0.087926, -0.023232, 0.001227, -0.000388)
LENS_MAX_THETA = 1.8
PHASE_COLORS = {0: "#8d6e63", 1: "#1e88e5", 2: "#e53935", 3: "#43a047", 4: "#fb8c00", 5: "#8e24aa"}


def ensure_gui() -> None:
    """A Qt GUI application is needed for fonts; offscreen works without a display."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.name == "nt":
        os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        ensure_gui.app = QGuiApplication(["demo-renderer"])  # type: ignore[attr-defined]


@dataclass
class Camera:
    position: np.ndarray
    rotation: np.ndarray  # rows: right, down, forward
    width: int
    height: int
    focal: float

    def to_camera(self, points: np.ndarray) -> np.ndarray:
        return (points - self.position) @ self.rotation.T

    def to_screen(self, camera_points: np.ndarray) -> np.ndarray:
        z = np.maximum(camera_points[..., 2], 1e-6)
        return np.stack((self.width / 2 + self.focal * camera_points[..., 0] / z,
                         self.height / 2 + self.focal * camera_points[..., 1] / z), axis=-1)


def look_rotation(forward: np.ndarray) -> np.ndarray:
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    return np.array([right, np.cross(forward, right), forward])


def heading_vector(heading: float) -> np.ndarray:
    return np.array([math.sin(heading), math.cos(heading), 0.0])


def fog(color, distance):  # type: ignore[no-untyped-def]
    amount = np.clip((np.asarray(distance, dtype=float) - 12.0) / 75.0, 0.0, 0.85)[..., None]
    return np.asarray(color, dtype=float) * (1 - amount) + FOG * amount


class SceneRenderer:
    """Draws one moment of the run from any camera."""

    def __init__(self, track: Track, scenery: Scenery) -> None:
        ensure_gui()
        self.track = track
        self.scenery = scenery
        rng = np.random.default_rng(track.spec.seed + 7)
        self.ring_heights = np.convolve(rng.uniform(18, 46, 160), np.ones(7) / 7, "same")

    # Runner -----------------------------------------------------------------------------------
    def runner_segments(self, t: float):  # type: ignore[no-untyped-def]
        """Body parts as (start, end, thickness m, colour), plus the head (centre, radius)."""
        track = self.track
        heading = float(track.heading_at(t))
        forward, left, up = heading_vector(heading), np.array([-math.cos(heading), math.sin(heading), 0.0]), np.array([0.0, 0.0, 1.0])
        origin = np.array([track.at("x", t), track.at("y", t), track.at("ground", t) + track.at("lift", t)])
        speed = float(track.at("speed", t))
        gait = float(track.at("gait", t))
        effort = min(1.0, speed / 2.2)
        airborne = bool(track.airborne(t))
        swing = math.sin(math.pi * gait)
        hip = origin + up * (0.95 + 0.04 * effort * math.cos(2 * math.pi * gait))
        shoulder = hip + up * 0.52 + forward * 0.06 * effort

        def limb(root, angle, bend, upper, lower):  # type: ignore[no-untyped-def]
            joint = root + upper * (math.sin(angle) * forward - math.cos(angle) * up)
            end = joint + lower * (math.sin(angle - bend) * forward - math.cos(angle - bend) * up)
            return joint, end

        parts = []
        shirt, shorts, skin, shoe = "#1565c0", "#212121", "#e0ac83", "#ff6f00"
        for side, sign in ((1, 1.0), (-1, -1.0)):
            leg_angle = (0.55 * effort * swing * sign) if not airborne else (0.5 if side == 1 else -0.2)
            knee_bend = (0.25 + 0.7 * effort * max(0.0, -swing * sign)) if not airborne else 1.3
            root = hip + left * 0.1 * side
            knee, ankle = limb(root, leg_angle, knee_bend, 0.46, 0.45)
            parts += [(root, knee, 0.15, shorts), (knee, ankle, 0.11, skin), (ankle, ankle + forward * 0.2, 0.09, shoe)]
            arm_angle = -0.5 * effort * swing * sign if not airborne else 0.9
            root = shoulder + left * 0.2 * side
            elbow, wrist = limb(root, arm_angle, -1.45 * effort - 0.1, 0.3, 0.27)
            parts += [(root, elbow, 0.1, shirt), (elbow, wrist, 0.08, skin)]
            if side == 1:
                lap = any(abs(t - lap_time) < 0.25 for lap_time in track.spec.laps)
                parts.append((wrist - forward * 0.03, wrist + forward * 0.03, 0.07, "#e3f2fd" if lap else "#111111"))
        parts.append((hip, shoulder, 0.34, shirt))
        head = (shoulder + up * 0.24 + forward * 0.04, 0.12)
        return parts, head

    # Drawing ----------------------------------------------------------------------------------
    def render(self, camera: Camera, t: float, *, draw_runner: bool = True):  # type: ignore[no-untyped-def]
        """Painter's algorithm: ground first within each distance, objects slightly later."""
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

        # RGB32 is the raster engine's fast format; RGB888 is several times slower to fill.
        image = QImage(camera.width, camera.height, QImage.Format.Format_RGB32)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF(0, 0, camera.width, camera.height), self._sky(camera))
        items: list[tuple[float, int, object]] = []
        self._ring(camera, items)
        self._terrain(camera, items)
        self._decals(camera, t, items)
        self._trees(camera, items)
        self._objects(camera, items)
        if draw_runner:
            depth = float(np.linalg.norm(self.track.position(t) - camera.position)) - OBJECT_BIAS
            items.append((depth, 3, t))
        items.sort(key=lambda item: -item[0])
        painter.setPen(Qt.PenStyle.NoPen)
        smooth = True
        for _depth, kind, data in items:
            if kind in (0, 2):  # filled polygon in screen space; ground (0) is not antialiased, for speed
                if smooth != (kind == 2):
                    smooth = kind == 2
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, smooth)
                points, color = data  # type: ignore[misc]
                painter.setBrush(QColor(*color))
                painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
                continue
            if not smooth:
                smooth = True
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if kind == 1:  # thick line in world space
                start, end, thickness, color = data  # type: ignore[misc]
                self._line(painter, camera, start, end, thickness, color)
                painter.setPen(Qt.PenStyle.NoPen)
            elif kind == 3:
                self._runner(painter, camera, float(data))  # type: ignore[arg-type]
                painter.setPen(Qt.PenStyle.NoPen)
        painter.end()
        return image

    def _sky(self, camera: Camera):  # type: ignore[no-untyped-def]
        from PySide6.QtGui import QImage

        w, h = max(8, camera.width // 16), max(8, camera.height // 16)
        u = (np.arange(w) + 0.5) / w * camera.width - camera.width / 2
        v = (np.arange(h) + 0.5) / h * camera.height - camera.height / 2
        uu, vv = np.meshgrid(u, v)
        directions = np.stack((uu, vv, np.full_like(uu, camera.focal)), axis=-1) @ camera.rotation
        elevation = directions[..., 2] / np.linalg.norm(directions, axis=-1)
        sky = SKY_HORIZON + (SKY_TOP - SKY_HORIZON) * np.clip(elevation, 0, 1)[..., None] ** 0.6
        colors = np.where(elevation[..., None] >= 0, sky, GROUND_FAR * 0.6 + FOG * 0.4)
        self._sky_pixels = np.ascontiguousarray(colors.astype(np.uint8))
        return QImage(self._sky_pixels.data, w, h, 3 * w, QImage.Format.Format_RGB888)

    def _add_batch(self, camera: Camera, items, polygons, colors, keys, grow: float = 1.0,  # type: ignore[no-untyped-def]
                   smooth: bool = False) -> None:
        """Project, cull and near-clip polygons (N, corners, 3) at once; queue the visible ones."""
        if len(polygons) == 0:
            return
        local = (polygons - camera.position) @ camera.rotation.T
        front = local[..., 2] > NEAR
        all_front, any_front = front.all(axis=1), front.any(axis=1)
        screen = camera.to_screen(local)
        w, h = camera.width, camera.height
        outside = ((screen[..., 0] < 0).all(axis=1) | (screen[..., 0] > w).all(axis=1)
                   | (screen[..., 1] < 0).all(axis=1) | (screen[..., 1] > h).all(axis=1))
        colors = np.broadcast_to(np.asarray(colors), (len(polygons), 3)).astype(int).tolist()
        keys = np.asarray(keys, dtype=float)
        whole = np.flatnonzero(all_front & ~outside)
        if grow != 1.0 and len(whole):
            # Grow ground cells slightly so antialiased edges leave no seams.
            middle = screen[whole].mean(axis=1, keepdims=True)
            screen[whole] = middle + (screen[whole] - middle) * grow
        kind = 2 if smooth else 0
        for index, points in zip(whole.tolist(), screen[whole].tolist()):
            items.append((keys[index], kind, (points, colors[index])))
        for index in np.flatnonzero(any_front & ~all_front).tolist():
            clipped = clip_near(local[index])
            if len(clipped) >= 3:
                items.append((keys[index], kind, (camera.to_screen(clipped).tolist(), colors[index])))

    def _ring(self, camera: Camera, items) -> None:  # type: ignore[no-untyped-def]
        count = len(self.ring_heights)
        a = np.linspace(0, 2 * np.pi, count + 1)
        radius, base = 420.0, self.track.spec.base_altitude - 8
        x = camera.position[0] + radius * np.sin(a)
        y = camera.position[1] + radius * np.cos(a)
        top = base + np.append(self.ring_heights, self.ring_heights[0])
        quads = np.stack((np.column_stack((x[:-1], y[:-1], np.full(count, base))),
                          np.column_stack((x[1:], y[1:], np.full(count, base))),
                          np.column_stack((x[1:], y[1:], top[1:])),
                          np.column_stack((x[:-1], y[:-1], top[:-1]))), axis=1)
        self._add_batch(camera, items, quads, np.tile([122, 140, 138], (count, 1)), np.full(count, 10_000.0))

    def _terrain(self, camera: Camera, items) -> None:  # type: ignore[no-untyped-def]
        cell = TERRAIN_CELL
        cx = math.floor(camera.position[0] / cell) * cell
        cy = math.floor(camera.position[1] / cell) * cell
        offsets = np.arange(-TERRAIN_HALF, TERRAIN_HALF + cell, cell)
        gx, gy = np.meshgrid(cx + offsets, cy + offsets)
        gz = self.track.terrain.height(gx, gy)
        grid = np.stack((gx, gy, gz), axis=-1)
        quads = np.stack((grid[:-1, :-1], grid[:-1, 1:], grid[1:, 1:], grid[1:, :-1]), axis=2).reshape(-1, 4, 3)
        # Shade each cell by its slope toward the light, vary it per cell, and fade it into the fog.
        dzdx = (gz[:-1, 1:] - gz[:-1, :-1] + gz[1:, 1:] - gz[1:, :-1]) / (2 * cell)
        dzdy = (gz[1:, :-1] - gz[:-1, :-1] + gz[1:, 1:] - gz[:-1, 1:]) / (2 * cell)
        normal = np.stack((-dzdx, -dzdy, np.ones_like(dzdx)), axis=-1)
        normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
        light = normal @ np.array([0.45, 0.3, 0.84])
        cell_x, cell_y = np.meshgrid(np.floor((cx + offsets[:-1]) / cell), np.floor((cy + offsets[:-1]) / cell))
        noise = np.sin(cell_x * 12.9898 + cell_y * 78.233) * 43758.5453
        noise = noise - np.floor(noise)
        base = np.array([72.0, 98.0, 54.0]) * (0.9 + 0.16 * noise[..., None])
        colors = base * (0.6 + 0.5 * np.clip(light, 0, 1))[..., None]
        distance = np.hypot(gx[:-1, :-1] + cell / 2 - camera.position[0], gy[:-1, :-1] + cell / 2 - camera.position[1])
        colors = fog(colors, distance).reshape(-1, 3)
        distance = distance.reshape(-1)
        near = distance <= TERRAIN_HALF
        self._add_batch(camera, items, quads[near], colors[near], distance[near] + GROUND_BIAS, grow=1.02)

    def _decals(self, camera: Camera, t: float, items) -> None:  # type: ignore[no-untyped-def]
        track = self.track
        here = float(track.at("distance", t))
        distances = np.arange(max(track.distance[0], here - 35), min(track.distance[-1], here + 90), 1.0)
        if len(distances) >= 2:
            times = np.interp(distances, track.distance, track.t)
            heading = track.heading_at(times)
            left = np.column_stack((-np.cos(heading), np.sin(heading), np.zeros(len(times)))) * 0.7
            centre = np.column_stack((track.at("x", times), track.at("y", times), track.at("ground", times) + 0.03))
            quads = np.stack((centre[:-1] + left[:-1], centre[1:] + left[1:], centre[1:] - left[1:], centre[:-1] - left[:-1]), axis=1)
            depth = np.linalg.norm(quads.mean(axis=1) - camera.position, axis=1)
            self._add_batch(camera, items, quads, fog(np.array([150, 124, 88]), depth), depth - DECAL_BIAS)
        for x, y, heading in self.scenery.streams:
            across = np.array([-math.cos(heading), math.sin(heading)])
            along = np.array([math.sin(heading), math.cos(heading)])
            k = np.arange(-30, 30, 2.0)
            a = np.array([x, y]) + across * k[:, None] + along * (0.6 * np.sin(k / 5))[:, None]
            b = np.array([x, y]) + across * (k + 2)[:, None] + along * (0.6 * np.sin((k + 2) / 5))[:, None]
            corners = np.stack((a - along * 0.8, b - along * 0.8, b + along * 0.8, a + along * 0.8), axis=1)
            heights = self.track.terrain.height(corners[..., 0], corners[..., 1]) + 0.05
            quads = np.concatenate((corners, heights[..., None]), axis=-1)
            depth = np.linalg.norm(quads.mean(axis=1) - camera.position, axis=1)
            self._add_batch(camera, items, quads, fog(np.array([70, 120, 170]), depth), depth - DECAL_BIAS)

    def _trees(self, camera: Camera, items) -> None:  # type: ignore[no-untyped-def]
        trees = self.scenery.trees
        dx, dy = trees[:, 0] - camera.position[0], trees[:, 1] - camera.position[1]
        distance = np.hypot(dx, dy)
        keep = (distance < TREE_RANGE) & (distance > 0.5)
        trees, dx, dy, distance = trees[keep], dx[keep], dy[keep], distance[keep]
        if not len(trees):
            return
        right = np.column_stack((dy, -dx, np.zeros(len(trees)))) / distance[:, None]
        base = trees[:, :3]
        up = np.array([0.0, 0.0, 1.0])
        height, crown, trunk, shade = (trees[:, i:i + 1] for i in (3, 4, 5, 6))
        trunks = np.stack((base - right * trunk, base + right * trunk, base + right * trunk * 0.6 + up * height * 0.45,
                           base - right * trunk * 0.6 + up * height * 0.45), axis=1)
        depth = distance - OBJECT_BIAS
        self._add_batch(camera, items, trunks, fog(np.array([92, 64, 44]) * shade, distance), depth, smooth=True)
        for level, (bottom, top, width, tone) in enumerate(((0.22, 0.75, 1.0, 0.85), (0.45, 1.0, 0.72, 1.0))):
            crowns = np.stack((base + up * height * bottom - right * crown * width,
                               base + up * height * bottom + right * crown * width, base + up * height * top), axis=1)
            self._add_batch(camera, items, crowns, fog(np.array([34, 78, 46]) * shade * tone, distance),
                            depth - 0.01 * (level + 1), smooth=True)

    def _objects(self, camera: Camera, items) -> None:  # type: ignore[no-untyped-def]
        for centre, along in self.scenery.logs:
            # A runner jumping over the log is above it: draw the log first.
            depth = float(np.linalg.norm(centre - camera.position)) + OBJECT_BIAS
            items.append((depth, 1, (centre - along * 1.8, centre + along * 1.8, 0.42, "#6d4c41")))
        planks = self.scenery.bridge
        if not planks:
            return
        decks = np.array([centre for centre, _ in planks]) + np.array([0, 0, 0.08])
        headings = np.array([heading for _, heading in planks])
        across = np.column_stack((-np.cos(headings), np.sin(headings), np.zeros(len(planks))))
        along = np.column_stack((np.sin(headings), np.cos(headings), np.zeros(len(planks))))
        quads = np.stack((decks - across * 0.85 - along * 0.2, decks + across * 0.85 - along * 0.2,
                          decks + across * 0.85 + along * 0.2, decks - across * 0.85 + along * 0.2), axis=1)
        depth = np.linalg.norm(decks - camera.position, axis=1)
        shade = (0.85 + 0.15 * (np.arange(len(planks)) % 2))[:, None]
        self._add_batch(camera, items, quads, fog(np.array([150, 110, 70]) * shade, depth), depth - DECAL_BIAS - 0.05)
        rail = np.array([0, 0, 1.0])
        for index in range(len(planks)):
            key = float(depth[index]) - OBJECT_BIAS
            for side in (-1, 1):
                post = decks[index] + across[index] * 0.9 * side
                if index % 3 == 0:
                    items.append((key, 1, (post, post + rail, 0.08, "#5d4037")))
                if index + 1 < len(planks):
                    items.append((key, 1, (post + rail, decks[index + 1] + across[index + 1] * 0.9 * side + rail,
                                           0.06, "#795548")))

    def _line(self, painter, camera: Camera, start, end, thickness: float, color: str) -> None:  # type: ignore[no-untyped-def]
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QPen

        local = camera.to_camera(np.array([start, end], dtype=float))
        if np.all(local[:, 2] <= NEAR):
            return
        if np.any(local[:, 2] <= NEAR):
            a, b = local
            fraction = (NEAR - a[2]) / (b[2] - a[2])
            point = a + (b - a) * fraction
            local = np.array([point, b]) if a[2] <= NEAR else np.array([a, point])
        screen = camera.to_screen(local)
        width = camera.focal * thickness / max(float(local[:, 2].mean()), NEAR)
        pen = QPen(QColor(color), max(1.0, width))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(*screen[0]), QPointF(*screen[1]))

    def _runner(self, painter, camera: Camera, t: float) -> None:  # type: ignore[no-untyped-def]
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor

        parts, (head, radius) = self.runner_segments(t)
        parts = sorted(parts, key=lambda part: -float(np.linalg.norm((part[0] + part[1]) / 2 - camera.position)))
        for start, end, thickness, color in parts:
            self._line(painter, camera, start, end, thickness, color)
        local = camera.to_camera(head[None, :])[0]
        if local[2] > NEAR:
            x, y = camera.to_screen(local[None, :])[0]
            r = camera.focal * radius / local[2]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e0ac83"))
            painter.drawEllipse(QPointF(x, y), r, r)
            painter.setBrush(QColor("#263238"))  # cap
            painter.drawEllipse(QPointF(x, y - r * 0.35), r * 0.95, r * 0.7)


def clip_near(points: np.ndarray) -> np.ndarray:
    """Clip a camera-space polygon to the part in front of the near plane."""
    result = []
    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        a_in, b_in = a[2] > NEAR, b[2] > NEAR
        if a_in:
            result.append(a)
        if a_in != b_in:
            result.append(a + (b - a) * (NEAR - a[2]) / (b[2] - a[2]))
    return np.array(result)


def image_array(image) -> np.ndarray:  # type: ignore[no-untyped-def]
    """RGB pixels of an RGB32 image (stored as B, G, R, A bytes)."""
    width, height = image.width(), image.height()
    buffer = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
    return buffer.reshape(height, image.bytesPerLine())[:, :width * 4].reshape(height, width, 4)[..., 2::-1].copy()


# Cameras --------------------------------------------------------------------------------------
def chase_camera(track: Track, t: float, width: int, height: int, horizontal_fov_deg: float = 78.0) -> Camera:
    """A camera following the runner from behind, lagging in turns so they show."""
    heading = float(track.heading_at(t - 0.7))
    runner = np.array([track.at("x", t), track.at("y", t), track.at("ground", t)])
    forward = heading_vector(heading)
    position = runner - forward * 4.6 + np.array([0, 0, 2.2 + 0.03 * math.sin(2 * math.pi * float(track.at("gait", t)))])
    ground = float(track.terrain.height(position[0], position[1]))
    position[2] = max(position[2], ground + 1.2)
    target = runner + np.array([0, 0, 1.0]) + forward * 2.5
    focal = width / 2 / math.tan(math.radians(horizontal_fov_deg) / 2)
    return Camera(position, look_rotation(target - position), width, height, focal)


def selfie_camera_pose(track: Track, t: float) -> tuple[np.ndarray, np.ndarray]:
    """A 360 camera on an invisible stick above and behind the runner, level, facing the run."""
    heading = float(track.heading_at(t - 0.3))
    forward = heading_vector(heading)
    position = track.position(t) + np.array([0, 0, 2.55]) - forward * 0.4
    return position, look_rotation(forward)


# Rotations of the six cube faces relative to the camera (rows: right, down, forward).
CUBE_FACES = (
    np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float),     # front
    np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], dtype=float),     # right
    np.array([[-1, 0, 0], [0, 1, 0], [0, 0, -1]], dtype=float),    # back
    np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float),     # left
    np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float),     # up
    np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float),     # down
)


def lens_radius(theta):  # type: ignore[no-untyped-def]
    """The lens model: image radius r = theta (1 + k1 t^2 + k2 t^4 + k3 t^6 + k4 t^8)."""
    k1, k2, k3, k4 = LENS_RADIAL
    t2 = theta * theta
    return theta * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4))))


def lens_theta(radius: np.ndarray) -> np.ndarray:
    """Invert the lens model with a dense table; it increases monotonically up to LENS_MAX_THETA."""
    table = np.linspace(0.0, LENS_MAX_THETA, 20_001)
    return np.interp(radius, lens_radius(table), table)


def lens_directions(size: int, back: bool) -> tuple[np.ndarray, np.ndarray]:
    """Camera-space view directions for each pixel of one square fisheye lens image, and their validity."""
    coordinates = (np.arange(size) + 0.5) / size
    u, v = np.meshgrid(coordinates, coordinates)
    x, y = (u - 0.5) / LENS_FOCAL, (v - 0.5) / LENS_FOCAL
    radius = np.hypot(x, y)
    max_radius = lens_radius(LENS_MAX_THETA)
    theta = lens_theta(np.minimum(radius, max_radius))
    valid = (radius <= max_radius) & np.isfinite(theta)
    scale = np.where(radius > 1e-9, np.sin(theta) / np.maximum(radius, 1e-9), 1.0)
    directions = np.stack((x * scale, y * scale, np.cos(theta)), axis=-1)
    if back:
        # The player samples the second lens at (-x, y, -z) of the view direction.
        directions = directions * np.array([-1.0, 1.0, -1.0])
    return directions, valid


def lens_lookup(size: int, face_size: int, back: bool) -> np.ndarray:
    """Index of each lens pixel in the flattened six-face atlas; the extra last pixel is black."""
    directions, valid = lens_directions(size, back)
    flat = directions.reshape(-1, 3)
    face = np.argmax(np.stack([flat @ rows[2] for rows in CUBE_FACES], axis=1), axis=1)
    index = np.full(len(flat), 6 * face_size * face_size, dtype=np.int64)
    for number, rows in enumerate(CUBE_FACES):
        mask = face == number
        local = flat[mask] @ rows.T
        column = np.clip((face_size / 2 * (1 + local[:, 0] / local[:, 2])).astype(np.int64), 0, face_size - 1)
        row = np.clip((face_size / 2 * (1 + local[:, 1] / local[:, 2])).astype(np.int64), 0, face_size - 1)
        index[mask] = number * face_size * face_size + row * face_size + column
    index[~valid.reshape(-1)] = 6 * face_size * face_size
    return index.reshape(size, size)


class FlatRenderer:
    """Chase-camera frames with a burned-in camera clock, like action-camera footage."""

    def __init__(self, track: Track, scenery: Scenery, width: int, height: int, log_start: float) -> None:
        self.scene = SceneRenderer(track, scenery)
        self.track, self.width, self.height, self.log_start = track, width, height, log_start

    def frame(self, t: float) -> np.ndarray:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

        image = self.scene.render(chase_camera(self.track, t, self.width, self.height), t)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("Consolas")
        font.setPixelSize(max(12, self.height // 30))
        clock = datetime.fromtimestamp(self.log_start + t).strftime("%Y-%m-%d %H:%M:%S.") + f"{int((t % 1) * 10) % 10}"
        path = QPainterPath()
        path.addText(QPointF(self.width * 0.03, self.height * 0.95), font, f"CAM  {clock}")
        painter.setPen(QPen(QColor(0, 0, 0, 160), max(2.0, self.height / 400)))
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(229, 57, 53))
        painter.drawEllipse(QPointF(self.width * 0.95, self.height * 0.06), self.height / 90, self.height / 90)
        painter.end()
        return image_array(image)


class InsvRenderer:
    """Back and front fisheye lens images of a 360 camera, rendered through a cube map."""

    def __init__(self, track: Track, scenery: Scenery, lens_size: int) -> None:
        self.scene = SceneRenderer(track, scenery)
        self.track = track
        # A 90° face as sharp as the lens centre: the lens has LENS_FOCAL * size pixels per radian.
        self.face_size = int(math.ceil(LENS_FOCAL * lens_size * 1.05 / 8) * 8) * 2
        self.lookups = [lens_lookup(lens_size, self.face_size, back) for back in (True, False)]

    def frame(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        position, rotation = selfie_camera_pose(self.track, t)
        size = self.face_size
        faces = [image_array(self.scene.render(Camera(position, face @ rotation, size, size, size / 2), t))
                 for face in CUBE_FACES]
        atlas = np.concatenate([np.concatenate(faces).reshape(-1, 3), np.zeros((1, 3), np.uint8)])
        return tuple(atlas[lookup] for lookup in self.lookups)  # type: ignore[return-value]


# Route picture --------------------------------------------------------------------------------
def route_picture(track: Track, scenery: Scenery, path, width: int = 2400, height: int = 1700) -> None:  # type: ignore[no-untyped-def]
    """An illustrated map of the route with its features and an elevation profile."""
    ensure_gui()
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

    duration = track.spec.duration
    run = (track.t >= 0) & (track.t <= duration)
    xs, ys = track.x[run], track.y[run]
    margin = 30.0
    lo_x, hi_x, lo_y, hi_y = xs.min() - margin, xs.max() + margin, ys.min() - margin, ys.max() + margin
    map_rect = QRectF(60, 140, width - 120, height - 560)
    # Widen the shown area to the map's shape, so the terrain fills it.
    extra_x = max(0.0, (hi_y - lo_y) * map_rect.width() / map_rect.height() - (hi_x - lo_x)) / 2
    extra_y = max(0.0, (hi_x - lo_x) * map_rect.height() / map_rect.width() - (hi_y - lo_y)) / 2
    lo_x, hi_x, lo_y, hi_y = lo_x - extra_x, hi_x + extra_x, lo_y - extra_y, hi_y + extra_y
    scale = min(map_rect.width() / (hi_x - lo_x), map_rect.height() / (hi_y - lo_y))
    ox = map_rect.left() + (map_rect.width() - (hi_x - lo_x) * scale) / 2
    oy = map_rect.top() + (map_rect.height() - (hi_y - lo_y) * scale) / 2

    def to_map(x, y):  # type: ignore[no-untyped-def]
        return QPointF(ox + (x - lo_x) * scale, oy + (hi_y - y) * scale)

    # Hillshaded terrain.
    w, h = int((hi_x - lo_x) * scale), int((hi_y - lo_y) * scale)
    gx, gy = np.meshgrid(np.linspace(lo_x, hi_x, max(2, w // 2)), np.linspace(hi_y, lo_y, max(2, h // 2)))
    gz = track.terrain.height(gx, gy)
    dzdx = np.gradient(gz, axis=1) / ((hi_x - lo_x) / gz.shape[1])
    dzdy = -np.gradient(gz, axis=0) / ((hi_y - lo_y) / gz.shape[0])
    shade = np.clip(0.75 + 0.9 * (-0.6 * dzdx + 0.5 * dzdy) / np.sqrt(1 + dzdx ** 2 + dzdy ** 2), 0.35, 1.15)
    elevation = (gz - gz.min()) / max(1e-6, np.ptp(gz))
    base = np.array([196, 214, 170]) * (1 - elevation[..., None] * 0.25) + np.array([150, 130, 100]) * elevation[..., None] * 0.25
    pixels = np.ascontiguousarray(np.clip(base * shade[..., None], 0, 255).astype(np.uint8))
    terrain = QImage(pixels.data, pixels.shape[1], pixels.shape[0], 3 * pixels.shape[1], QImage.Format.Format_RGB888)

    image = QImage(width, height, QImage.Format.Format_RGB888)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(ox, oy, w, h), terrain)
    # Contour-like elevation bands.
    painter.setPen(QPen(QColor(120, 100, 70, 70), 1.2))
    levels = np.arange(math.ceil(gz.min() / 2) * 2, gz.max(), 2.0)
    step_x, step_y = w / gz.shape[1], h / gz.shape[0]
    for level in levels:
        crossing = np.argwhere((gz[:, :-1] < level) != (gz[:, 1:] < level))
        for row, column in crossing[::2]:
            painter.drawPoint(QPointF(ox + column * step_x, oy + row * step_y))
    # Trees.
    painter.setPen(Qt.PenStyle.NoPen)
    inside = ((scenery.trees[:, 0] > lo_x) & (scenery.trees[:, 0] < hi_x)
              & (scenery.trees[:, 1] > lo_y) & (scenery.trees[:, 1] < hi_y))
    for x, y, _z, _height, crown, _trunk, tone in scenery.trees[inside]:
        painter.setBrush(QColor(int(40 * tone), int(96 * tone), int(52 * tone), 200))
        painter.drawEllipse(to_map(x, y), crown * scale * 0.9, crown * scale * 0.9)
    # Stream and bridge.
    for x, y, heading in scenery.streams:
        stream = QPainterPath()
        across = np.array([-math.cos(heading), math.sin(heading)])
        along = np.array([math.sin(heading), math.cos(heading)])
        for index, k in enumerate(np.arange(-30, 31, 2.0)):
            point = np.array([x, y]) + across * k + along * 0.6 * math.sin(k / 5)
            (stream.moveTo if index == 0 else stream.lineTo)(to_map(*point))
        painter.setPen(QPen(QColor("#4f8fc0"), max(3.0, 1.6 * scale)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(stream)
    if scenery.bridge:
        first, last = scenery.bridge[0][0], scenery.bridge[-1][0]
        painter.setPen(QPen(QColor("#6d4c41"), max(6.0, 2.2 * scale), Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawLine(to_map(first[0], first[1]), to_map(last[0], last[1]))
    # The route, coloured by phase.
    times = track.t[run][::10]
    for a, b in zip(times[:-1], times[1:]):
        phase = int(track.at("phase", a))
        painter.setPen(QPen(QColor(PHASE_COLORS.get(phase, "#000000")), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(to_map(track.at("x", a), track.at("y", a)), to_map(track.at("x", b), track.at("y", b)))
    for start, end in track.spec.gnss_loss:
        painter.setPen(QPen(QColor("#ffffff"), 4, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap))
        for a, b in zip(np.arange(start, end, 0.25), np.arange(start + 0.25, end + 0.25, 0.25)):
            painter.drawLine(to_map(track.at("x", a), track.at("y", a)), to_map(track.at("x", b), track.at("y", b)))

    font = QFont("Segoe UI")
    font.setPixelSize(30)
    painter.setFont(font)

    def label(time, text, color="#212121", dx=24, dy=-18):  # type: ignore[no-untyped-def]
        point = to_map(track.at("x", time), track.at("y", time))
        painter.setPen(QPen(QColor("#ffffff"), 4))
        painter.setBrush(QColor(color))
        painter.drawEllipse(point, 13, 13)
        text_path = QPainterPath()
        text_path.addText(point + QPointF(dx, dy), font, text)
        painter.setPen(QPen(QColor(255, 255, 255, 230), 7))
        painter.drawPath(text_path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#212121"))
        painter.drawPath(text_path)

    label(0.0, "Start", "#2e7d32")
    label(duration, "Finish", "#c62828")
    for jump in track.spec.jumps:
        label(jump.takeoff + jump.airtime / 2, "Jump over log" if jump.obstacle == "log" else "Jump over stream", "#fb8c00")
    for turn in track.spec.turns:
        label((turn.start + turn.end) / 2, f"Turn {'right' if turn.degrees > 0 else 'left'} {abs(turn.degrees):.0f}°", "#5e35b1", dy=-24 if turn.degrees > 0 else 44)
    if track.spec.bridge:
        label(sum(track.spec.bridge) / 2, "Wooden bridge", "#6d4c41", dx=-250, dy=10)
    for start, end in track.spec.gnss_loss:
        label((start + end) / 2, "No GNSS fix (dense canopy)", "#000000", dx=30, dy=10)
    summit = float(track.t[run][np.argmax(track.ground[run])])
    label(summit, f"Hilltop {track.ground[run].max():.0f} m", "#e53935", dx=10, dy=56)

    # Title, legend and scale bar.
    title = QFont("Segoe UI")
    title.setPixelSize(56)
    title.setBold(True)
    painter.setFont(title)
    painter.setPen(QColor("#212121"))
    painter.drawText(QPointF(60, 90), "Forest run, 60 s: synthetic route")
    painter.setFont(font)
    painter.setPen(QColor("#616161"))
    painter.drawText(QPointF(60, 128), f"{track.distance[run][-1]:.0f} m, climb {track.ground[run].max() - track.ground[run][0]:.0f} m. "
                     "LODGaZilla demo data: not a real recording.")
    legend_x = width - 760
    for index, (key, name) in enumerate(PHASE_LABELS.items()):
        painter.setPen(QPen(QColor(PHASE_COLORS[int(key)]), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x = legend_x + (index % 3) * 240
        y = 60 + (index // 3) * 44
        painter.drawLine(QPointF(x, y), QPointF(x + 40, y))
        painter.setPen(QColor("#212121"))
        painter.drawText(QPointF(x + 52, y + 10), name)
    bar = 50.0
    painter.setPen(QPen(QColor("#212121"), 4))
    start = QPointF(map_rect.left() + 20, map_rect.bottom() - 20)
    painter.drawLine(start, start + QPointF(bar * scale, 0))
    painter.drawText(start + QPointF(0, -14), f"{bar:.0f} m")
    north = QPointF(map_rect.right() - 60, map_rect.top() + 80)
    arrow = QPainterPath()
    arrow.moveTo(north + QPointF(0, -50))
    arrow.lineTo(north + QPointF(20, 20))
    arrow.lineTo(north + QPointF(0, 8))
    arrow.lineTo(north + QPointF(-20, 20))
    arrow.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#212121"))
    painter.drawPath(arrow)
    painter.setPen(QColor("#212121"))
    painter.drawText(north + QPointF(-10, 60), "N")

    # Elevation profile.
    profile = QRectF(110, height - 370, width - 170, 280)
    painter.setPen(QPen(QColor("#bdbdbd"), 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(profile)
    distance, ground = track.distance[run], track.ground[run]
    low, high = ground.min() - 2, ground.max() + 2
    area = QPainterPath()
    area.moveTo(QPointF(profile.left(), profile.bottom()))
    for d, z in zip(distance[::5], ground[::5]):
        area.lineTo(QPointF(profile.left() + d / distance[-1] * profile.width(),
                            profile.bottom() - (z - low) / (high - low) * profile.height()))
    area.lineTo(QPointF(profile.right(), profile.bottom()))
    area.closeSubpath()
    painter.setPen(QPen(QColor("#2e7d32"), 4))
    painter.setBrush(QColor(46, 125, 50, 60))
    painter.drawPath(area)
    painter.setPen(QColor("#424242"))
    painter.drawText(QPointF(profile.left(), profile.bottom() + 40), "0 m")
    painter.drawText(QPointF(profile.right() - 120, profile.bottom() + 40), f"{distance[-1]:.0f} m")
    painter.drawText(QPointF(10, profile.top() + 30), f"{high:.0f} m")
    painter.drawText(QPointF(10, profile.bottom()), f"{low:.0f} m")
    painter.drawText(QPointF(profile.left() + 12, profile.top() + 36), "Elevation")
    painter.end()
    image.save(str(path))
