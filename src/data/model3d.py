"""3D model attachments: a folder with ``placement.json`` and the glTF binary (GLB) it names.

``load_placement`` reads the placement (schema 2): the model's anchor in its own scene frame,
the WGS84 point it stands on, a scale and a rotation into local East/North/Up metres:

    enu = scale * R(rotation_xyzw) * (p - model_anchor)

``load_model`` reads the core glTF 2.0 subset a model builder writes: triangle meshes with
positions, indices, ``TEXCOORD_0`` and ``COLOR_0``, node transforms (every instance kept),
base colour factors and embedded base colour images. Anything else is refused with a reason.

``model_matrix`` places the model in a 3D plot's data units, at the plot's 0, 0, 0 (the model's
anchor there, east/north/up along X/Y/Z) or at its GNSS position (latitude and longitude axes).
"""

from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.data.axis_scale import (
    METRES_PER_DEGREE_LATITUDE, METRES_PER_DEGREE_LONGITUDE, METRES_PER_UNIT, ROLE_LATITUDE, ROLE_LONGITUDE,
    ROLE_TIME, coordinate_scale, geographic_roles,
)

PLACEMENT_FILE = "placement.json"
PLACEMENT_SCHEMA = 2
CALIBRATED = "CALIBRATED"
# Where a 3D plot places a model: its anchor at the plot's 0, 0, 0, or at its GNSS position.
PLACE_ORIGIN = "origin"
PLACE_GNSS = "gnss"
PLACEMENTS = {PLACE_ORIGIN: "At 0, 0, 0", PLACE_GNSS: "At GNSS position"}
# A model expanded beyond this many vertices (every instance drawn) is refused.
MAXIMUM_VERTICES = 20_000_000

_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
_COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_TRIANGLES = 4


class ModelError(ValueError):
    """A model or placement file LODGaZilla cannot read, with the reason."""


