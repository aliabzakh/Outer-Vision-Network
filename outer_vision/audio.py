"""Microphone capture with end-of-speech detection, and a streaming PCM player (sounddevice/PortAudio)."""
from __future__ import annotations

import collections
import io
import threading
import time
import wave

import numpy as np

MIC_SR = 16000
FRAME = 480                 # 30 ms at 16 kHz


def to_wav(pcm16: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.astype("<i2").tobytes())
    return buf.getvalue()


class Endpointer:
    """Energy-based speech endpointing. Pure logic (no audio device) so it's unit-testable.

    feed() 30 ms frames; returns an int16 utterance when the speaker has finished, else None.
    Armed mode (key / talk marker): start capturing now, stop after speech + 0.8 s silence.
    VAD mode: wait for speech to start, then the same.
    """

    def __init__(self, max_s=8.0, silence_s=0.8, min_speech_s=0.3, give_up_s=4.0, preroll_s=0.3):
        self.max_n = int(max_s * MIC_SR / FRAME)
        self.silence_n = int(silence_s * MIC_SR / FRAME)
        self.min_speech_n = int(min_speech_s * MIC_SR / FRAME)
        self.give_up_n = int(give_up_s * MIC_SR / FRAME)
        self.pre = collections.deque(maxlen=int(preroll_s * MIC_SR / FRAME))
        self.noise = 0.004
        self.capturing = False
        self.armed = False
        self.vad = False
        self._reset()

    def _reset(self):
        self.buf, self.speech_n, self.silent_n, self.n = [], 0, 0, 0

    def arm(self):
        if not self.capturing:
            self.armed, self.capturing = True, True
            self._reset()
            self.buf.extend(self.pre)

    def feed(self, frame: np.ndarray):
        rms = float(np.sqrt(np.mean((frame.astype(np.float32) / 32768) ** 2)))
        speech = rms > max(3.0 * self.noise, 0.012)
        if not speech:
            self.noise = 0.98 * self.noise + 0.02 * rms       # track the room's noise floor
        if not self.capturing:
            self.pre.append(frame)
            if self.vad and speech:
                self.capturing = True
                self._reset()
                self.buf.extend(self.pre)
            else:
                return None
        self.buf.append(frame)
        self.n += 1
        self.speech_n += speech
        self.silent_n = 0 if speech else self.silent_n + 1
        done = (self.speech_n >= self.min_speech_n and self.silent_n >= self.silence_n) or self.n >= self.max_n
        if not done and self.speech_n == 0 and self.n >= self.give_up_n:
            self.capturing = self.armed = False               # armed but nobody spoke
            return None
        if done:
            self.capturing = self.armed = False
            return np.concatenate(self.buf) if self.speech_n >= self.min_speech_n else None
        return None


class Mic:
    """Always-open input stream feeding an Endpointer; calls on_utterance(wav_bytes) from a worker thread."""

    def __init__(self, on_utterance, vad=False, max_s=8.0):
        import sounddevice as sd
        self.ep = Endpointer(max_s=max_s)
        self.ep.vad = vad
        self.muted = False          # set while the assistant is speaking (no self-hearing)
        self.on_utterance = on_utterance
        self._q = collections.deque()
        self._ev = threading.Event()
        self.stream = sd.InputStream(samplerate=MIC_SR, channels=1, dtype="int16", blocksize=FRAME,
                                     callback=self._cb)
        self.stream.start()
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def capturing(self):
        return self.ep.capturing

    def arm(self):
        self.ep.arm()

    def _cb(self, indata, frames, t, status):
        self._q.append(indata[:, 0].copy())
        self._ev.set()

    def _loop(self):
        while True:
            self._ev.wait(0.1)
            self._ev.clear()
            while self._q:
                f = self._q.popleft()
                if self.muted:
                    continue
                utt = self.ep.feed(f)
                if utt is not None:
                    self.on_utterance(to_wav(utt, MIC_SR))


class Player:
    """Streams PCM16 mono chunks as they arrive (low time-to-first-sound). stop() cuts playback."""

    def __init__(self, sr=24000):
        import sounddevice as sd
        self.sr = sr
        self._buf = collections.deque()
        self._lock = threading.Lock()
        self._pending = np.zeros(0, np.int16)
        self.last_audio = 0.0
        self.stream = sd.OutputStream(samplerate=sr, channels=1, dtype="int16", callback=self._cb)
        self.stream.start()

    def feed(self, pcm_bytes: bytes):
        with self._lock:
            self._buf.append(np.frombuffer(pcm_bytes, "<i2"))

    def stop(self):
        with self._lock:
            self._buf.clear()
            self._pending = np.zeros(0, np.int16)

    @property
    def playing(self):
        return bool(self._buf) or len(self._pending) > 0 or time.monotonic() - self.last_audio < 0.25

    def _cb(self, out, frames, t, status):
        with self._lock:
            while len(self._pending) < frames and self._buf:
                self._pending = np.concatenate([self._pending, self._buf.popleft()])
            n = min(frames, len(self._pending))
            out[:n, 0] = self._pending[:n]
            out[n:, 0] = 0
            self._pending = self._pending[n:]
            if n:
                self.last_audio = time.monotonic()
