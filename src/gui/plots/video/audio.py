"""Bounded PCM output; the audio device's consumed samples drive playback."""
import queue

from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices


class AudioOutput:
    rate = 48000
    bytes_per_frame = 4

    def __init__(self, parent):
        device = QMediaDevices.defaultAudioOutput()
        fmt = QAudioFormat()
        fmt.setSampleRate(self.rate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isNull() or not device.isFormatSupported(fmt):
            raise RuntimeError('No output device supporting 48 kHz stereo PCM')
        self.sink = QAudioSink(device, fmt, parent)
        self.sink.setBufferSize(19200)  # 100 ms, independent of clip duration
        self.base = 0.0
        self.written = 0
        self.pending = None
        self.io = None
        self.reset(0.0)

    def reset(self, seconds):
        self.sink.reset()
        self.base, self.written, self.pending = seconds, 0, None
        self.io = self.sink.start()
        if self.io is None:
            raise RuntimeError('Could not start audio output')
        self.sink.suspend()

    def position(self):
        return self.base + self.sink.processedUSecs() / 1e6

    def close(self):
        self.sink.stop()
        self.sink.deleteLater()

    def pump(self, session, duration):
        error = self.sink.error()
        # Qt releases may return QtAudio.Error while exposing QAudio.Error as
        # a distinct enum type. Compare their numeric values, not enum identity.
        if error.value not in (QAudio.Error.NoError.value, QAudio.Error.UnderrunError.value):
            raise RuntimeError('Audio device error: ' + error.name)
        generation = session.request()[0]
        # Limit iterations even when dropping stale packets after a seek.
        for _ in range(40):
            capacity = self.sink.bytesFree() // 4 * 4
            if capacity <= 0:
                return
            cursor = self.base + self.written / self.rate
            if self.pending is None:
                try:
                    item = session.audio.get_nowait()
                except queue.Empty:
                    if session.audio_eof == generation and cursor < duration:
                        count = min(capacity // 4, max(0, round((duration - cursor) * self.rate)))
                        data = bytes(count * 4)
                        written = self.io.write(data)
                        if written > 0:
                            self.written += written // 4
                    return
                if item[0] != generation:
                    continue
                self.pending = item[1:]
            seconds, pcm = self.pending
            delta = round((seconds - cursor) * self.rate)
            if delta < 0:
                trim = min(len(pcm) // 4, -delta)
                seconds += trim / self.rate
                pcm = pcm[trim * 4:]
                self.pending = (seconds, pcm) if pcm else None
                if not pcm:
                    continue
            if delta > 0:
                data = bytes(min(capacity // 4, delta) * 4)
            else:
                data = pcm[:capacity]
            written = self.io.write(data)
            if written < 0:
                raise RuntimeError('Audio output write failed')
            if written == 0:
                return
            self.written += written // 4
            if delta <= 0:
                pcm = pcm[written:]
                self.pending = (seconds + written / (4 * self.rate), pcm) if pcm else None
