"""X6 identity/calibration subset ported from VR180 Silver Bullet (MIT).

See THIRD_PARTY_NOTICES.md. No IMU records or video payloads are read here.
"""
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import struct

MAGIC = b"8db42d694ccc418790edff439fe026bf"


@dataclass
class Lens:
    xi: float = 0.0  # zero selects the measured KB fallback
    focal: tuple = (1044.389 / 3840, 1044.389 / 3840)
    center: tuple = (0.5, 0.5)
    radial: tuple = (0.087926, -0.023232, 0.001227, -0.000388, 0.0)
    tangential: tuple = (0.0,) * 4
    prism: tuple = (0.0,) * 4


@dataclass
class Metadata:
    model: str = "Unknown camera"
    lenses: list = field(default_factory=lambda: [Lens(), Lens()])
    reversed_tracks: bool = True
    calibrated: bool = False
    note: str = "Measured X6 fallback; factory calibration unavailable"


def fields(data):
    """Bounded protobuf wire reader; unknown fields need no generated schema."""
    pos = 0

    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError("Truncated protobuf varint")
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("Oversized protobuf varint")

    while pos < len(data):
        tag = varint()
        wire = tag & 7
        if tag >> 3 == 0:
            raise ValueError("Invalid protobuf tag")
        if wire == 0:
            value = varint()
        else:
            size = varint() if wire == 2 else {1: 8, 5: 4}.get(wire)
            if size is None or size > len(data) - pos:
                raise ValueError("Invalid protobuf field length/type")
            value = data[pos:pos + size]
            pos += size
        yield tag >> 3, value


def parse_identity(data):
    meta = Metadata()
    values = dict(fields(data))
    meta.model = values.get(2, b"Unknown camera").decode("utf-8", errors="replace")
    order, kind = values.get(80), values.get(131)
    meta.reversed_tracks = order == 1 if order in (1, 2) else kind != 3
    crop = dict(fields(values[27])) if 27 in values else {}
    sw, sh, dw, dh = (crop.get(i, default) for i, default in
                       ((1, 7744), (2, 7744), (3, 7680), (4, 7680)))
    if min(sw, sh, dw, dh) <= 0:
        raise ValueError("Invalid sensor crop")
    signed = lambda x: (x & 0xffffffff) - (0x100000000 if x & 0x80000000 else 0)
    ox = (sw - dw) / 2 + signed(crop.get(5, 0))
    oy = (sh - dh) / 2 + signed(crop.get(6, 0))
    for match in re.finditer(rb"(?<![0-9_.eE-])2_[0-9_.eE-]+", data):
        try:
            numbers = [float(v) for v in match[0].decode().split("_")[1:]]
        except ValueError:
            continue
        if len(numbers) < 54 or not all(math.isfinite(v) for v in numbers):
            continue
        lenses = []
        for n in (numbers[:27], numbers[27:54]):
            if min(n[0], n[1], n[2], n[24], n[25]) <= 0:
                break
            half_origin = math.floor(n[3] / n[25]) * n[25]
            lenses.append(Lens(n[0], (n[1] / dw, n[2] / dh),
                               ((n[3] - half_origin - ox) / dw, (n[4] - oy) / dh),
                               tuple(n[11:16]), tuple(n[16:20]),
                               (n[20], n[22], n[21], n[23])))
        if len(lenses) == 2:
            meta.lenses = lenses
            meta.calibrated = True
            meta.note = "Factory lens calibration; approximate 360 seam alignment"
            break
    return meta


def read_metadata(path):
    """Read only the small identity record; validate offsets before seeking."""
    with Path(path).open("rb") as source:
        length = source.seek(0, 2)
        if length < 88:
            return Metadata()
        source.seek(-78, 2)
        footer = source.read(78)
        if footer[46:] != MAGIC:
            return Metadata()
        directory_size = struct.unpack_from("<I", footer, 2)[0]
        trailer_size = struct.unpack_from("<I", footer, 38)[0]
        base = length - trailer_size - 10
        directory_start = length - 78 - directory_size
        if (base < 0 or not 0 < directory_size <= 1048576 or
                directory_size % 10 or directory_start < base):
            raise ValueError("Invalid INSV trailer directory")
        source.seek(directory_start)
        candidates = []
        for record, size, offset in struct.iter_unpack("<HII", source.read(directory_size)):
            start = base + offset + 10
            if record == 0x0101 and 0 < size <= 4 * 1048576 and start + size <= directory_start:
                candidates.append((size, start))
        if not candidates:
            return Metadata()
        size, start = max(candidates)
        source.seek(start)
        return parse_identity(source.read(size))