@dataclass(frozen=True)
class ModelPlacement:
    model_file: str
    model_anchor: tuple[float, float, float]
    latitude: float | None
    longitude: float | None
    altitude_metres: float | None
    scale: float
    rotation_xyzw: tuple[float, float, float, float]
    calibration_status: str

    @property
    def calibrated(self) -> bool:
        return self.calibration_status == CALIBRATED

    @property
    def has_gnss_anchor(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass
class ModelPart:
    """One glTF primitive with every instance of it, in the model's scene frame."""
    name: str
    positions: np.ndarray          # N x 3 float32
    normals: np.ndarray            # N x 3 float32
    indices: np.ndarray            # M x 3 uint32
    uv: np.ndarray | None          # N x 2 float32 (glTF: v = 0 at the image's top)
    colors: np.ndarray | None      # N x 4 float32, display (sRGB) values
    image: int | None              # index into Model.images
    base_color: tuple[float, float, float, float]  # display (sRGB) values and alpha
    alpha_mode: str                # "OPAQUE", "MASK" or "BLEND"
    alpha_cutoff: float


@dataclass
class Model:
    parts: list[ModelPart]
    images: list[bytes] = field(default_factory=list)  # embedded JPEG or PNG files

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        points = [part.positions for part in self.parts if len(part.positions)]
        if not points:
            return np.zeros(3), np.zeros(3)
        return (np.min([p.min(axis=0) for p in points], axis=0), np.max([p.max(axis=0) for p in points], axis=0))


def _number(value, name: str) -> float:  # type: ignore[no-untyped-def]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ModelError(f"{PLACEMENT_FILE}: {name} must be a number")
    return float(value)


def _optional_number(value, name: str) -> float | None:  # type: ignore[no-untyped-def]
    return None if value is None else _number(value, name)


def _numbers(value, count: int, name: str) -> tuple[float, ...]:  # type: ignore[no-untyped-def]
    if not isinstance(value, list) or len(value) != count:
        raise ModelError(f"{PLACEMENT_FILE}: {name} must list {count} numbers")
    return tuple(_number(item, name) for item in value)


def load_placement(path: str | Path) -> ModelPlacement:
    """Read and check a ``placement.json`` (schema 2)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelError(f"Cannot read {PLACEMENT_FILE}: {error}") from error
    if not isinstance(data, dict):
        raise ModelError(f"{PLACEMENT_FILE} must contain an object")
    if data.get("schema_version") != PLACEMENT_SCHEMA:
        raise ModelError(f"{PLACEMENT_FILE}: schema_version {data.get('schema_version')!r} is not supported "
                         f"(expected {PLACEMENT_SCHEMA})")
    model_file = data.get("model")
    if not isinstance(model_file, str) or not model_file.strip() or Path(model_file).name != model_file:
        raise ModelError(f"{PLACEMENT_FILE}: model must name a file in the same folder")
    geographic = data.get("geographic_anchor") or {}
    if not isinstance(geographic, dict):
        raise ModelError(f"{PLACEMENT_FILE}: geographic_anchor must be an object")
    latitude = _optional_number(geographic.get("latitude"), "latitude")
    longitude = _optional_number(geographic.get("longitude"), "longitude")
    if latitude is not None and abs(latitude) > 90 or longitude is not None and abs(longitude) > 180:
        raise ModelError(f"{PLACEMENT_FILE}: the geographic anchor is outside WGS84 latitude and longitude")
    scale = _number(data.get("scale", 1.0), "scale")
    if scale <= 0:
        raise ModelError(f"{PLACEMENT_FILE}: scale must be positive")
    rotation = _numbers(data.get("rotation_xyzw", [0, 0, 0, 1]), 4, "rotation_xyzw")
    length = math.sqrt(sum(value * value for value in rotation))
    if length < 1e-9:
        raise ModelError(f"{PLACEMENT_FILE}: rotation_xyzw must not be zero")
    return ModelPlacement(
        model_file=model_file,
        model_anchor=_numbers(data.get("model_anchor", [0, 0, 0]), 3, "model_anchor"),  # type: ignore[arg-type]
        latitude=latitude, longitude=longitude,
        altitude_metres=_optional_number(geographic.get("altitude_metres"), "altitude_metres"),
        scale=scale,
        rotation_xyzw=tuple(value / length for value in rotation),  # type: ignore[arg-type]
        calibration_status=str(data.get("calibration_status") or "UNKNOWN"),
    )


def model_folder_files(folder: str | Path) -> tuple[ModelPlacement, Path]:
    """A model attachment folder's placement and its model file, both checked to exist."""
    folder = Path(folder)
    placement = load_placement(folder / PLACEMENT_FILE)
    model_path = folder / placement.model_file
    if not model_path.is_file():
        raise ModelError(f"{folder.name}: the model file {placement.model_file} is missing")
    return placement, model_path


def calibration_text(placement: ModelPlacement) -> str:
    """The placement's calibration status for the user, as the model's makers ask."""
    if placement.calibrated:
        return "Calibrated placement"
    return f"Approximate placement, not calibrated ({placement.calibration_status})"


# --- Placement maths -------------------------------------------------------------------------------

def quaternion_matrix(xyzw) -> np.ndarray:  # type: ignore[no-untyped-def]
    """The 3 x 3 rotation of a normalised x, y, z, w quaternion."""
    x, y, z, w = (float(value) for value in xyzw)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def model_to_enu(placement: ModelPlacement) -> np.ndarray:
    """4 x 4 matrix from the model's scene frame to East/North/Up metres around its anchor."""
    rotation = placement.scale * quaternion_matrix(placement.rotation_xyzw)
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = -rotation @ np.asarray(placement.model_anchor, dtype=float)
    return matrix


def _metres_per_unit(unit: str) -> float:
    """A length unit's metres; an axis without a known length unit counts in metres."""
    return METRES_PER_UNIT.get(unit.strip().casefold(), 1.0)


def enu_to_data(placement: ModelPlacement, mode: str, units, roles, low, high):  # type: ignore[no-untyped-def]
    """4 x 4 matrix from East/North/Up metres to the plot's X/Y/Z data units, or None and the reason.

    At the origin the anchor is the plot's 0, 0, 0 with east, north and up along X, Y and Z.
    At the GNSS position the latitude and longitude axes (by name, or two axes in degrees) take
    north and east around the anchor, in degrees or fixed-point degrees as the data holds them,
    and the third axis takes up, from the anchor's altitude (0 when it is unknown).
    """
    matrix = np.eye(4)
    if mode == PLACE_ORIGIN:
        for axis in range(3):
            matrix[axis, axis] = 1.0 / _metres_per_unit(units[axis])
        return matrix, ""
    if mode != PLACE_GNSS:
        return None, f"Unknown placement {mode!r}"
    if not placement.has_gnss_anchor:
        return None, "The model has no GNSS anchor"
    roles = geographic_roles(units, roles)
    latitude = next((index for index, role in enumerate(roles) if role == ROLE_LATITUDE), None)
    longitude = next((index for index, role in enumerate(roles) if role == ROLE_LONGITUDE), None)
    if latitude is None or longitude is None:
        return None, "Needs latitude and longitude axes"
    up = 3 - latitude - longitude
    if roles[up] == ROLE_TIME:
        return None, f"{'XYZ'[up]} is time, not altitude"
    scales = [coordinate_scale(max(abs(low[index]), abs(high[index])), limit, unit=units[index])
              for index, limit in ((latitude, 90.0), (longitude, 180.0))]
    cos_latitude = max(1e-9, math.cos(math.radians(placement.latitude)))
    matrix = np.zeros((4, 4))
    matrix[3, 3] = 1.0
    # North and east metres to degrees (the Equal scale's metres per degree), then to stored units.
    matrix[latitude, 1] = scales[0] / METRES_PER_DEGREE_LATITUDE
    matrix[latitude, 3] = scales[0] * placement.latitude
    matrix[longitude, 0] = scales[1] / (METRES_PER_DEGREE_LONGITUDE * cos_latitude)
    matrix[longitude, 3] = scales[1] * placement.longitude
    metres = _metres_per_unit(units[up])
    matrix[up, 2] = 1.0 / metres
    matrix[up, 3] = (placement.altitude_metres or 0.0) / metres
    return matrix, ""


def box_matrix(low, high, stretch) -> np.ndarray:  # type: ignore[no-untyped-def]
    """4 x 4 matrix from data units to a 3D plot's -1..1 box, as its traces are drawn."""
    low, high = np.asarray(low, dtype=float), np.asarray(high, dtype=float)
    half = np.where(high > low, (high - low) / 2, 1.0)
    factors = np.asarray(stretch, dtype=float) / half
    matrix = np.diag([*factors, 1.0])
    matrix[:3, 3] = -(low + high) / 2 * factors
    return matrix


def model_matrix(placement: ModelPlacement, mode: str, units, roles, low, high, stretch):  # type: ignore[no-untyped-def]
    """The model's scene frame to the plot's box, or None and the reason it cannot be placed."""
    to_data, reason = enu_to_data(placement, mode, units, roles, low, high)
    if to_data is None:
        return None, reason
    return box_matrix(low, high, stretch) @ to_data @ model_to_enu(placement), ""


# --- GLB reading -----------------------------------------------------------------------------------

def linear_to_srgb(values: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(values, dtype=float), 0.0, 1.0)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * np.power(values, 1 / 2.4) - 0.055)


