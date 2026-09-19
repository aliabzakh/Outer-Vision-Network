"""ElevenLabs: generated instrument samples (sound effects API) and menu voice prompts (text to speech).

Everything is requested as raw PCM16 mono at 24 kHz (pcm_44100 needs a Pro plan; 24 kHz also matches the
speech Player). Key from env ELEVENLABS_API_KEY. Used ahead of time by tools/gen_audio.py so the demo
doesn't depend on venue Wi-Fi; live TTS is only a fallback when OMNI's own voice fails.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import numpy as np

API = "https://api.elevenlabs.io/v1"
SR = 24000


class ElevenError(RuntimeError):
    pass


class Eleven:
    def __init__(self, cfg: dict, key=None, timeout=30):
        e = cfg["eleven"]
        self.key = key if key is not None else os.environ.get("ELEVENLABS_API_KEY", "")
        self.voice_id = os.environ.get("ELEVENLABS_VOICE_ID", e["voice_id"])
        self.tts_model = e["tts_model"]
        self.timeout = timeout

    @property
    def ok(self) -> bool:
        return bool(self.key)

    def _post(self, path: str, body: dict) -> bytes:
        req = urllib.request.Request(f"{API}{path}?output_format=pcm_{SR}", json.dumps(body).encode(), method="POST",
                                     headers={"xi-api-key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ElevenError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise ElevenError(f"network: {e}") from None

    def tts(self, text: str) -> bytes:
        """Speech as PCM16 24 kHz."""
        return self._post(f"/text-to-speech/{self.voice_id}", {"text": text, "model_id": self.tts_model})

    def sfx(self, prompt: str, seconds: float, influence: float = 0.7) -> bytes:
        """Sound effect as mono PCM16 24 kHz."""
        b = self._post("/sound-generation", {"text": prompt, "duration_seconds": seconds,
                                             "prompt_influence": influence})
        x = np.frombuffer(b[:len(b) - len(b) % 4], "<i2")
        # The sound effects API returns interleaved STEREO PCM (measured 2026-09-19: twice the samples for
        # the requested duration). Read as mono it plays at half speed, an octave low. Mix it down.
        if abs(len(x) / 2 / SR - seconds) < abs(len(x) / SR - seconds):
            x = x.reshape(-1, 2).astype(np.int32).mean(1).astype(np.int16)
        return x.tobytes()
