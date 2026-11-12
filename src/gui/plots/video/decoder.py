"""Direct libavformat/libavcodec readers with bounded, generation-tagged queues."""
from dataclasses import dataclass
import logging
from pathlib import Path
import queue
import sys
import threading
import time

import av
import numpy as np
from av.codec.hwaccel import HWAccel, hwdevices_available

from .calibration import Metadata, read_metadata

LOG = logging.getLogger(__name__)


def load_metadata(path):
    """Read lens metadata; a damaged trailer falls back to default lenses."""
    try:
        return read_metadata(path)
    except Exception as exc:
        LOG.warning('Cannot read camera metadata from %s; using default lenses: %s', path, exc)
        return Metadata()


@dataclass
class Picture:
    generation: int
    seconds: float
    frames: tuple
    projection: str = 'fisheye'
    source_aspect: float = 1.0
    rotation: float = 0.0
    source_size: tuple = (0, 0)


def split_packed_lenses(frame):
    """Split a side-by-side YUV420 frame without converting through RGB."""
    width, height = frame.width, frame.height
    half = width // 2
    planes = frame.to_ndarray(format='yuv420p')
    chroma = planes[height:].reshape(2, height // 2, width // 2)
    lenses = []
    for index in range(2):
        pixels = np.empty((height * 3 // 2, half), dtype=np.uint8)
        pixels[:height] = planes[:height, index * half:(index + 1) * half]
        pixels[height:] = chroma[:, :, index * (half // 2):(index + 1) * (half // 2)].reshape(height // 2, half)
        lens = av.VideoFrame.from_ndarray(pixels, format='yuv420p')
        lens.pts, lens.time_base = frame.pts, frame.time_base
        lenses.append(lens)
    return lenses


class Session:
    """Owns all readers. Qt only polls queues; it never performs media I/O."""

    def __init__(self, path, max_side=1536, hardware="auto", gpu_output=False):
        self.path, self.max_side, self.hardware = str(path), max_side, hardware
        self.gpu_output = gpu_output
        self.video = queue.Queue(maxsize=3)
        self.audio = queue.Queue(maxsize=32)
        self.events = queue.SimpleQueue()
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.generation, self.target = 0, 0.0
        self.video_eof = self.audio_eof = -1
        self.dropped = 0
        self.playhead = (0.0, time.monotonic(), False)
        self.threads = []
        self._start(self._video_worker)

    def _start(self, function):
        thread = threading.Thread(target=function, daemon=True)
        self.threads.append(thread)
        thread.start()

    def request(self):
        with self.lock:
            return self.generation, self.target

    def set_clock(self, seconds, playing):
        self.playhead = (seconds, time.monotonic(), playing)

    def playback_time(self):
        seconds, when, playing = self.playhead
        return seconds + min(0.15, time.monotonic() - when) if playing else seconds

    def seek(self, seconds):
        with self.lock:
            self.generation += 1
            self.target = max(0.0, seconds)
            for buffer in (self.video, self.audio):
                while True:
                    try:
                        buffer.get_nowait()
                    except queue.Empty:
                        break
            return self.generation

    def close(self):
        # Readers own/close their containers. Never close a codec concurrently
        # with decode(), and never wait for a slow decoder on the GUI thread.
        self.stop_event.set()

    def _put(self, buffer, item, generation):
        while not self.stop_event.is_set() and self.request()[0] == generation:
            if buffer is self.video and self.playhead[2] and item.seconds < self.playback_time() - 0.08:
                self.dropped += 1
                return True
            try:
                buffer.put(item, timeout=0.03)
                return True
            except queue.Full:
                pass
        return False

    def _reader(self, ordinal, *, packed=False):
        available = hwdevices_available()
        candidates = ([name for name in
                       (['d3d11va', 'cuda'] if sys.platform == 'win32' else
                        ['videotoolbox', 'cuda', 'vaapi']) if name in available]
                      if self.hardware == 'auto' else
                      ([] if self.hardware == 'none' else [self.hardware]))
        candidates.append(None)
        for backend in candidates:
            container = None
            try:
                kwargs = {} if backend is None else {
                    'hwaccel': HWAccel(backend, allow_software_fallback=False,
                                      is_hw_owned=self.gpu_output and backend == 'd3d11va' and not packed)}
                container = av.open(self.path, **kwargs)
                stream = container.streams.video[ordinal]
                stream.thread_type = 'SLICE' if backend else 'AUTO'
                stream.codec_context.thread_count = 1 if backend else 4
                first = next(container.decode(stream))  # validate actual decode
                active = bool(stream.codec_context.is_hwaccel)
                if backend and not active:
                    raise RuntimeError('codec did not activate hardware decoding')
                LOG.info('Video track %s: %s, %sx%s %s', ordinal,
                         backend or 'CPU', first.width, first.height, first.format.name)
                return container, stream, backend or 'CPU'
            except Exception as exc:
                if container is not None:
                    container.close()
                LOG.info('Video track %s backend %s unavailable: %s', ordinal, backend, exc)
                if backend is None:
                    raise

    def _video_worker(self):
        readers = []
        try:
            suffix = Path(self.path).suffix.lower()
            packed = False
            projection = 'fisheye' if suffix == '.insv' else 'flat'
            metadata = load_metadata(self.path) if projection == 'fisheye' else None
            with av.open(self.path) as probe:
                videos = list(probe.streams.video)
                # Ignore cover art; regular videos use the first actual video track.
                videos = [s for s in videos if not (s.disposition & av.stream.Disposition.attached_pic) and
                          s.width > 0 and s.height > 0]
                # LRV previews can use a regular video track or the same pair
                # of square lens tracks as INSV. Choose from the actual tracks.
                if suffix == '.lrv':
                    lens_tracks = [s for s in videos if s.width >= 128 and 0.8 < s.width / s.height < 1.2]
                    if len(lens_tracks) == 2:
                        projection = 'fisheye'
                        metadata = load_metadata(self.path)
                    elif len(videos) == 1:
                        candidate = load_metadata(self.path)
                        stream = videos[0]
                        # X6 previews pack both lens images in one wide track.
                        # Require camera metadata so a normal 2:1 video stays flat.
                        if (stream.width == 2 * stream.height and stream.height % 2 == 0
                                and (candidate.calibrated or candidate.model.startswith('Insta360'))):
                            projection, metadata, packed = 'fisheye', candidate, True
                if projection == 'fisheye' and not packed:
                    videos = [s for s in videos if s.width >= 128 and 0.8 < s.width / s.height < 1.2]
                    if len(videos) != 2:
                        raise ValueError('Expected two square fisheye video tracks in one INSV/LRV. '
                                         'Packed INSV or paired-file recordings are not supported yet.')
                else:
                    if not videos:
                        raise ValueError('No playable video track found.')
                    videos = videos[:1]
                ordinals = [list(probe.streams.video).index(s) for s in videos]
                pixel_aspect = float(videos[0].sample_aspect_ratio or 1)
                if pixel_aspect <= 0:
                    pixel_aspect = 1.0
                origin = float(probe.start_time or 0) / av.time_base
                durations = [float(s.duration * s.time_base) for s in videos if s.duration]
                duration = min(durations) if durations else float(probe.duration or 0) / av.time_base
                fps = float(videos[0].average_rate or 30)
                has_audio = bool(probe.streams.audio)
            for ordinal in ordinals:
                readers.append(self._reader(ordinal, packed=packed))
            self.events.put(('ready', dict(duration=duration, fps=fps, origin=origin,
                                          metadata=metadata, projection=projection, audio=has_audio,
                                          backends=[r[2] for r in readers])))
            if has_audio:
                self._start(lambda: self._audio_worker(origin))
            generation = -1
            iterators = []
            frames = [None] * len(readers)
            while not self.stop_event.is_set():
                requested, target = self.request()
                if requested != generation:
                    generation = requested
                    self.video_eof = -1
                    iterators = []
                    for container, stream, _ in readers:
                        container.seek(int((target + origin) / stream.time_base),
                                       stream=stream, backward=True)
                        iterators.append(iter(container.decode(stream)))
                    frames = [None] * len(readers)
                if self.video_eof == generation:
                    self.stop_event.wait(0.02)
                    continue
                try:
                    for i in range(len(readers)):
                        if frames[i] is None:
                            frames[i] = next(iterators[i])
                    if any(frame.pts is None for frame in frames):
                        raise ValueError('Video track has no presentation timestamps')
                    stamps = [float(f.pts * f.time_base) - origin for f in frames]
                    # Pair by timestamps, never by packet arrival order.
                    if len(stamps) == 2 and abs(stamps[0] - stamps[1]) > 0.55 / fps:
                        older = 0 if stamps[0] < stamps[1] else 1
                        frames[older] = None
                        self.dropped += 1
                        continue
                    seconds = max(stamps)
                    if seconds + 0.5 / fps < target:
                        frames = [None] * len(readers)
                        continue
                    if self.playhead[2] and seconds < self.playback_time() - 0.04:
                        # Discard before download/scale, and keep decoding while
                        # the GUI renders. A bounded queue must not slow media
                        # time down to the viewport's refresh rate.
                        self.dropped += 1
                        frames = [None] * len(readers)
                        if self.playback_time() - seconds > 0.75:
                            # A machine unable to decode in real time catches up
                            # at the next keyframe; audio keeps its own clock.
                            now = self.playback_time()
                            for j, (container, stream, _) in enumerate(readers):
                                container.seek(int((now + origin) / stream.time_base),
                                               stream=stream, backward=False)
                                iterators[j] = iter(container.decode(stream))
                        continue
                    resized = []
                    for frame in frames:
                        if frame.format.name == 'd3d11':
                            resized.append(frame)
                            continue
                        scale = min(1.0, self.max_side / max(frame.width // 2 if packed else frame.width, frame.height))
                        w, h = (max(2, int(side * scale) // 2 * 2)
                                for side in (frame.width, frame.height))
                        if packed:
                            w = 2 * h
                        scaled = frame.reformat(width=w, height=h, format='yuv420p', threads=4)
                        resized.extend(split_packed_lenses(scaled) if packed else [scaled])
                    source_width = frames[0].width // 2 if packed else frames[0].width
                    picture = Picture(generation, seconds, tuple(resized), projection,
                                      source_width * pixel_aspect / frames[0].height,
                                      frames[0].rotation if projection == 'flat' else 0.0,
                                      (source_width, frames[0].height))
                    self._put(self.video, picture, generation)
                    frames = [None] * len(readers)
                except StopIteration:
                    self.video_eof = generation
        except Exception as exc:
            LOG.exception('Video reader failed')
            self.events.put(('error', str(exc)))
        finally:
            for container, _, _ in readers:
                container.close()

    def _audio_worker(self, origin):
        try:
            with av.open(self.path) as container:
                stream = container.streams.audio[0]
                generation = -1
                while not self.stop_event.is_set():
                    requested, target = self.request()
                    if requested != generation:
                        generation = requested
                        self.audio_eof = -1
                        container.seek(int((target + origin) / stream.time_base),
                                       stream=stream, backward=True)
                        resampler = av.AudioResampler(format='s16', layout='stereo', rate=48000)
                        iterator = iter(container.decode(stream))
                    if self.audio_eof == generation:
                        self.stop_event.wait(0.02)
                        continue
                    source = next(iterator, None)
                    output = resampler.resample(source)
                    for frame in output:
                        if frame.pts is None:
                            raise ValueError('Audio track has no presentation timestamps')
                        seconds = float(frame.pts * frame.time_base) - origin
                        pcm = bytes(frame.planes[0])[:frame.samples * 4]
                        trim = min(frame.samples, max(0, round((target - seconds) * 48000)))
                        pcm = pcm[trim * 4:]
                        if pcm:
                            self._put(self.audio, (generation, seconds + trim / 48000, pcm), generation)
                    if source is None:
                        self.audio_eof = generation
        except Exception as exc:
            LOG.exception('Audio reader failed')
            self.events.put(('audio_error', str(exc)))