def _read_glb(path: Path) -> tuple[dict, bytes]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ModelError(f"Cannot read {path.name}: {error}") from error
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise ModelError(f"{path.name} is not a binary glTF (GLB) file")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ModelError(f"{path.name}: glTF version {version} is not supported (expected 2)")
    offset, document, binary = 12, None, b""
    while offset + 8 <= min(length, len(data)):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + chunk_length]
        if chunk_type == _CHUNK_JSON and document is None:
            document = json.loads(chunk.decode("utf-8"))
        elif chunk_type == _CHUNK_BIN and not binary:
            binary = chunk
        offset += 8 + chunk_length
    if not isinstance(document, dict):
        raise ModelError(f"{path.name} has no glTF JSON chunk")
    return document, binary


class _Reader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.gltf, binary = _read_glb(path)
        required = self.gltf.get("extensionsRequired") or []
        if required:
            raise ModelError(f"{path.name} needs glTF extensions LODGaZilla does not read: {', '.join(required)}")
        self.buffers = [self._buffer(index, buffer, binary) for index, buffer in enumerate(self.gltf.get("buffers", []))]

    def _buffer(self, index: int, buffer: dict, binary: bytes) -> bytes:
        uri = buffer.get("uri")
        if uri is None:
            if index != 0:
                raise ModelError(f"{self.path.name}: buffer {index} has no data")
            return binary
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        try:
            return (self.path.parent / uri).read_bytes()
        except OSError as error:
            raise ModelError(f"{self.path.name}: cannot read its buffer {uri}: {error}") from error

    def view_bytes(self, index: int) -> tuple[bytes, int]:
        view = self.gltf["bufferViews"][index]
        buffer = self.buffers[view.get("buffer", 0)]
        start = view.get("byteOffset", 0)
        return buffer[start:start + view["byteLength"]], int(view.get("byteStride", 0))

    def accessor(self, index: int) -> np.ndarray:
        accessor = self.gltf["accessors"][index]
        if "sparse" in accessor:
            raise ModelError(f"{self.path.name}: sparse accessors are not supported")
        dtype = np.dtype(_COMPONENTS[accessor["componentType"]])
        width, count = _WIDTHS[accessor["type"]], int(accessor["count"])
        if "bufferView" not in accessor:
            return np.zeros((count, width), dtype=np.float32)
        data, stride = self.view_bytes(accessor["bufferView"])
        offset, item = int(accessor.get("byteOffset", 0)), dtype.itemsize * width
        stride = stride or item
        if count and offset + stride * (count - 1) + item > len(data):
            raise ModelError(f"{self.path.name}: accessor {index} runs past its buffer")
        values = np.lib.stride_tricks.as_strided(
            np.frombuffer(data, dtype=np.uint8, offset=offset), shape=(count, item), strides=(stride, 1))
        array = np.ascontiguousarray(values).view(dtype).reshape(count, width)
        if accessor.get("normalized") and dtype.kind in "iu":
            array = np.maximum(array.astype(np.float32) / np.iinfo(dtype).max, -1.0)
        return array

    def image(self, index: int) -> bytes:
        image = self.gltf["images"][index]
        if "bufferView" in image:
            return self.view_bytes(image["bufferView"])[0]
        uri = image.get("uri", "")
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        try:
            return (self.path.parent / uri).read_bytes()
        except OSError as error:
            raise ModelError(f"{self.path.name}: cannot read its image {uri}: {error}") from error


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T  # glTF stores columns
    matrix = np.eye(4)
    matrix[:3, :3] = quaternion_matrix(node.get("rotation", [0, 0, 0, 1])) * np.asarray(node.get("scale", [1, 1, 1]))
    matrix[:3, 3] = node.get("translation", [0, 0, 0])
    return matrix


