"""Config: built-in defaults, optionally overridden by a JSON file (deep-merged)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULTS = {
    # Frames are resized to this width before processing. Every *_frac value below is a
    # fraction of this width, so tuning survives resolution changes.
    "process_width": 640,
    # Colour prototypes, OpenCV HSV (H 0-180, S/V 0-255): each pixel goes to the NEAREST prototype.
    # Register real values per object with tools/tune_colors.py (click the object). Rainbow order =
    # suggested scale C D E F G A B C'. "bgr" is only the debug-overlay colour.
    "colors": {
        "red":    {"hsv": [0, 200, 170],   "bgr": [40, 40, 230]},
        "orange": {"hsv": [12, 210, 220],  "bgr": [20, 120, 245]},
        "yellow": {"hsv": [27, 200, 210],  "bgr": [40, 220, 240]},
        "green":  {"hsv": [62, 170, 150],  "bgr": [60, 200, 60]},
        "cyan":   {"hsv": [90, 180, 170],  "bgr": [220, 210, 40]},
        "blue":   {"hsv": [110, 200, 170], "bgr": [230, 120, 40]},
        "purple": {"hsv": [135, 150, 140], "bgr": [180, 60, 150]},
        "pink":   {"hsv": [165, 140, 210], "bgr": [180, 120, 245]},
    },
    "color_match": {
        "s_min": 90,        # below this saturation = table/glare/shadow, never an object
        "v_min": 60,        # below this brightness = shadow/black
        "hue_tol": 8,       # hue units that count as distance 1.0
        "sat_tol": 90,      # saturation units that count as distance 1.0
        "max_dist": 1.7,    # pixels farther than this from every prototype are ignored
    },
    "shape_net": {
        "enabled": True,
        "model_dir": "models/shape",
        "reject_min_prob": 0.6,     # drop a candidate (hand, scrap, pen) above this "reject" probability
    },
    "detector": {
        "blur": 5,                    # Gaussian kernel (odd) before HSV; 0 disables
        "morph": 5,                   # open/close kernel size in px
        "min_area_frac": 0.0015,      # contour area / frame area
        "max_area_frac": 0.25,
        "poly_eps": 0.02,             # approxPolyDP epsilon, fraction of hull perimeter
        "round_min_circlefill": 0.86, # area / min-enclosing-circle area (circle~1, hexagon .83, square .64)
        "square_max_aspect": 1.5,
        "square_min_rectfill": 0.90,  # area / min-area-rect area (square~1, circle .785)
        "square_max_vertices": 6,
        "square_min_solidity": 0.90,
        "cylinder_min_aspect": 1.25,
        "border_margin": 3,           # px; contours touching the frame edge are flagged partial
    },
    "tracker": {
        "max_jump_frac": 0.15,        # max centroid move between frames to count as same object
        "max_missing_s": 0.5,         # drop a track after this long unseen
        "confirm_hits": 3,            # frames seen before a track is reported
        "smooth": 0.5,                # EMA weight on the new centroid (1 = no smoothing)
        "shape_votes": 9,             # majority vote window for shape label
    },
    "selector": {
        "dwell_s": 0.5,               # look this long -> lock (= play note once)
        "grace_s": 0.15,              # gaze may leave/blink this long without resetting dwell
        "select_radius_frac": 0.03,   # gaze within this distance of an outline = confident hit
        "best_guess_radius_frac": 0.12,  # beyond select radius but within this = best guess
        "switch_margin_frac": 0.02,   # a new object must be this much closer to steal the target
        "gaze_max_age_s": 0.2,        # older gaze samples are treated as invalid
    },
    "depth": {
        # Filled by pressing 'c' in run.py with objects at a known distance (--ref-distance).
        "ref_distance_cm": None,
        "ref_size": {},               # "color/shape" or "shape" -> sqrt(area)/width at ref distance
        "near_cm": 40.0,              # volume 1.0 at/inside this
        "far_cm": 100.0,              # volume min_volume at/beyond this
        "min_volume": 0.2,
    },
    "gaze_udp_port": 5005,
    "events": {"host": "127.0.0.1", "port": 5006},
    "calib_marker": {"dictionary": "DICT_4X4_50"},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "colors":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)  # colours replace wholesale so removed colours stay removed
    return out


def load(path: str | None) -> dict:
    if path and Path(path).exists():
        with open(path) as f:
            user = json.load(f)
        cols = user.get("colors", {})
        if any(isinstance(c.get("hsv", [None])[0], list) for c in cols.values()):
            print(f"[config] {path}: v0 colour ranges found; using v1 colour prototypes instead", flush=True)
            user.pop("colors")
        return _merge(DEFAULTS, user)
    return copy.deepcopy(DEFAULTS)


def save(cfg: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
