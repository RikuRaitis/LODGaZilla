"""Synthetic regression tests; no camera footage or LODGaZilla dependencies."""
import math
from fractions import Fraction
from pathlib import Path
import struct
import tempfile
import time
import unittest

import av
import numpy as np

from src.gui.plots.video.calibration import MAGIC, fields, parse_identity, read_metadata
from src.gui.plots.video.decoder import Session, split_packed_lenses
from src.gui.plots.video.preview import preview_size, render_size


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def blob(number, value):
    return varint(number * 8 + 2) + varint(len(value)) + value


def identity():
    entries = []
    for center in (3872, 11616):
        entries.extend([2.45543, 7217, 7217, center, 3872, 0, 0, 90,
                        0, 0, 0, 0.1, 0.2, 0.3, 0.4, 0.5,
                        0, 0, 0, 0, 0, 0, 0, 0, 15488, 7744, 193])
    calibration = ('2_' + '_'.join(map(str, entries))).encode()
    return blob(2, b'Insta360 X6') + blob(5, calibration) + varint(80 * 8) + b'\x01'


def make_clip(path, seconds=2, audio=True, tracks=2, size=(256, 256), rotation=0,
              pixel_aspect=Fraction(1)):
    """Synthetic MP4 container with one standard track or two INSV lens tracks."""
    with av.open(str(path), 'w', format='mp4') as container:
        streams = [container.add_stream('libx264', rate=24) for _ in range(tracks)]
        for stream in streams:
            stream.width, stream.height = size
            stream.sample_aspect_ratio = pixel_aspect
            if rotation:
                stream.set_display_rotation(rotation)
            stream.pix_fmt = 'yuv420p'
            stream.options = {'preset': 'ultrafast', 'crf': '18'}
        sound = container.add_stream('aac', rate=48000) if audio else None
        if sound:
            sound.layout = 'stereo'
        for i in range(seconds * 24):
            for lens, stream in enumerate(streams):
                rgb = np.zeros((size[1], size[0], 3), dtype=np.uint8)
                rgb[:, :, lens * 2] = 200
                rgb[::32, :, :] = 255
                rgb[:, ::32, :] = 255
                frame = av.VideoFrame.from_ndarray(rgb, format='rgb24')
                frame.pts = i
                for packet in stream.encode(frame):
                    container.mux(packet)
        if sound:
            for start in range(0, seconds * 48000, 1024):
                count = min(1024, seconds * 48000 - start)
                samples = (1000 * np.sin(2 * math.pi * 440 *
                           np.arange(start, start + count) / 48000)).astype(np.int16)
                pcm = np.repeat(samples, 2).reshape(1, -1)
                frame = av.AudioFrame.from_ndarray(pcm, format='s16', layout='stereo')
                frame.sample_rate = 48000
                frame.pts = start
                for packet in sound.encode(frame):
                    container.mux(packet)
            for packet in sound.encode(None):
                container.mux(packet)
        for stream in streams:
            for packet in stream.encode(None):
                container.mux(packet)


class PreviewSizeTests(unittest.TestCase):
    def test_flat_fit_rotation_and_source_limit(self):
        args = (600, 360, 1, 'flat', (3840, 2160))
        self.assertEqual(preview_size(*args, source_aspect=16/9), 600)
        self.assertEqual(preview_size(*args, source_aspect=16/9, rotation=90), 360)
        self.assertEqual(preview_size(600, 360, 1, 'flat', (1080, 1920), 9/16), 360)
        self.assertEqual(preview_size(600, 360, 1, 'flat', (320, 180), 16/9), 320)
        self.assertEqual(preview_size(614, 360, 1, 'flat', (3840, 2160), 16/9), 614)

    def test_insv_zoom_and_native_limit(self):
        args = (600, 360, 1, 'fisheye', (3840, 3840))
        self.assertEqual(preview_size(*args, fov=90), 1200)
        self.assertEqual(preview_size(*args, fov=45), 2400)
        self.assertEqual(preview_size(1000, 600, 1, 'fisheye', (3840, 3840), fov=35), 3840)

    def test_high_dpi_respects_actual_render_resolution(self):
        self.assertEqual(render_size(600, 360, 2), (1000, 600))
        self.assertEqual(preview_size(600, 360, 2, 'flat', (3840, 2160), 16/9), 1000)
        self.assertEqual(preview_size(1920, 1080, 1, 'flat', (3840, 2160), 16/9), 1000)


