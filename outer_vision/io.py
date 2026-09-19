"""Frame sources, gaze inputs, event output, recording, calibration-marker detection."""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

from . import synthetic


# ---------------------------------------------------------------- frame sources
class CameraSource:
    """Live camera: int index, or any URL/string cv2.VideoCapture accepts, e.g. the Pi's MJPEG stream
    http://<pi>.local:8081/stream from tools/pi_camera_server.py."""
    live = True

    def __init__(self, spec, width=640, height=480, fps=30):
        self.cap = cv2.VideoCapture(int(spec) if str(spec).isdigit() else spec)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # newest frame, not a queue of stale ones
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open camera {spec!r} (macOS: allow camera access for your terminal in System Settings > Privacy & Security > Camera; try index 1 for an iPhone/USB camera)")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or fps
        self.idx = -1

    def read(self):
        ok, frame = self.cap.read()
        self.idx += 1
        return (frame if ok else None), time.monotonic(), self.idx


class VideoSource:
    """Recorded video. Time comes from the frame index so dwell timing matches the original run."""
    live = False

    def __init__(self, path):
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open video {path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.idx = -1

    def read(self):
        ok, frame = self.cap.read()
        self.idx += 1
        return (frame if ok else None), self.idx / self.fps, self.idx


class SyntheticSource:
    live = False
    fps = 30.0

    def __init__(self, n_frames=None):
        self.n, self.idx = n_frames, -1

    def read(self):
        self.idx += 1
        if self.n is not None and self.idx >= self.n:
            return None, self.idx / self.fps, self.idx
        return synthetic.render(self.idx), self.idx / self.fps, self.idx


class PicamSource:
    """Raspberry Pi camera via picamera2 (Pi OS). spec: "picam" or "picam:<index>"."""
    live = True

    def __init__(self, spec: str, width=1280, height=720, fps=30):
        from picamera2 import Picamera2   # only exists on the Pi
        idx = int(spec.split(":", 1)[1]) if ":" in spec else 0
        self.cam = Picamera2(idx)
        cfg = self.cam.create_video_configuration(main={"size": (width, height), "format": "RGB888"},
                                                  controls={"FrameRate": fps})
        self.cam.configure(cfg)
        self.cam.start()
        self.fps, self.idx = fps, -1

    def read(self):
        frame = self.cam.capture_array()   # "RGB888" in picamera2 is BGR byte order, i.e. OpenCV-ready
        self.idx += 1
        return frame, time.monotonic(), self.idx

    def close(self):
        self.cam.stop()


def open_source(spec: str):
    if spec == "synthetic":
        return SyntheticSource()
    if spec.startswith("picam"):
        return PicamSource(spec)
    if Path(spec).is_file():
        return VideoSource(spec)
    return CameraSource(spec)


# ---------------------------------------------------------------- gaze inputs
# All gaze is normalized world-camera coordinates: x, y in [0, 1], origin top-left. See INTERFACE.md.
class NoGaze:
    name = "none"

    def get(self, frame_idx):
        return None


class MouseGaze:
    """Mouse position over the debug window stands in for gaze."""
    name = "mouse"

    def __init__(self):
        self.pos = None

    def on_mouse(self, event, x, y, flags, frame_wh):
        w, h = frame_wh
        self.pos = (x / w, y / h)

    def get(self, frame_idx):
        return self.pos


class UdpGaze:
    """Listens for gaze JSON from the inner-camera process. Stale samples count as no gaze."""
    name = "udp"

    def __init__(self, port: int, max_age_s: float):
        self.max_age = max_age_s
        self.latest = None       # (x, y, recv_monotonic)
        self.count = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            data, _ = self.sock.recvfrom(4096)
            try:
                g = json.loads(data)
                if g.get("valid", True):
                    self.latest = (float(g["x"]), float(g["y"]), time.monotonic())
                else:
                    self.latest = None
                self.count += 1
            except (ValueError, KeyError, TypeError):
                pass

    def get(self, frame_idx):
        g = self.latest
        if g is None or time.monotonic() - g[2] > self.max_age:
            return None
        return g[0], g[1]

    def age_ms(self):
        g = self.latest
        return None if g is None else round((time.monotonic() - g[2]) * 1000, 1)


class ReplayGaze:
    """Gaze from a recording's log.jsonl, matched by frame index."""
    name = "replay"

    def __init__(self, log_path):
        self.by_frame = {}
        with open(log_path) as f:
            for line in f:
                r = json.loads(line)
                if "frame" in r:
                    self.by_frame[r["frame"]] = r.get("gaze")

    def get(self, frame_idx):
        g = self.by_frame.get(frame_idx)
        return tuple(g) if g else None


class SyntheticGaze:
    name = "synthetic"

    def get(self, frame_idx):
        return synthetic.gaze(frame_idx)


def open_gaze(spec: str, cfg: dict):
    if spec == "none":
        return NoGaze()
    if spec == "mouse":
        return MouseGaze()
    if spec == "udp":
        return UdpGaze(cfg["gaze_udp_port"], cfg["selector"]["gaze_max_age_s"])
    if spec == "synthetic":
        return SyntheticGaze()
    if spec.startswith("replay:"):
        return ReplayGaze(spec.split(":", 1)[1])
    raise ValueError(f"unknown gaze source {spec!r}")


# ---------------------------------------------------------------- output
class Publisher:
    """Fire-and-forget UDP JSON to the game/audio process."""

    def __init__(self, host: str, port: int):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, msg: dict):
        try:
            self.sock.sendto(json.dumps(msg, separators=(",", ":")).encode(), self.addr)
        except OSError:
            pass  # receiver not up yet; never let output stall the vision loop


