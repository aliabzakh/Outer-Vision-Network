"""Colour segmentation + contour shape classification. Pure OpenCV/NumPy so it ports to C++ 1:1."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Detection:
    color: str
    shape: str               # "round" | "square" | "cylinder"
    shape_conf: float        # 1.0 = clean rule match, 0.5 = fallback guess
    contour: np.ndarray      # Nx1x2 int32, process-resolution pixels
    cx: float
    cy: float
    area: float
    bbox: tuple              # x, y, w, h
    partial: bool            # touches the frame edge -> shape/size unreliable
    features: dict


def shape_features(contour: np.ndarray, poly_eps: float) -> dict:
    area = cv2.contourArea(contour)
    hull = cv2.convexHull(contour)
    hull_area = max(cv2.contourArea(hull), 1e-6)
    hull_perim = cv2.arcLength(hull, True)
    (_, _), (rw, rh), _ = cv2.minAreaRect(contour)
    long_side, short_side = max(rw, rh), max(min(rw, rh), 1e-6)
    (_, _), r_enc = cv2.minEnclosingCircle(contour)
    # Approximate the hull, not the raw contour: concave mask noise shouldn't add corners.
    vertices = len(cv2.approxPolyDP(hull, poly_eps * hull_perim, True))
    return {
        "aspect": long_side / short_side,
        "rectfill": area / max(rw * rh, 1e-6),
        "circlefill": area / max(math.pi * r_enc * r_enc, 1e-6),
        "solidity": area / hull_area,
        "vertices": vertices,
    }


def classify_shape(f: dict, p: dict) -> tuple:
    # Ball: always projects to a near-circle, which nothing else on the table does.
    if f["circlefill"] >= p["round_min_circlefill"] and f["aspect"] < p["cylinder_min_aspect"]:
        return "round", 1.0
    # Cube/box: compact and straight-edged (a square face, or a hexagon seen from a corner).
    if f["aspect"] < p["square_max_aspect"] and (
        f["rectfill"] >= p["square_min_rectfill"]
        or (f["vertices"] <= p["square_max_vertices"] and f["solidity"] >= p["square_min_solidity"])
    ):
        return "square", 1.0
    # Upright cylinder: elongated silhouette with curved caps.
    if f["aspect"] >= p["cylinder_min_aspect"]:
        return "cylinder", 1.0
    return ("square", 0.5) if f["vertices"] <= p["square_max_vertices"] else ("round", 0.5)


class Detector:
    def __init__(self, cfg: dict):
        self.colors = cfg["colors"]
        self.p = cfg["detector"]
        k = max(1, int(self.p["morph"]))
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        self._bounds = {
            name: [(np.array(r[:3], np.uint8), np.array(r[3:], np.uint8)) for r in c["hsv"]]
            for name, c in self.colors.items()
        }

    def masks(self, frame: np.ndarray) -> dict:
        b = int(self.p["blur"])
        if b > 1:
            frame = cv2.GaussianBlur(frame, (b | 1, b | 1), 0)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        out = {}
        for name, bounds in self._bounds.items():
            m = cv2.inRange(hsv, *bounds[0])
            for lo, hi in bounds[1:]:
                m |= cv2.inRange(hsv, lo, hi)
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self.kernel)
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, self.kernel)
            out[name] = m
        return out

    def detect(self, frame: np.ndarray) -> list:
        h, w = frame.shape[:2]
        frame_area = float(h * w)
        lo, hi = self.p["min_area_frac"] * frame_area, self.p["max_area_frac"] * frame_area
        bm = self.p["border_margin"]
        dets = []
        for name, mask in self.masks(frame).items():
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                area = cv2.contourArea(c)
                if not lo <= area <= hi:
                    continue
                m = cv2.moments(c)
                x, y, bw, bh = cv2.boundingRect(c)
                feats = shape_features(c, self.p["poly_eps"])
                shape, conf = classify_shape(feats, self.p)
                dets.append(Detection(
                    color=name, shape=shape, shape_conf=conf, contour=c,
                    cx=m["m10"] / m["m00"], cy=m["m01"] / m["m00"], area=area,
                    bbox=(x, y, bw, bh),
                    partial=x <= bm or y <= bm or x + bw >= w - bm or y + bh >= h - bm,
                    features=feats,
                ))
        return dets