class CalibrationTests(unittest.TestCase):
    def test_native_principal_points_and_track_order(self):
        meta = parse_identity(identity())
        self.assertTrue(meta.calibrated)
        self.assertTrue(meta.reversed_tracks)
        for lens in meta.lenses:
            self.assertEqual(lens.center, (0.5, 0.5))
            self.assertAlmostEqual(lens.focal[0], 7217 / 7680)
            self.assertEqual(lens.radial[-1], 0.5)

    def test_trailer_directory_and_duplicate_identity(self):
        data = identity()
        prefix = bytes(128)
        records = bytes(10) + data
        directory = struct.pack('<HII', 0x101, 1, 0) + struct.pack('<HII', 0x101, len(data), 0)
        footer = bytearray(78)
        struct.pack_into('<I', footer, 2, len(directory))
        struct.pack_into('<I', footer, 38, len(records) + len(directory) + 78 - 10)
        footer[46:] = MAGIC
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'trailer.insv'
            path.write_bytes(prefix + records + directory + footer)
            self.assertTrue(read_metadata(path).calibrated)
            footer[38:42] = b'\xff' * 4
            path.write_bytes(prefix + records + directory + footer)
            with self.assertRaises(ValueError):
                read_metadata(path)

    def test_malformed_protobuf_rejected(self):
        for payload in (b'\x12\xff', b'\x12\x10abc', b'\x80' * 12, b'\x00'):
            with self.assertRaises(ValueError):
                list(fields(payload))


