"""Regression tests for review findings J3, J4 and J7."""
import math
import struct
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import av
import numpy as np

from src.gui.plots.video.calibration import MAGIC, Metadata
from src.gui.plots.video.decoder import Session, load_metadata


ROOT = Path(__file__).resolve().parents[1]


def nmea(body):
    checksum = 0
    for byte in body.encode("ascii"):
        checksum ^= byte
    return f"${body}*{checksum:02X}\r\n".encode("ascii")


def gga(time_text):
    return nmea(f"GNGGA,{time_text},6016.3369830,N,02458.3666919,E,1,12,0.8,10.0,M,18.0,M,,")


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc).timestamp()






def damaged_trailer():
    """INSV trailer whose identity record passes bounds checks but fails parsing."""
    data = b"\x12\xff"  # truncated protobuf length
    records = bytes(10) + data
    directory = struct.pack("<HII", 0x101, len(data), 0)
    footer = bytearray(78)
    struct.pack_into("<I", footer, 2, len(directory))
    struct.pack_into("<I", footer, 38, len(records) + len(directory) + 78 - 10)
    footer[46:] = MAGIC
    return records + directory + bytes(footer)


def make_dual_lens_clip(path, frames=12, size=128):
    with av.open(str(path), "w", format="mp4") as container:
        streams = [container.add_stream("libx264", rate=24) for _ in range(2)]
        for stream in streams:
            stream.width = stream.height = size
            stream.pix_fmt = "yuv420p"
            stream.options = {"preset": "ultrafast"}
        for index in range(frames):
            for stream in streams:
                frame = av.VideoFrame.from_ndarray(np.zeros((size, size, 3), dtype=np.uint8), format="rgb24")
                frame.pts = index
                for packet in stream.encode(frame):
                    container.mux(packet)
        for stream in streams:
            for packet in stream.encode(None):
                container.mux(packet)


class DamagedInsvTrailerTests(unittest.TestCase):
    def test_load_metadata_falls_back_and_logs_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "damaged.insv"
            path.write_bytes(bytes(128) + damaged_trailer())
            with self.assertLogs("src.gui.plots.video.decoder", level="WARNING"):
                metadata = load_metadata(path)
        self.assertEqual(metadata, Metadata())

    def test_session_plays_insv_with_damaged_trailer(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "damaged.insv"
            make_dual_lens_clip(path)
            with path.open("ab") as file:
                file.write(damaged_trailer())
            with self.assertLogs("src.gui.plots.video.decoder", level="WARNING"):
                session = Session(path, max_side=64, hardware="none")
                try:
                    kind, info = session.events.get(timeout=15)
                finally:
                    session.close()
                    for thread in session.threads:
                        thread.join(5)
        self.assertEqual(kind, "ready", info)
        self.assertEqual(info["projection"], "fisheye")
        self.assertFalse(info["metadata"].calibrated)


if __name__ == "__main__":
    unittest.main()
