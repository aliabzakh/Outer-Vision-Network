#!/usr/bin/env python3
"""Reference gaze sender for the inner-camera team: shows the exact UDP message run.py expects.

  python tools/send_gaze.py --demo     # sweeps a fake gaze point in a circle at 60 Hz
"""
import argparse
import json
import math
import socket
import time

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=5005)
ap.add_argument("--demo", action="store_true")
args = ap.parse_args()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send(x, y, valid=True, conf=1.0):
    """x, y: normalized WORLD-camera coords in [0,1], origin top-left."""
    msg = {"x": x, "y": y, "valid": valid, "conf": conf, "t": time.time()}
    sock.sendto(json.dumps(msg).encode(), (args.host, args.port))


if args.demo:
    t0 = time.time()
    while True:
        a = (time.time() - t0) * 0.5
        send(0.5 + 0.3 * math.cos(a), 0.5 + 0.3 * math.sin(a))
        time.sleep(1 / 60)
