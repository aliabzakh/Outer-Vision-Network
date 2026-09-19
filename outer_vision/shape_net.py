"""Learned shape classifier (round / square / cylinder / reject) on object crops.

Backends, in order: ncnn (the QNX AI module; also pip-installable on Mac/Linux) -> OpenCV DNN on the
same model's ONNX export -> None (the detector falls back to contour rules).
Model input: 1x3xSxS float RGB in 0..255 (the model divides by 255 itself).
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


class ShapeNet:
    def __init__(self, model_dir="models/shape", prefer="ncnn", threads=2):
        d = Path(model_dir)
        meta = json.loads((d / "labels.json").read_text())
        self.labels, self.size = meta["labels"], meta["input_size"]
        self.backend = None
        if prefer == "ncnn" and (d / "shape.ncnn.param").exists():
            try:
                import ncnn
                self.net = ncnn.Net()
                self.net.opt.use_vulkan_compute = False
                self.net.opt.num_threads = threads
                if self.net.load_param(str(d / "shape.ncnn.param")) == 0 and self.net.load_model(str(d / "shape.ncnn.bin")) == 0:
                    self._ncnn = ncnn
                    self.backend = "ncnn"
            except ImportError:
                pass
        if self.backend is None and (d / "shape.onnx").exists():
            self.net = cv2.dnn.readNetFromONNX(str(d / "shape.onnx"))
            self.backend = "opencv-dnn"
        if self.backend is None:
            raise FileNotFoundError(f"no usable model in {d}")

    def probs(self, crops: list) -> np.ndarray:
        """crops: list of BGR uint8 SxS images -> (N, n_labels) softmax probabilities."""
        if not crops:
            return np.zeros((0, len(self.labels)), np.float32)
        if self.backend == "ncnn":
            out = []
            for c in crops:
                m = self._ncnn.Mat.from_pixels(np.ascontiguousarray(c), self._ncnn.Mat.PixelType.PIXEL_BGR2RGB, c.shape[1], c.shape[0])
                ex = self.net.create_extractor()
                ex.input("in0", m)
                _, o = ex.extract("out0")
                out.append(np.array(o).reshape(-1))
            logits = np.stack(out)
        else:
            blob = cv2.dnn.blobFromImages(crops, 1.0, (self.size, self.size), swapRB=True)
            self.net.setInput(blob)
            logits = self.net.forward().reshape(len(crops), -1)
        e = np.exp(logits - logits.max(1, keepdims=True))
        return e / e.sum(1, keepdims=True)


def load(cfg: dict):
    """Returns a ShapeNet, or None if disabled/missing (detector then uses rules)."""
    c = cfg.get("shape_net", {})
    if not c.get("enabled", True):
        return None
    try:
        return ShapeNet(c.get("model_dir", "models/shape"), c.get("backend", "ncnn"), c.get("threads", 2))
    except (FileNotFoundError, cv2.error, OSError) as e:
        print(f"[shape_net] disabled ({e}); using contour rules", flush=True)
        return None