class DecodeTests(unittest.TestCase):
    def test_packed_lens_split_preserves_luma_chroma_and_timestamps(self):
        pixels = np.empty((6, 8), dtype=np.uint8)
        pixels[:4, :4], pixels[:4, 4:] = 50, 200
        chroma = pixels[4:].reshape(2, 2, 4)
        chroma[0, :, :2], chroma[0, :, 2:] = 55, 100
        chroma[1, :, :2], chroma[1, :, 2:] = 125, 200
        frame = av.VideoFrame.from_ndarray(pixels, format='yuv420p')
        frame.pts, frame.time_base = 42, Fraction(1, 30)
        lenses = split_packed_lenses(frame)
        for lens, (y, u, v) in zip(lenses, ((50, 55, 125), (200, 100, 200))):
            result = lens.to_ndarray(format='yuv420p')
            self.assertTrue(np.all(result[:4] == y))
            self.assertTrue(np.all(result[4] == u))
            self.assertTrue(np.all(result[5] == v))
            self.assertEqual((lens.pts, lens.time_base), (42, Fraction(1, 30)))

    def test_packed_x6_lrv_decodes_two_lenses_and_seeks(self):
        path = Path(self.folder.name) / 'packed.lrv'
        make_clip(path, tracks=1, size=(512, 256), audio=True)
        records = bytes(10) + identity()
        directory = struct.pack('<HII', 0x101, len(identity()), 0)
        footer = bytearray(78)
        struct.pack_into('<I', footer, 2, len(directory))
        struct.pack_into('<I', footer, 38, len(records) + len(directory) + 78 - 10)
        footer[46:] = MAGIC
        with path.open('ab') as file:
            file.write(records + directory + footer)
        session = Session(path, max_side=128, hardware='none', gpu_output=True)
        try:
            kind, info = session.events.get(timeout=15)
            self.assertEqual(kind, 'ready', info)
            self.assertEqual(info['projection'], 'fisheye')
            self.assertTrue(info['metadata'].calibrated)
            self.assertTrue(info['audio'])
            self.assertGreater(len(session.audio.get(timeout=10)[2]), 0)
            picture = session.video.get(timeout=10)
            self.assertEqual(picture.source_size, (256, 256))
            self.assertEqual([(f.width, f.height) for f in picture.frames], [(128, 128)] * 2)
            expected = session.seek(1.25)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                picture = session.video.get(timeout=5)
                if picture.generation == expected:
                    break
            self.assertEqual(picture.generation, expected)
            self.assertAlmostEqual(picture.seconds, 1.25, delta=1/24)
        finally:
            session.close()
            for thread in session.threads:
                thread.join(5)
            self.assertTrue(all(not thread.is_alive() for thread in session.threads))

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.path = Path(cls.folder.name) / 'dual.insv'
        make_clip(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_direct_decode_bounded_buffers_audio_and_seek(self):
        session = Session(self.path, max_side=128, hardware='none')
        try:
            kind, info = session.events.get(timeout=15)
            self.assertEqual(kind, 'ready', info)
            self.assertTrue(info['audio'])
            self.assertEqual(info['projection'], 'fisheye')
            self.assertEqual(info['backends'], ['CPU', 'CPU'])
            picture = session.video.get(timeout=10)
            self.assertLess(picture.seconds, 0.05)
            self.assertEqual([f.width for f in picture.frames], [128, 128])
            generation, seconds, pcm = session.audio.get(timeout=10)
            self.assertEqual(generation, 0)
            self.assertGreater(len(pcm), 0)
            self.assertEqual(len(pcm) % 4, 0)
            time.sleep(0.1)
            self.assertLessEqual(session.video.qsize(), 3)
            self.assertLessEqual(session.audio.qsize(), 32)
            expected = session.seek(1.25)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                picture = session.video.get(timeout=5)
                if picture.generation == expected:
                    break
            self.assertEqual(picture.generation, expected)
            self.assertAlmostEqual(picture.seconds, 1.25, delta=1/24)
            while True:
                generation, seconds, pcm = session.audio.get(timeout=5)
                if generation == expected:
                    break
            self.assertGreaterEqual(seconds, 1.25 - 1/48000)
            self.assertAlmostEqual(float(picture.frames[0].pts * picture.frames[0].time_base),
                                   float(picture.frames[1].pts * picture.frames[1].time_base))
        finally:
            session.close()
            for thread in session.threads:
                thread.join(5)
            self.assertTrue(all(not thread.is_alive() for thread in session.threads))

    def test_invalid_file_reports_error(self):
        session = Session(self.path.with_name('missing.insv'), hardware='none')
        try:
            self.assertEqual(session.events.get(timeout=5)[0], 'error')
        finally:
            session.close()

    def test_standard_mp4_decode_aspect_audio_and_seek(self):
        for size, rotation, audio, pixel_aspect in (
                ((320, 180), 0, True, Fraction(1)),
                ((180, 320), 0, False, Fraction(1)),
                ((128, 128), 0, False, Fraction(1)),
                ((320, 180), 90, False, Fraction(4, 3))):
            with self.subTest(size=size, rotation=rotation, audio=audio):
                path = Path(self.folder.name) / 'standard.mp4'
                make_clip(path, tracks=1, size=size, rotation=rotation, audio=audio,
                          pixel_aspect=pixel_aspect)
                session = Session(path, max_side=128, hardware='none')
                try:
                    kind, info = session.events.get(timeout=15)
                    self.assertEqual(kind, 'ready', info)
                    self.assertEqual(info['projection'], 'flat')
                    self.assertIsNone(info['metadata'])
                    self.assertEqual(info['audio'], audio)
                    self.assertEqual(info['backends'], ['CPU'])
                    picture = session.video.get(timeout=10)
                    self.assertEqual(len(picture.frames), 1)
                    self.assertEqual(picture.projection, 'flat')
                    self.assertAlmostEqual(picture.source_aspect, size[0] * float(pixel_aspect) / size[1])
                    self.assertEqual(picture.rotation, rotation)
                    self.assertLessEqual(max(picture.frames[0].width, picture.frames[0].height), 128)
                    expected = session.seek(1.25)
                    while True:
                        picture = session.video.get(timeout=5)
                        if picture.generation == expected:
                            break
                    self.assertAlmostEqual(picture.seconds, 1.25, delta=1/24)
                    if audio:
                        while True:
                            generation, seconds, pcm = session.audio.get(timeout=5)
                            if generation == expected:
                                break
                        self.assertGreaterEqual(seconds, 1.25 - 1/48000)
                        self.assertGreater(len(pcm), 0)
                finally:
                    session.close()
                    for thread in session.threads:
                        thread.join(5)
                    self.assertTrue(all(not thread.is_alive() for thread in session.threads))

    def test_lrv_preview_tracks_audio_and_seek(self):
        for suffix, tracks, size in (('.lrv', 1, (320, 180)),
                                     ('.lrv', 1, (512, 256)),
                                     ('.LRV', 1, (128, 128)),
                                     ('.lrv', 2, (256, 256))):
            with self.subTest(suffix=suffix, tracks=tracks):
                path = Path(self.folder.name) / f'preview{suffix}'
                make_clip(path, tracks=tracks, size=size, audio=True)
                session = Session(path, max_side=128, hardware='none')
                try:
                    kind, info = session.events.get(timeout=15)
                    self.assertEqual(kind, 'ready', info)
                    projection = 'fisheye' if tracks == 2 else 'flat'
                    self.assertEqual(info['projection'], projection)
                    self.assertEqual(info['backends'], ['CPU'] * tracks)
                    self.assertTrue(info['audio'])
                    picture = session.video.get(timeout=10)
                    self.assertEqual(picture.projection, projection)
                    self.assertEqual(len(picture.frames), tracks)
                    self.assertLess(picture.seconds, 0.05)
                    self.assertGreater(len(session.audio.get(timeout=10)[2]), 0)
                    expected = session.seek(1.25)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        picture = session.video.get(timeout=5)
                        if picture.generation == expected:
                            break
                    self.assertEqual(picture.generation, expected)
                    self.assertAlmostEqual(picture.seconds, 1.25, delta=1/24)
                finally:
                    session.close()
                    for thread in session.threads:
                        thread.join(5)
                    self.assertTrue(all(not thread.is_alive() for thread in session.threads))

    def test_single_track_insv_still_rejected(self):
        path = Path(self.folder.name) / 'single.insv'
        make_clip(path, tracks=1, audio=False)
        session = Session(path, hardware='none')
        try:
            kind, message = session.events.get(timeout=10)
            self.assertEqual(kind, 'error')
            self.assertIn('two square fisheye', message)
        finally:
            session.close()
            for thread in session.threads:
                thread.join(5)


if __name__ == '__main__':
    unittest.main()
