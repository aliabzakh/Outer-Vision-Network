"""Debug drawing. Never feeds back into the pipeline."""
from __future__ import annotations

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img, s, org, scale=0.45, color=(255, 255, 255)):
    cv2.putText(img, s, org, FONT, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, s, org, FONT, scale, color, 1, cv2.LINE_AA)


def draw(frame, tracks, depth, cfg, gaze_px, selector, hud_lines, flash_id=None, markers=(), show_features=False):
    img = frame.copy()
    for t in tracks:
        col = tuple(cfg["colors"].get(t.color, {}).get("bgr", (255, 255, 255)))
        is_target = t.id == selector.target_id
        thick = 3 if is_target else 1
        if t.id == flash_id:
            overlay = img.copy()
            cv2.drawContours(overlay, [t.det.contour], -1, (255, 255, 255), -1)
            img = cv2.addWeighted(overlay, 0.5, img, 0.5, 0)
        cv2.drawContours(img, [t.det.contour], -1, col, thick, cv2.LINE_AA)
        x, y, w, h = t.det.bbox
        dist, vol = depth.get(t.id, (None, None))
        label = f"#{t.id} {t.color} {t.shape}"
        if t.det.shape_conf < 0.999:
            label += f" {t.det.shape_conf:.0%}"
        if dist is not None:
            label += f" {dist:.0f}cm v{vol:.2f}"
        if t.det.partial:
            label += " (edge)"
        _text(img, label, (x, max(12, y - 6)), color=col)
        if show_features:
            f = t.det.features
            _text(img, f"cf{f['circlefill']:.2f} rf{f['rectfill']:.2f} ar{f['aspect']:.2f} v{f['vertices']} so{f['solidity']:.2f}",
                  (x, y + h + 14), 0.38)
            if "net" in f:
                _text(img, " ".join(f"{k[:3]}{v:.2f}" for k, v in f["net"].items()), (x, y + h + 28), 0.38)
        if is_target and selector.progress > 0:
            c = (int(t.cx), int(t.cy))
            r = int(max(w, h) * 0.6) + 6
            done = selector.locked
            cv2.ellipse(img, c, (r, r), -90, 0, 360 * selector.progress,
                        (0, 255, 0) if done else (0, 255, 255), 3, cv2.LINE_AA)
    for m in markers:
        pts = np.array(m["corners"], np.int32)
        cv2.polylines(img, [pts], True, (255, 0, 255), 2)
        _text(img, f"marker {m['id']}", tuple(pts[0]), color=(255, 0, 255))
    if gaze_px is not None:
        g = (int(gaze_px[0]), int(gaze_px[1]))
        cv2.circle(img, g, 9, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.circle(img, g, 2, (0, 0, 255), -1)
    for i, s in enumerate(hud_lines):
        _text(img, s, (8, 18 + 18 * i))
    return img


def mask_view(label_map: np.ndarray, names: list, cfg: dict) -> np.ndarray:
    """Every pixel painted with the colour prototype it was assigned to (black = none)."""
    palette = np.zeros((256, 3), np.uint8)
    for i, n in enumerate(names):
        palette[i] = cfg["colors"][n].get("bgr", (255, 255, 255))
    return palette[label_map]
