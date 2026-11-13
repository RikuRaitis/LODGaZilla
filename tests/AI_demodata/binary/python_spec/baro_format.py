"""baro_format.py - reader for BARO .dat files (synthetic test format).

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
    return {"site": site.rstrip(b"\0").decode(), "flags": flags, "samples": samples}
