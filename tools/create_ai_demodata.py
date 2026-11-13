"""Synthetic test files for Magic drop and Create plugin: `python tools/create_ai_demodata.py`.

Writes tests/AI_demodata/: small and big files in CSV, JSON, JSON Lines, YAML, text, GPX,
NMEA and binary formats. Each binary format comes with its description as a document or as
C, C++ or Python source, except one left without, for the AI to work out. Everything is
generated, with a fixed seed, so the true contents are known (see tests/AI_demodata/README.md);
no real person's data is used. The files under big/ are ignored by Git; run this script again
to make them.
"""
from __future__ import annotations

import argparse
import json
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "tests" / "AI_demodata"
START = datetime(2026, 5, 14, 7, 30, 0, tzinfo=timezone.utc)
# A made-up loop near a made-up start point; no real route.
LAT0, LON0 = 61.4978, 23.7610


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def workout(seconds: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """A 1 Hz run: speed, heart rate, cadence, altitude and a loop track."""
    t = np.arange(seconds, dtype=float)
    warm = np.clip(t / 600.0, 0, 1)
    speed = 2.2 + 1.0 * warm + 0.25 * np.sin(t / 97.0) + rng.normal(0, 0.05, seconds)
    hr = 95 + 60 * warm + 8 * np.sin(t / 180.0) + rng.normal(0, 1.2, seconds)
    cadence = 150 + 22 * warm + rng.normal(0, 1.5, seconds)
    altitude = 110 + 18 * np.sin(t / 420.0) + 6 * np.sin(t / 77.0)
    distance = np.cumsum(speed)
    angle = 2 * np.pi * distance / max(distance[-1], 1.0)
    radius_deg = distance[-1] / (2 * np.pi) / 111_320.0
    lat = LAT0 + radius_deg * (np.sin(angle) + 0.08 * np.sin(5 * angle))
    lon = LON0 + radius_deg * (1 - np.cos(angle)) / np.cos(np.radians(LAT0))
    return {"t": t, "speed": speed, "hr": np.round(hr), "cadence": np.round(cadence),
            "altitude": altitude, "distance": distance, "lat": lat, "lon": lon}


# ---- Text formats ---------------------------------------------------------------------------

def weather_csv(path: Path, rng: np.random.Generator) -> None:
    """Finnish locale: semicolons, decimal commas, local day.month.year times, UTF-8 BOM."""
    rows = ["Aika;Lämpötila (°C);Ilmanpaine (hPa);Kosteus (%);Tuuli (m/s)"]
    local = START.astimezone(timezone(timedelta(hours=3))).replace(minute=0)
    for index in range(7 * 24 * 6):
        moment = local + timedelta(minutes=10 * index)
        day = index / 144.0
        temperature = 12 + 6 * np.sin(2 * np.pi * (day - 0.375)) + rng.normal(0, 0.3)
        pressure = 1012 + 8 * np.sin(day / 1.3) + rng.normal(0, 0.2)
        humidity = np.clip(70 - 2.5 * (temperature - 12) + rng.normal(0, 2), 20, 100)
        wind = abs(3 + 2 * np.sin(day * 3.1) + rng.normal(0, 0.8))
        values = [f"{temperature:.1f}", f"{pressure:.1f}", f"{humidity:.0f}", f"{wind:.1f}"]
        rows.append(moment.strftime("%d.%m.%Y %H:%M") + ";" + ";".join(v.replace(".", ",") for v in values))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8-sig")


def bike_csv(path: Path, rng: np.random.Generator) -> None:
    """Comment header, Unix millisecond times, dropouts (missing rows) and empty cells."""
    seconds = 90 * 60
    t = np.arange(seconds)
    power = np.clip(180 + 60 * np.sin(t / 240.0) + rng.normal(0, 25, seconds), 0, None)
    power[(t > 1800) & (t < 1860)] = 0  # Coasting downhill.
    cadence = np.where(power > 0, 85 + rng.normal(0, 4, seconds), 0)
    hr = 110 + 0.12 * power + 10 * np.clip(t / 900, 0, 1) + rng.normal(0, 1.5, seconds)
    speed = 24 + power / 40 + rng.normal(0, 0.6, seconds)
    dropped = (t % 600 > 400) & (t % 600 < 407)  # A few lost seconds every 10 minutes.
    start_ms = int(START.timestamp() * 1000)
    lines = ["# Exported by VeloLog 2.3 (synthetic test data)", "# Bike: road, wheel 2105 mm",
             "# Device serial: VL-0042-77", "timestamp_ms,power_w,cadence_rpm,heart_rate_bpm,speed_kmh"]
    for index in np.flatnonzero(~dropped):
        hr_text = "" if index % 997 == 0 else f"{hr[index]:.0f}"  # Strap contact lost.
        lines.append(f"{start_ms + 1000 * index},{power[index]:.0f},{cadence[index]:.0f},{hr_text},"
                     f"{speed[index]:.2f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def imu_csv(path: Path, rng: np.random.Generator, *, minutes: int) -> None:
    """200 Hz accelerometer (g) and gyroscope (deg/s), seconds from the start."""
    rate = 200
    count = minutes * 60 * rate
    t = np.arange(count) / rate
    step = 2 * np.pi * 2.8 * t  # Running steps at 2.8 Hz.
    data = np.column_stack([
        t,
        0.3 * np.sin(step) + rng.normal(0, 0.02, count),
        0.2 * np.sin(step / 2) + rng.normal(0, 0.02, count),
        1.0 + 0.8 * np.abs(np.sin(step)) + rng.normal(0, 0.03, count),
        40 * np.sin(step + 0.3) + rng.normal(0, 1, count),
        15 * np.sin(step / 2) + rng.normal(0, 1, count),
        5 * np.sin(t / 3) + rng.normal(0, 1, count),
    ])
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("t_s,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps\n")
        np.savetxt(stream, data, fmt=["%.3f"] + ["%.4f"] * 3 + ["%.2f"] * 3, delimiter=",")


def run_json(path: Path, rng: np.random.Generator, *, minutes: int) -> None:
    """Nested JSON: device and session metadata, laps, events and 1 Hz sample arrays."""
    run = workout(minutes * 60, rng)
    laps = [{"index": lap + 1, "start_s": lap * 600, "duration_s": 600,
             "distance_m": round(float(run["distance"][min((lap + 1) * 600, len(run["t"])) - 1]
                                       - (run["distance"][lap * 600 - 1] if lap else 0)), 1)}
            for lap in range(minutes // 10)]
    document = {
        "format": "trackr-session", "version": 3,
        "device": {"model": "Trackr Pulse 5", "serial": "TP5-118-2093", "firmware": "5.12.0"},
        "session": {"start": iso(START), "sport": "running", "timezone": "+03:00"},
        "laps": laps,
        "events": [{"t_s": 0, "type": "start"}, {"t_s": 1234, "type": "pause"},
                   {"t_s": 1290, "type": "resume"}, {"t_s": minutes * 60 - 1, "type": "stop"}],
        "samples": {
            "interval_s": 1,
            "heart_rate_bpm": run["hr"].astype(int).tolist(),
            "speed_mps": np.round(run["speed"], 2).tolist(),
            "cadence_spm": run["cadence"].astype(int).tolist(),
            "altitude_m": np.round(run["altitude"], 1).tolist(),
            "position": [[round(float(a), 6), round(float(b), 6)] for a, b in zip(run["lat"], run["lon"])],
        },
    }
    path.write_text(json.dumps(document, indent=1), encoding="utf-8")


def events_jsonl(path: Path, rng: np.random.Generator) -> None:
    """JSON Lines from a home sensor hub: mixed sensors, value types and irregular times."""
    moment = START
    door, lines = "closed", []
    for _ in range(2500):
        moment += timedelta(seconds=float(rng.exponential(40)))
        kind = rng.choice(["temperature", "door", "battery", "motion"], p=[0.6, 0.1, 0.05, 0.25])
        if kind == "temperature":
            hours = (moment - START).total_seconds() / 3600
            record = {"sensor": "temp_livingroom", "value": round(21 + 1.5 * np.sin(hours / 4) + rng.normal(0, 0.1), 2),
                      "unit": "C"}
        elif kind == "door":
            door = "open" if door == "closed" else "closed"
            record = {"sensor": "door_front", "value": door}
        elif kind == "battery":
            record = {"sensor": "hub", "value": round(3.0 - (moment - START).total_seconds() / 3e6, 3), "unit": "V"}
        else:
            record = {"sensor": "motion_hall", "value": True}
        lines.append(json.dumps({"ts": moment.isoformat(timespec="milliseconds"), **record}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def dive_yaml(path: Path, rng: np.random.Generator) -> None:
    """A dive log in YAML: metadata, gas mixes and a 2 s depth and temperature profile."""
    seconds = np.arange(0, 46 * 60, 2)
    depth = np.interp(seconds, [0, 180, 1500, 1800, 2400, 2580, 2760], [0, 24, 26, 15, 15, 5, 0])
    depth = np.clip(depth + rng.normal(0, 0.05, len(seconds)), 0, None)
    temperature = 16 - 0.25 * depth + rng.normal(0, 0.05, len(seconds))
    lines = ["# Dive log export (synthetic test data)", "dive:", "  number: 214",
             f"  start: {iso(START)}", "  site: Test quarry", "  computer:", "    model: Deep 7",
             "    serial: \"0007-3321\"", "  gases:", "    - {name: air, o2: 0.21}",
             "    - {name: EAN50, o2: 0.50, switch_at_s: 2400}", "  units: {depth: m, temperature: C}",
             "  samples:"]
    lines += [f"    - {{t: {int(s)}, depth: {d:.2f}, temp: {c:.1f}}}" for s, d, c in zip(seconds, depth, temperature)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fixed_width_txt(path: Path, rng: np.random.Generator) -> None:
    """A data logger's fixed-width report: header block, units row, 5 s rows, a footer."""
    rows = 6 * 60 * 12
    lines = ["LOGGER XT-200        SERIAL 004211        FW 1.07", f"START {START:%Y/%m/%d %H:%M:%S} UTC",
             "INTERVAL 5 s         CHANNELS 4", "",
             f"{'ELAPSED':>8}{'CH1':>10}{'CH2':>10}{'CH3':>10}{'CH4':>8}",
             f"{'[s]':>8}{'[V]':>10}{'[mA]':>10}{'[degC]':>10}{'[-]':>8}"]
    for index in range(rows):
        t = 5 * index
        voltage = 12.6 - t / 40000 + rng.normal(0, 0.005)
        current = 450 + 120 * np.sin(t / 900) + rng.normal(0, 4)
        temperature = 25 + 10 * (1 - np.exp(-t / 3000)) + rng.normal(0, 0.1)
        state = 1 if (t // 1800) % 2 else 0
        cells = f"{t:>8d}{voltage:>10.3f}{current:>10.1f}{temperature:>10.2f}{state:>8d}"
        lines.append(cells if index % 1500 != 7 else f"{t:>8d}{'---':>10}{current:>10.1f}{temperature:>10.2f}{state:>8d}")
    lines += ["", "END OF DATA  ROWS %d" % rows]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def console_log(path: Path, rng: np.random.Generator) -> None:
    """A device's serial console: values inside free text, mixed with other messages."""
    lines, battery = [], 4.12
    for second in range(40 * 60):
        stamp = f"[{second // 3600:02d}:{second // 60 % 60:02d}:{second % 60:02d}.{rng.integers(0, 999):03d}]"
        hr = 70 + 50 * min(second / 900, 1) + rng.normal(0, 1)
        lines.append(f"{stamp} INFO  hr={hr:.0f} bpm rr={60000 / hr:.0f}ms")
        if second % 10 == 0:
            battery -= 0.0007
            lines.append(f"{stamp} INFO  batt={battery:.3f}V temp={31 + rng.normal(0, 0.2):.1f}C")
        if second % 613 == 300:
            lines.append(f"{stamp} WARN  ble: link lost, reconnecting (attempt 1)")
            lines.append(f"{stamp} INFO  ble: connected, mtu=247")
        if second == 1500:
            lines.append(f"{stamp} ERROR flash: write failed at 0x0003F200, retrying")
    path.write_text("\n".join(["Boot v2.0.4 (synthetic test data)", "Sensor OK, logging"] + lines) + "\n",
                    encoding="utf-8")


def gpx(path: Path, rng: np.random.Generator, *, minutes: int, segments: int = 1, name: str) -> None:
    """GPX 1.1 with elevation, times and Garmin-style heart rate and cadence extensions."""
    run = workout(minutes * 60, rng)
    head = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<gpx version="1.1" creator="LODGaZilla synthetic test data" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">',
            f"  <metadata><name>{name}</name><time>{iso(START)}</time></metadata>", "  <trk>",
            f"    <name>{name}</name><type>running</type>"]
    body, bounds = [], np.linspace(0, len(run["t"]), segments + 1).astype(int)
    for segment in range(segments):
        body.append("    <trkseg>")
        for index in range(bounds[segment], bounds[segment + 1]):
            # A break between segments: the second one starts 20 minutes later.
            moment = START + timedelta(seconds=float(run["t"][index]) + 1200 * segment)
            body.append(f'      <trkpt lat="{run["lat"][index]:.7f}" lon="{run["lon"][index]:.7f}">'
                        f'<ele>{run["altitude"][index]:.1f}</ele><time>{iso(moment)}</time>'
                        f'<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{run["hr"][index]:.0f}</gpxtpx:hr>'
                        f'<gpxtpx:cad>{run["cadence"][index] / 2:.0f}</gpxtpx:cad></gpxtpx:TrackPointExtension>'
                        '</extensions></trkpt>')
        body.append("    </trkseg>")
    path.write_text("\n".join(head + body + ["  </trk>", "</gpx>"]) + "\n", encoding="utf-8")


def nmea(path: Path, rng: np.random.Generator) -> None:
    """A GNSS receiver's NMEA 0183 log: GGA and RMC sentences at 1 Hz, with checksums."""
    run = workout(20 * 60, rng)

    def sentence(body: str) -> str:
        checksum = 0
        for character in body:
            checksum ^= ord(character)
        return f"${body}*{checksum:02X}"

    def angle(value: float, positive: str, negative: str, width: int) -> str:
        degrees = int(abs(value))
        return f"{degrees:0{width}d}{(abs(value) - degrees) * 60:07.4f},{positive if value >= 0 else negative}"

    lines = []
    for index in range(len(run["t"])):
        moment = START + timedelta(seconds=float(run["t"][index]))
        clock = moment.strftime("%H%M%S") + ".00"
        lat, lon = angle(run["lat"][index], "N", "S", 2), angle(run["lon"][index], "E", "W", 3)
        satellites = 9 + int(rng.integers(-2, 3))
        lines.append(sentence(f"GPGGA,{clock},{lat},{lon},1,{satellites:02d},0.9,{run['altitude'][index]:.1f},M,"
                              "18.2,M,,"))
        knots = run["speed"][index] * 1.943844
        lines.append(sentence(f"GPRMC,{clock},A,{lat},{lon},{knots:.2f},{(index * 0.3) % 360:.1f},"
                              f"{moment:%d%m%y},,,A"))
    path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")


# ---- Binary formats -------------------------------------------------------------------------

LOGGER_H = r"""/* logger_format.h - the LGR1 binary log of a motion logger (synthetic test format).
 *
 * All values are little-endian and the structures are packed (no padding).
 * A file is one lgr_header_t followed by records of header.record_size bytes,
 * one per sample, at header.sample_rate_hz. Unknown trailing bytes are ignored.
 */
#ifndef LOGGER_FORMAT_H
#define LOGGER_FORMAT_H
#include <stdint.h>

#define LGR_MAGIC "LGR1"

#pragma pack(push, 1)
typedef struct {
    char     magic[4];        /* "LGR1" */
    uint16_t version;         /* 1 */
    uint16_t record_size;     /* sizeof(lgr_record_t) = 16 */
    uint32_t sample_rate_hz;  /* samples per second */
    uint32_t start_unix_s;    /* UTC start time, seconds since 1970-01-01 */
    char     serial[16];      /* device serial number, NUL padded */
} lgr_header_t;               /* 32 bytes */

typedef struct {
    uint32_t tick_ms;         /* milliseconds since start_unix_s */
    int16_t  acc_mg[3];       /* acceleration x, y, z in milli-g */
    int16_t  temp_cdeg;       /* temperature in 0.01 degC */
    uint16_t pressure_2pa;    /* air pressure in units of 2 Pa */
    uint8_t  flags;           /* bit 0: button pressed, bit 1: low battery */
    uint8_t  reserved;        /* always 0 */
} lgr_record_t;               /* 16 bytes */
#pragma pack(pop)

#endif
"""


def logger_bin(path: Path, rng: np.random.Generator, *, rate: int, minutes: int) -> None:
    count = rate * minutes * 60
    t = np.arange(count) / rate
    records = np.zeros(count, dtype=np.dtype([("tick", "<u4"), ("acc", "<i2", 3), ("temp", "<i2"),
                                               ("pressure", "<u2"), ("flags", "u1"), ("reserved", "u1")]))
    records["tick"] = np.round(t * 1000).astype(np.uint32)
    step = 2 * np.pi * 1.9 * t
    records["acc"][:, 0] = np.round(200 * np.sin(step) + rng.normal(0, 15, count))
    records["acc"][:, 1] = np.round(120 * np.sin(step / 2) + rng.normal(0, 15, count))
    records["acc"][:, 2] = np.round(1000 + 300 * np.abs(np.sin(step)) + rng.normal(0, 20, count))
    records["temp"] = np.round(100 * (22 + 3 * np.sin(t / 600)))
    records["pressure"] = np.round((100_800 - 2 * t / 60) / 2).astype(np.uint16)
    records["flags"] = ((t % 120) < 1).astype(np.uint8) | ((t > t[-1] * 0.9).astype(np.uint8) << 1)
    header = struct.pack("<4sHHII16s", b"LGR1", 1, 16, rate, int(START.timestamp()), b"MX-20-118842")
    path.write_bytes(header + records.tobytes())


TLV_SPEC = """# TLV sensor stream (synthetic test format)

A byte stream of frames from a chest strap with GNSS. All numbers are little-endian.

## Frame

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 2 | Sync bytes `A5 5A` |
| 2 | 1 | Type |
| 3 | 1 | Payload length N (bytes) |
| 4 | N | Payload |
| 4+N | 2 | CRC-16/CCITT-FALSE (polynomial 0x1021, initial 0xFFFF, no reflection) over type, length and payload |

A frame with a wrong CRC is damaged: skip it and search for the next sync bytes. The
stream may begin in the middle of a frame.

## Payloads

`t_ms` is milliseconds since the last Time sync frame's `unix_s`.

| Type | Name | Payload |
| --- | --- | --- |
| 0x01 | Time sync | `uint32 unix_s` (UTC seconds), `uint32 t_ms` (always 0) |
| 0x10 | Heart rate | `uint32 t_ms`, `uint8 bpm`, `uint8 n`, then `n` x `uint16` RR intervals in 1/1024 s |
| 0x20 | GNSS fix | `uint32 t_ms`, `int32 lat` (1e-7 degrees), `int32 lon` (1e-7 degrees), `int16 alt` (0.1 m), `uint8 satellites` |
| 0x30 | Battery | `uint32 t_ms`, `uint16 millivolts` |
| 0x7F | Text | `uint32 t_ms`, then UTF-8 text (a log message) |

Other types may appear; skip them by their length.
"""


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def tlv_stream(path: Path, rng: np.random.Generator) -> None:
    def frame(kind: int, payload: bytes) -> bytes:
        body = bytes([kind, len(payload)]) + payload
        return b"\xA5\x5A" + body + struct.pack("<H", crc16(body))

    run = workout(30 * 60, rng)
    out = bytearray(b"\x12\x00\x9c")  # The tail of a frame from before the recording.
    out += frame(0x01, struct.pack("<II", int(START.timestamp()), 0))
    for second in range(len(run["t"])):
        t_ms = second * 1000
        hr = int(run["hr"][second])
        rr = [int(round(60 / hr * 1024 + rng.normal(0, 8)))] * (1 + (second % 3 == 0))
        heart = frame(0x10, struct.pack(f"<IBB{len(rr)}H", t_ms + 120, hr, len(rr), *rr))
        if second % 211 == 50:
            heart = heart[:-1] + bytes([heart[-1] ^ 0xFF])  # Damaged: wrong CRC.
        out += heart
        out += frame(0x20, struct.pack("<IiihB", t_ms, int(round(run["lat"][second] * 1e7)),
                                       int(round(run["lon"][second] * 1e7)), int(round(run["altitude"][second] * 10)),
                                       9 + int(rng.integers(-2, 3))))
        if second % 30 == 0:
            out += frame(0x30, struct.pack("<IH", t_ms + 500, 4100 - second // 4))
        if second == 900:
            out += frame(0x7F, struct.pack("<I", t_ms + 700) + "lap 1 done".encode())
        if second % 400 == 399:
            out += frame(0x42, bytes(rng.integers(0, 256, 6, dtype=np.uint8)))  # Unknown type.
    path.write_bytes(bytes(out))


BARO_SPEC = '''"""baro_format.py - reader for BARO .dat files (synthetic test format).

The description is this code: read_baro(path) returns the samples. All values are
big-endian. Note the 16-bit tick counter: it wraps around every 64 seconds.
"""
import struct

FILE_HEADER = struct.Struct(">4sBBHI20s")  # magic b"BARO", version, flags, sample_rate_hz, start_unix_s, site
BLOCK_HEADER = struct.Struct(">HH")         # first_tick (1/1024 s, wraps at 65536), sample_count
SAMPLE = struct.Struct(">iH")               # pressure in 0.01 Pa, temperature in 0.01 K


def read_baro(path):
    data = open(path, "rb").read()
    magic, version, flags, rate, start, site = FILE_HEADER.unpack_from(data, 0)
    assert magic == b"BARO" and version == 2
    offset, samples, wraps, previous = FILE_HEADER.size, [], 0, None
    while offset + BLOCK_HEADER.size <= len(data):
        first_tick, count = BLOCK_HEADER.unpack_from(data, offset)
        offset += BLOCK_HEADER.size
        if previous is not None and first_tick < previous:
            wraps += 1
        previous = first_tick
        for index in range(count):
            pressure, temperature = SAMPLE.unpack_from(data, offset)
            offset += SAMPLE.size
            seconds = (wraps * 65536 + first_tick) / 1024 + index / rate
            samples.append((start + seconds, pressure / 100.0, temperature / 100.0 - 273.15))
    return {"site": site.rstrip(b"\\0").decode(), "flags": flags, "samples": samples}
'''


def baro_dat(path: Path, rng: np.random.Generator) -> None:
    rate, block = 25, 200  # One block every 8 s.
    seconds = 45 * 60
    out = bytearray(struct.pack(">4sBBHI20s", b"BARO", 2, 1, rate, int(START.timestamp()), b"Mast 3"))
    for first in range(0, seconds * rate, block):
        t = (first + np.arange(block)) / rate
        pressure = 101_250 - 12 * t / 60 + 30 * np.sin(t / 300) + rng.normal(0, 1.5, block)  # Pa
        temperature = 288.15 + 2 * np.sin(t / 900) + rng.normal(0, 0.01, block)  # K
        out += struct.pack(">HH", int(round(first / rate * 1024)) % 65536, block)
        for p, k in zip(pressure, temperature):
            out += struct.pack(">iH", int(round(p * 100)), int(round(k * 100)))
    path.write_bytes(bytes(out))


POWER_HPP = r"""// power_meter.hpp - records of the PWR power meter log (synthetic test format).
//
// A file starts with the 8-byte magic "PWRLOG\x00\x01" and is followed by records.
// Each record starts with a one-byte RecordType; its size depends on the type.
// All multi-byte values are BIG-endian. Structures are packed.
#pragma once
#include <cstdint>

enum class RecordType : std::uint8_t {
    Config = 0x01,  // ConfigRecord; comes first, may come again (a new crank length)
    Sample = 0x02,  // SampleRecord; about 4 per second
    Lap    = 0x03,  // LapRecord
};

#pragma pack(push, 1)
struct ConfigRecord {            // after the type byte: 12 bytes
    std::uint32_t start_unix_s;  // UTC time of tick 0
    float         torque_scale;  // IEEE 754 float: torque [Nm] = raw_torque * torque_scale
    std::uint16_t crank_mm10;    // crank length in 0.1 mm
    std::uint16_t reserved;
};

struct SampleRecord {            // after the type byte: 8 bytes
    std::uint16_t dt_ms;         // milliseconds since the previous sample (the first: since tick 0)
    std::int16_t  raw_torque;    // torque in units of torque_scale Nm
    std::uint16_t cadence_rpm8;  // cadence in 1/8 rpm
    std::uint8_t  balance_left;  // left leg's share of power, percent; 0xFF = unknown
    std::uint8_t  flags;         // bit 0: coasting
};

struct LapRecord {               // after the type byte: 5 bytes
    std::uint32_t t_ms;          // milliseconds since tick 0
    std::uint8_t  lap_number;
};
#pragma pack(pop)

// Power [W] = torque [Nm] * cadence [rpm] * 2 * pi / 60
"""


def power_bin(path: Path, rng: np.random.Generator) -> None:
    scale = 0.01
    out = bytearray(b"PWRLOG\x00\x01")
    out += b"\x01" + struct.pack(">IfHH", int(START.timestamp()), scale, 1725, 0)
    elapsed, lap, crank_changed = 0, 1, False
    while elapsed < 60 * 60 * 1000:
        dt = int(rng.integers(230, 270))
        elapsed += dt
        seconds = elapsed / 1000
        coasting = 1500 < seconds < 1560
        cadence = 0.0 if coasting else 88 + 6 * np.sin(seconds / 200) + rng.normal(0, 2)
        torque = 0.0 if coasting else 22 + 6 * np.sin(seconds / 300) + rng.normal(0, 1.5)
        balance = 0xFF if coasting else int(np.clip(50 + rng.normal(0, 2), 0, 100))
        out += b"\x02" + struct.pack(">HhHBB", dt, int(round(torque / scale)), int(round(cadence * 8)), balance,
                                     int(coasting))
        if seconds >= lap * 900:
            out += b"\x03" + struct.pack(">IB", elapsed, lap)
            lap += 1
        if lap == 3 and not crank_changed:  # A new crank length mid-ride.
            out += b"\x01" + struct.pack(">IfHH", int(START.timestamp()), scale, 1700, 0)
            crank_changed = True
    path.write_bytes(bytes(out))


def mystery_bin(path: Path, rng: np.random.Generator) -> None:
    """No description at all: a short header and float32 rows, for the AI to work out."""
    count = 4 * 3600 // 10
    t = np.arange(count, dtype=np.float32) * 10
    temperature = (4 + 3 * np.sin(2 * np.pi * t / 86400 * 6) + rng.normal(0, 0.1, count)).astype(np.float32)
    humidity = np.clip(85 - 4 * (temperature - 4) + rng.normal(0, 1, count), 0, 100).astype(np.float32)
    rows = np.column_stack([t, temperature, humidity]).astype("<f4")
    path.write_bytes(b"FRDG" + struct.pack("<HI", 3, count) + rows.tobytes())


# ---- Main -----------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-big", action="store_true", help="skip the big files (about 150 MB)")
    options = parser.parse_args()
    out = options.output
    rng = np.random.default_rng(20260514)
    folders = {name: out / name for name in ("csv", "json", "yaml", "txt", "gpx", "nmea", "binary")}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    binary = folders["binary"]
    for name in ("c_header", "markdown_spec", "python_spec", "cpp_header", "no_spec"):
        (binary / name).mkdir(exist_ok=True)

    weather_csv(folders["csv"] / "weather_station_fi.csv", rng)
    bike_csv(folders["csv"] / "bike_ride_power.csv", rng)
    imu_csv(folders["csv"] / "imu_200hz_short.csv", rng, minutes=1)
    run_json(folders["json"] / "run_session.json", rng, minutes=45)
    events_jsonl(folders["json"] / "home_sensors.jsonl", rng)
    dive_yaml(folders["yaml"] / "dive_log.yaml", rng)
    fixed_width_txt(folders["txt"] / "logger_fixed_width.txt", rng)
    console_log(folders["txt"] / "serial_console.log", rng)
    gpx(folders["gpx"] / "trail_run.gpx", rng, minutes=60, name="Trail run")
    nmea(folders["nmea"] / "gnss_receiver.nmea", rng)
    (binary / "c_header" / "logger_format.h").write_text(LOGGER_H, encoding="utf-8")
    logger_bin(binary / "c_header" / "motion_logger.lgr", rng, rate=50, minutes=10)
    (binary / "markdown_spec" / "tlv_protocol.md").write_text(TLV_SPEC, encoding="utf-8")
    tlv_stream(binary / "markdown_spec" / "chest_strap.tlv", rng)
    (binary / "python_spec" / "baro_format.py").write_text(BARO_SPEC, encoding="utf-8")
    baro_dat(binary / "python_spec" / "weather_mast.dat", rng)
    (binary / "cpp_header" / "power_meter.hpp").write_text(POWER_HPP, encoding="utf-8")
    power_bin(binary / "cpp_header" / "ride.pwr", rng)
    mystery_bin(binary / "no_spec" / "fridge_logger.bin", rng)

    if not options.no_big:
        big = out / "big"
        big.mkdir(exist_ok=True)
        imu_csv(big / "imu_200hz_1h.csv", rng, minutes=60)
        run_json(big / "run_session_6h.json", rng, minutes=360)
        gpx(big / "long_hike_2_segments.gpx", rng, minutes=8 * 60, segments=2, name="Long hike")
        logger_bin(big / "motion_logger_1khz.lgr", rng, rate=1000, minutes=40)

    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "README.md":
            print(f"{path.stat().st_size / 1e6:9.2f} MB  {path.relative_to(out)}")


if __name__ == "__main__":
    main()