def _mesh_instances(gltf: dict) -> dict[int, list[np.ndarray]]:
    """Each mesh's world matrices: every node that draws it, through the scene's node tree."""
    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes", [])
    roots = scenes[gltf.get("scene", 0)].get("nodes", []) if scenes else range(len(nodes))
    instances: dict[int, list[np.ndarray]] = {}
    # Depth first in the file's order (the stack pops its last entry first).
    pending = [(index, np.eye(4), 0) for index in reversed(list(roots))]
    while pending:
        index, parent, depth = pending.pop()
        if depth > 64:
            raise ModelError("The model's node tree is too deep")
        node = nodes[index]
        world = parent @ _node_matrix(node)
        if "mesh" in node:
            instances.setdefault(int(node["mesh"]), []).append(world)
        pending.extend((child, world, depth + 1) for child in reversed(node.get("children", [])))
    return instances


def _vertex_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    corners = positions[indices]
    faces = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals = np.zeros_like(positions, dtype=np.float64)
    for column in range(3):
        np.add.at(normals, indices[:, column], faces)
    length = np.linalg.norm(normals, axis=1)
    valid = length > 1e-20
    normals[valid] /= length[valid, None]
    normals[~valid] = (0.0, 0.0, 1.0)
    return normals.astype(np.float32)


