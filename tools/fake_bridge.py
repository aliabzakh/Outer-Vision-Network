#!/usr/bin/env python3
"""Stand-in for qnx/camera_bridge: writes synthetic frames in the same OVF1 wire format to stdout.

  python run.py --source "pipe:python tools/fake_bridge.py --format nv12" --gaze synthetic
"""
import argparse
import struct
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outer_vision import synthetic  # noqa: E402

FMT = {"rgbx": 1, "bgrx": 2, "nv12": 3}


def encode(bgr, fmt):
    h, w = bgr.shape[:2]
    if fmt == "rgbx":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA).tobytes()
    if fmt == "bgrx":
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA).tobytes()
    i420 = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420).reshape(-1)
    y, u, v = i420[:w * h], i420[w * h:w * h * 5 // 4], i420[w * h * 5 // 4:]
    uv = np.empty(w * h // 2, np.uint8)
    uv[0::2], uv[1::2] = u, v
    return y.tobytes() + uv.tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=FMT, default="bgrx")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--width", type=int, default=1280)
    args = ap.parse_args()
    out = sys.stdout.buffer
    i = 0
    while not args.frames or i < args.frames:
        t0 = time.monotonic()
        img = synthetic.render(i)
        if args.width != img.shape[1]:
            img = cv2.resize(img, (args.width, args.width * 3 // 4))
        h, w = img.shape[:2]
        try:
            out.write(struct.pack("<4sIIIQ", b"OVF1", w, h, FMT[args.format], int(time.time() * 1e6)))
            out.write(encode(img, args.format))
            out.flush()
        except BrokenPipeError:
            break
        i += 1
        time.sleep(max(0.0, 1 / args.fps - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
