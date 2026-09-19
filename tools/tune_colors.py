#!/usr/bin/env python3
"""Tune HSV colour ranges on the real camera/lighting, then save to config.json.

  python tools/tune_colors.py --source 0

Click an object -> its colour range is set from the clicked patch.
Trackbars fine-tune the selected colour. Keys: 1-9 select colour | s save | q quit
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outer_vision import config  # noqa: E402
from outer_vision.detector import Detector  # noqa: E402
from outer_vision.io import open_source  # noqa: E402

WIN = "tune-colors"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0")
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()
    cfg = config.load(args.config)
    names = list(cfg["colors"])
    src = open_source(args.source)
    state = {"sel": 0, "frame": None, "pushing": False}

    def rng():
        return cfg["colors"][names[state["sel"]]]["hsv"]

    def push_trackbars():
        r = rng()
        state["pushing"] = True   # setTrackbarPos fires on_trackbar with half-updated values
        # Red wraps around H=0/180: trackbars edit the low range's S/V; H stays as clicked.
        for i, key in enumerate(["H lo", "S lo", "V lo", "H hi", "S hi", "V hi"]):
            cv2.setTrackbarPos(key, WIN, int(r[0][i]))
        state["pushing"] = False

    def on_trackbar(_):
        if state["pushing"]:
            return
        vals = [cv2.getTrackbarPos(k, WIN) for k in ["H lo", "S lo", "V lo", "H hi", "S hi", "V hi"]]
        r = rng()
        if len(r) == 1:
            r[0] = vals
        else:  # wrapped red: keep hue split, share S/V floors
            for band in r:
                band[1], band[2], band[4], band[5] = vals[1], vals[2], vals[4], vals[5]

    def on_click(event, x, y, *_):
        if event != cv2.EVENT_LBUTTONDOWN or state["frame"] is None:
            return
        hsv = cv2.cvtColor(state["frame"], cv2.COLOR_BGR2HSV)
        patch = hsv[max(0, y - 4):y + 5, max(0, x % hsv.shape[1] - 4):x % hsv.shape[1] + 5].reshape(-1, 3)
        h, s, v = np.median(patch, 0).astype(int)
        s_lo, v_lo = max(60, s - 70), max(40, v - 90)
        name = names[state["sel"]]
        if h < 10 or h > 170:   # red-ish: needs both ends of the hue circle
            cfg["colors"][name]["hsv"] = [[0, s_lo, v_lo, (h + 10) % 180 if h < 10 else 10, 255, 255],
                                          [h - 10 if h > 170 else 170, s_lo, v_lo, 180, 255, 255]]
        else:
            cfg["colors"][name]["hsv"] = [[max(0, h - 10), s_lo, v_lo, min(180, h + 10), 255, 255]]
        print(f"{name}: clicked HSV=({h},{s},{v}) -> {cfg['colors'][name]['hsv']}", flush=True)
        push_trackbars()

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    for key, mx in [("H lo", 180), ("S lo", 255), ("V lo", 255), ("H hi", 180), ("S hi", 255), ("V hi", 255)]:
        cv2.createTrackbar(key, WIN, 0, mx, on_trackbar)
    cv2.setMouseCallback(WIN, on_click)
    push_trackbars()

    while True:
        frame, _, _ = src.read()
        if frame is None:
            break
        scale = cfg["process_width"] / frame.shape[1]
        frame = cv2.resize(frame, None, fx=scale, fy=scale)
        state["frame"] = frame
        mask = Detector(cfg).masks(frame)[names[state["sel"]]]
        vis = np.hstack([frame, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)])
        label = "  ".join(f"[{i + 1}]{n}" + ("*" if i == state["sel"] else "") for i, n in enumerate(names))
        cv2.putText(vis, label + "   click object | s save | q quit", (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(WIN, vis)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q"):
            break
        if k == ord("s"):
            config.save(cfg, args.config)
            print(f"saved {args.config}", flush=True)
        if ord("1") <= k <= ord("9") and k - ord("1") < len(names):
            state["sel"] = k - ord("1")
            push_trackbars()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