def load_model(path: str | Path) -> Model:
    """Read a GLB model, every mesh instance placed by its node transforms."""
    reader = _Reader(Path(path))
    gltf = reader.gltf
    materials = gltf.get("materials", [])
    textures = gltf.get("textures", [])
    image_indexes: dict[int, int] = {}
    images: list[bytes] = []
    parts: list[ModelPart] = []
    vertices = 0
    try:
        for mesh_index, matrices in sorted(_mesh_instances(gltf).items()):
            mesh = gltf["meshes"][mesh_index]
            for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
                if primitive.get("mode", _TRIANGLES) != _TRIANGLES or "POSITION" not in primitive.get("attributes", {}):
                    continue
                attributes = primitive["attributes"]
                base = reader.accessor(attributes["POSITION"]).astype(np.float64)
                indices = (reader.accessor(primitive["indices"]).reshape(-1) if "indices" in primitive
                           else np.arange(len(base))).astype(np.uint32)
                if len(indices) % 3 or (len(indices) and int(indices.max()) >= len(base)):
                    raise ModelError(f"{mesh.get('name', mesh_index)}: invalid triangle indices")
                vertices += len(base) * len(matrices)
                if vertices > MAXIMUM_VERTICES:
                    raise ModelError(f"The model has more than {MAXIMUM_VERTICES:,} vertices")
                positions = np.concatenate([base @ matrix[:3, :3].T + matrix[:3, 3] for matrix in matrices])
                triangles = np.concatenate([indices + offset * len(base) for offset in range(len(matrices))])
                triangles = triangles.reshape(-1, 3).astype(np.uint32)
                uv = reader.accessor(attributes["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in attributes else None
                colors = None
                if "COLOR_0" in attributes:
                    raw = reader.accessor(attributes["COLOR_0"]).astype(np.float64)
                    alpha = raw[:, 3:4] if raw.shape[1] == 4 else np.ones((len(raw), 1))
                    colors = np.hstack([linear_to_srgb(raw[:, :3]), alpha]).astype(np.float32)
                material = materials[primitive["material"]] if "material" in primitive else {}
                pbr = material.get("pbrMetallicRoughness", {})
                factor = np.asarray(pbr.get("baseColorFactor", [1, 1, 1, 1]), dtype=float)
                image = None
                texture = pbr.get("baseColorTexture")
                if uv is not None and texture is not None and texture.get("texCoord", 0) == 0:
                    source = textures[texture["index"]].get("source")
                    if source is not None:
                        if source not in image_indexes:
                            image_indexes[source] = len(images)
                            images.append(reader.image(source))
                        image = image_indexes[source]
                parts.append(ModelPart(
                    name=str(mesh.get("name") or f"mesh {mesh_index}"),
                    positions=positions.astype(np.float32),
                    normals=_vertex_normals(positions, triangles),
                    indices=triangles,
                    uv=None if uv is None else np.tile(uv, (len(matrices), 1)),
                    colors=None if colors is None else np.tile(colors, (len(matrices), 1)),
                    image=image,
                    base_color=(*linear_to_srgb(factor[:3]).tolist(), float(factor[3])),  # type: ignore[arg-type]
                    alpha_mode=str(material.get("alphaMode", "OPAQUE")),
                    alpha_cutoff=float(material.get("alphaCutoff", 0.5)),
                ))
    except (KeyError, IndexError, TypeError) as error:
        raise ModelError(f"{Path(path).name}: an incomplete or unsupported glTF structure ({error})") from error
    if not parts:
        raise ModelError(f"{Path(path).name} has no triangle meshes")
    return Model(parts, images)
