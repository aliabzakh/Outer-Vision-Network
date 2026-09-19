"""Maestro's fixed lines (blink-menu prompts) and fallback speech.

Prompts play from pre-generated clips (assets/voice/<key>.wav, made by tools/gen_audio.py with ElevenLabs),
so the menu answers instantly and offline. Free text (OMNI's reply when its own voice fails) goes to live
ElevenLabs TTS, then macOS `say` / espeak, so the user is never left without an answer.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .audio import read_wav
from .eleven import SR, ElevenError
from .menu import PROMPTS


def local_say(text: str):
    for cmd in (["say", text], ["espeak", text]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, check=False)
            return


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return x
    n = int(len(x) * sr_to / sr_from)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32)).astype(np.int16)


class Voice:
    def __init__(self, player=None, eleven=None, clip_dir="assets/voice", log=print):
        self.player, self.eleven, self.log = player, eleven, log
        self.clips = {}
        for key in PROMPTS:
            p = Path(clip_dir) / f"{key}.wav"
            if p.exists():
                x, sr = read_wav(p)
                self.clips[key] = resample(x, sr, SR)
        missing = sorted(set(PROMPTS) - set(self.clips))
        if missing:
            self.log(f"[voice] no clips for {missing}; run tools/gen_audio.py (using live/local speech meanwhile)")

    def prompt(self, key: str):
        """Speak a menu prompt now, cutting off anything still playing. Never blocks."""
        clip = self.clips.get(key)
        if clip is not None and self.player is not None:
            self.player.stop()
            self.player.feed(clip.tobytes())
        else:
            threading.Thread(target=self.say, args=(PROMPTS[key],), daemon=True).start()

    def say(self, text: str):
        """Speak free text; blocks until done."""
        if self.player is not None and self.eleven is not None and self.eleven.ok:
            try:
                self.player.feed(self.eleven.tts(text))
                while self.player.playing:
                    time.sleep(0.05)
                return
            except ElevenError as e:
                self.log(f"[voice] ElevenLabs failed ({e}); using local speech")
        local_say(text)
