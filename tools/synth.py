#!/usr/bin/env python3
"""Plays lock events as sound: note -> pitch, instrument -> timbre, volume -> loudness. Also chimes
lesson feedback. Stand-in for the game's audio (or the demo audio itself).

  python tools/synth.py              # listens on UDP 5006 (instead of tools/listen.py)
  python tools/synth.py --test       # plays every instrument once
"""
import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outer_vision.music import midi  # noqa: E402

SR = 44100


def env(n, attack, decay):
    t = np.arange(n) / SR
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t / decay)


def voice(instrument: str, f: float, dur=1.2) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n) / SR
    if instrument == "piano":
        w = sum(np.sin(2 * np.pi * f * k * t) / k ** 1.3 for k in range(1, 7)) * env(n, 0.005, 0.5)
    elif instrument == "marimba":
        w = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 4 * t) * env(n, 0.001, 0.05)) * env(n, 0.002, 0.25)
    elif instrument == "flute":
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5 * t)
        w = (np.sin(2 * np.pi * f * vib * t) + 0.15 * np.sin(4 * np.pi * f * t)
             + 0.03 * np.random.default_rng(0).normal(size=n)) * np.minimum(1, t / 0.06) * np.exp(-np.maximum(0, t - 0.5) / 0.15)
    elif instrument == "strings":
        w = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 12)) * 0.5 * np.minimum(1, t / 0.15) * np.exp(-np.maximum(0, t - 0.6) / 0.2)
    elif instrument == "bell":
        w = sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t / d) for r, a, d in
                [(1, 1, 1.2), (2.76, 0.5, 0.6), (5.4, 0.3, 0.3), (8.9, 0.2, 0.15)])
    elif instrument == "drum":
        f0 = f / 2
        sweep = 2 * np.pi * np.cumsum(f0 * (1 + 2 * np.exp(-t / 0.03))) / SR
        w = np.sin(sweep) * env(n, 0.001, 0.18) + 0.3 * np.random.default_rng(1).normal(size=n) * env(n, 0.001, 0.03)
    else:  # synth: soft square
        w = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 10, 2)) * env(n, 0.01, 0.3)
    return (w / (np.abs(w).max() + 1e-9)).astype(np.float32)


class Mixer:
    def __init__(self):
        self.voices, self.lock = [], threading.Lock()
        self.stream = sd.OutputStream(samplerate=SR, channels=1, dtype="float32", callback=self.cb, blocksize=256)
        self.stream.start()

    def play(self, wave, gain):
        with self.lock:
            self.voices.append([wave * gain, 0])

    def cb(self, out, frames, t, status):
        buf = np.zeros(frames, np.float32)
        with self.lock:
            for v in self.voices:
                chunk = v[0][v[1]:v[1] + frames]
                buf[:len(chunk)] += chunk
                v[1] += frames
            self.voices = [v for v in self.voices if v[1] < len(v[0])]
        out[:, 0] = np.tanh(buf)   # soft clip when voices overlap


def hz(note):
    return 440.0 * 2 ** ((midi(note) - 69) / 12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5006)
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    mix = Mixer()
    if args.test:
        for inst in ["piano", "marimba", "flute", "strings", "bell", "drum", "synth"]:
            print(inst, flush=True)
            mix.play(voice(inst, hz("C4")), 0.6)
            time.sleep(0.9)
        time.sleep(1)
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    print(f"synth listening on udp :{args.port}", flush=True)
    cache = {}
    while True:
        msg = json.loads(sock.recv(65536))
        if msg.get("type") != "lock":
            continue
        o = msg["object"]
        if not o.get("note"):
            continue
        key = (o["instrument"], o["note"])
        if key not in cache:
            cache[key] = voice(o["instrument"], hz(o["note"]))
        vol = o.get("volume") or 1.0
        mix.play(cache[key], 0.5 * vol)
        fb = msg.get("lesson")
        if fb and fb.get("done"):
            for i, n in enumerate(["C5", "E5", "G5", "C6"]):   # little fanfare
                threading.Timer(0.5 + 0.12 * i, mix.play, (voice("bell", hz(n), 0.8), 0.3)).start()
        print(f"{o['note']:>4} {o['instrument']:<8} vol {vol:.2f}"
              + ("" if not fb else f"  lesson {fb['index']}/{fb['total']} {'✓' if fb['correct'] else '✗'}"), flush=True)


if __name__ == "__main__":
    main()
