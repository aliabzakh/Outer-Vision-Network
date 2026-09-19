"""Rendered fake table scene + fake gaze, so the pipeline can be developed and tested without hardware."""
from __future__ import annotations

import math

import cv2
import numpy as np

# (color, shape, x, y, size) in 640x480 scene pixels. Back row (smaller y) drawn smaller = farther.
DEFAULT_OBJECTS = [
    ("red", "round", 150, 330, 46),
    ("blue", "square", 330, 360, 48),
    ("green", "cylinder", 500, 320, 40),
    ("yellow", "round", 230, 170, 32),
    ("red", "cylinder", 430, 160, 30),
]
BGR = {"red": (30, 30, 200), "yellow": (30, 200, 220), "green": (50, 170, 50), "blue": (200, 90, 30)}


def _shade(c, k):
    return tuple(int(min(255, v * k)) for v in c)


def render(frame_idx: int, objects=DEFAULT_OBJECTS, w=640, h=480, seed=0, head_motion=True) -> np.ndarray:
    rng = np.random.default_rng(seed + frame_idx)
    # Grey table with a lighting gradient: saturation stays ~0 so it must never be detected.
    grad = np.linspace(150, 110, h, dtype=np.float32)[:, None]
    img = np.repeat(np.repeat(grad, w, 1)[:, :, None], 3, 2)
    img = np.clip(img + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)
    t = frame_idx / 30.0
    dx = 12 * math.sin(t * 0.9) if head_motion else 0.0   # slow head sway
    dy = 6 * math.sin(t * 1.3) if head_motion else 0.0
    for color, shape, x, y, s in objects:
        x, y, c = int(x + dx), int(y + dy), BGR[color]
        cv2.ellipse(img, (x + s // 4, y + s // 2), (s, s // 3), 0, 0, 360, (70, 70, 70), -1)  # soft shadow
        if shape == "round":
            cv2.circle(img, (x, y), s // 2 + s // 4, _shade(c, 0.7), -1, cv2.LINE_AA)
            cv2.circle(img, (x - s // 8, y - s // 8), s // 2, c, -1, cv2.LINE_AA)
        elif shape == "cylinder":
            r, hgt, e = s // 2, int(s * 1.6), s // 5
            cv2.rectangle(img, (x - r, y - hgt // 2), (x + r, y + hgt // 2), _shade(c, 0.8), -1)
            cv2.ellipse(img, (x, y + hgt // 2), (r, e), 0, 0, 180, _shade(c, 0.8), -1, cv2.LINE_AA)
            cv2.ellipse(img, (x, y - hgt // 2), (r, e), 0, 0, 360, _shade(c, 1.15), -1, cv2.LINE_AA)
        elif shape == "square":
            a = s // 2
            top = np.array([[x - a, y - a], [x - a // 2, y - a - a // 2], [x + a + a // 2, y - a - a // 2], [x + a, y - a]])
            side = np.array([[x + a, y - a], [x + a + a // 2, y - a - a // 2], [x + a + a // 2, y + a // 2], [x + a, y + a]])
            cv2.rectangle(img, (x - a, y - a), (x + a, y + a), c, -1)
            cv2.fillPoly(img, [top], _shade(c, 1.2), cv2.LINE_AA)
            cv2.fillPoly(img, [side], _shade(c, 0.65), cv2.LINE_AA)
    return cv2.GaussianBlur(img, (3, 3), 0)


def gaze(frame_idx: int, objects=DEFAULT_OBJECTS, w=640, h=480, dwell_frames=30, move_frames=6, seed=0):
    """Visits each object in turn (1 s fixation, 0.2 s saccade), with jitter and occasional blinks.

    Returns normalized (x, y) or None during a blink.
    """
    period = dwell_frames + move_frames
    i, k = divmod(frame_idx, period)
    a, b = objects[i % len(objects)], objects[(i + 1) % len(objects)]
    t = frame_idx / 30.0
    dx, dy = 12 * math.sin(t * 0.9), 6 * math.sin(t * 1.3)
    if k < dwell_frames:
        x, y = a[2], a[3]
        if k in (15, 16, 17):   # 100 ms blink mid-fixation: must NOT reset the dwell
            return None
    else:
        u = (k - dwell_frames) / move_frames
        x, y = a[2] + (b[2] - a[2]) * u, a[3] + (b[3] - a[3]) * u
    rng = np.random.default_rng(seed + 10_000 + frame_idx)
    x, y = x + dx + rng.normal(0, 4), y + dy + rng.normal(0, 4)
    return (x / w, y / h)