class Recorder:
    """Raw (un-annotated) world frames + per-frame JSON log, so runs can be replayed through new code."""

    def __init__(self, root="recordings", fps=30.0, cfg=None):
        self.dir = Path(root) / time.strftime("%Y%m%d-%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fps, self.writer = fps, None
        self.log = open(self.dir / "log.jsonl", "w")
        self.frame = 0
        if cfg is not None:
            (self.dir / "config.json").write_text(json.dumps(cfg, indent=2))

    def write(self, frame, record: dict):
        if self.writer is None:
            h, w = frame.shape[:2]
            self.writer = cv2.VideoWriter(str(self.dir / "world.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
        self.writer.write(frame)
        # Re-index from 0 so the log lines up with world.mp4 when replayed.
        self.log.write(json.dumps({**record, "frame": self.frame}, separators=(",", ":")) + "\n")
        self.frame += 1

    def close(self):
        if self.writer is not None:
            self.writer.release()
        self.log.close()


class MjpegServer:
    """Debug overlay as an MJPEG stream: open http://<pi>:<port>/ on the Mac. Stdlib only."""

    PAGE = (b"<html><head><title>outer-vision</title></head><body style='margin:0;background:#111'>"
            b"<img src='/stream' style='width:100%;height:auto'></body></html>")

    def __init__(self, port: int, quality=70, max_fps=15):
        self.quality, self.min_dt = quality, 1.0 / max_fps
        self._jpeg, self._last = None, 0.0
        self._cond = threading.Condition()
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path != "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(srv.PAGE)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        with srv._cond:
                            srv._cond.wait(timeout=2.0)
                            jpg = srv._jpeg
                        if jpg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def wants_frame(self) -> bool:
        return time.monotonic() - self._last >= self.min_dt

    def publish(self, img):
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if ok:
            with self._cond:
                self._jpeg, self._last = buf.tobytes(), time.monotonic()
                self._cond.notify_all()


class CalibMarker:
    """Finds ArUco markers so the inner-camera team can pair pupil positions with world positions."""

    def __init__(self, dictionary="DICT_4X4_50"):
        d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
        self.det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())

    def detect(self, frame):
        h, w = frame.shape[:2]
        corners, ids, _ = self.det.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        if ids is None:
            return []
        return [
            {"id": int(i), "x": float(c[0][:, 0].mean() / w), "y": float(c[0][:, 1].mean() / h), "corners": c[0].tolist()}
            for c, i in zip(corners, ids.flatten())
        ]
