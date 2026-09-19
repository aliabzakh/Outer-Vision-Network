#!/usr/bin/env python3
"""Outer vision: world camera -> objects -> gaze target -> dwell lock -> UDP events.

Examples:
  python run.py --source synthetic --gaze synthetic          # no hardware at all
  python run.py --source 0 --gaze mouse                       # webcam, mouse = gaze
  python run.py --source 0 --gaze udp --record                # real gaze from the inner-camera process
  python run.py --source recordings/X/world.mp4 --gaze replay:recordings/X/log.jsonl
  python run.py --source http://192.168.2.2:8081/stream --gaze udp --stream 8080      # Pi camera -> laptop
  python run.py --source picam --gaze udp --headless --stream 8080                     # everything on the Pi

Keys: q quit | m mask view | f shape features | r record on/off | c depth-calibrate | p pause | s snapshot
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from outer_vision import config, overlay, shape_net
from outer_vision.detector import Detector
from outer_vision.io import CalibMarker, MjpegServer, MouseGaze, Publisher, Recorder, open_gaze, open_source
from outer_vision.selector import Selector
from outer_vision.tracker import Tracker, estimate_depth, reference_sizes

WIN = "outer-vision"


def obj_json(t, depth, w, h):
    x, y, bw, bh = t.det.bbox
    dist, vol = depth.get(t.id, (None, None))
    return {
        "id": t.id, "color": t.color, "shape": t.shape,
        "x": round(t.cx / w, 4), "y": round(t.cy / h, 4),
        "bbox": [round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)],
        "distance_cm": None if dist is None else round(dist, 1),
        "volume": None if vol is None else round(vol, 3),
        "partial": t.det.partial,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="0", help="camera index | video file | 'synthetic' | 'picam[:N]' | MJPEG URL")
    ap.add_argument("--gaze", default="mouse", help="mouse | udp | synthetic | replay:<log.jsonl> | none")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--send", default=None, help="host:port for events (default from config)")
    ap.add_argument("--record", action="store_true", help="start recording immediately")
    ap.add_argument("--headless", action="store_true", help="no window (e.g. on the Pi)")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--snapshot-every", type=int, default=0, help="headless: save annotated frame every N frames")
    ap.add_argument("--calib-marker", action="store_true", help="detect ArUco markers for gaze calibration")
    ap.add_argument("--ref-distance", type=float, default=60.0, help="cm, used by the 'c' depth calibration")
    ap.add_argument("--realtime", action="store_true", help="pace file/synthetic sources to their fps")
    ap.add_argument("--stream", type=int, default=0, help="serve the debug overlay as MJPEG on this port")
    ap.add_argument("--no-net", action="store_true", help="ignore the shape net; contour rules only")
    args = ap.parse_args()

    cfg = config.load(args.config)
    src = open_source(args.source)
    gaze = open_gaze(args.gaze, cfg)
    if args.no_net:
        cfg["shape_net"]["enabled"] = False
    net = shape_net.load(cfg)
    det, trk, sel = Detector(cfg, net), Tracker(cfg), Selector(cfg)
    backend = net.backend if net else "rules"
    stream = MjpegServer(args.stream) if args.stream else None
    if stream:
        print(f"[stream] http://0.0.0.0:{args.stream}/", flush=True)
    print(f"[shape] {backend}", flush=True)
    marker = CalibMarker(cfg["calib_marker"]["dictionary"]) if args.calib_marker else None
    host, port = (args.send.split(":") if args.send else (cfg["events"]["host"], cfg["events"]["port"]))
    pub = Publisher(host, int(port))
    rec = Recorder(fps=src.fps, cfg=cfg) if args.record else None

    show = not args.headless
    view_mask = show_feat = paused = False
    flash_id, flash_until = None, 0.0
    snap_dir = Path("recordings/snapshots")
    fps_ema, last_wall = 0.0, time.monotonic()
    frame = t = idx = None
    locks = 0

    if show:
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)

    while True:
        if not paused or frame is None:
            raw, t, idx = src.read()
            if raw is None:
                break
            scale = cfg["process_width"] / raw.shape[1]
            frame = raw if abs(scale - 1) < 1e-3 else cv2.resize(raw, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        h, w = frame.shape[:2]
        if show and isinstance(gaze, MouseGaze):
            cv2.setMouseCallback(WIN, gaze.on_mouse, (w, h))

        t0 = time.perf_counter()
        dets = det.detect(frame)
        tracks = trk.update(dets, t, w)
        g = gaze.get(idx)
        gaze_px = (g[0] * w, g[1] * h) if g is not None else None
        events = sel.update(tracks, gaze_px, t, w)
        depth = {tr.id: estimate_depth(tr, cfg, w) for tr in tracks}
        markers = marker.detect(frame) if marker else []
        proc_ms = (time.perf_counter() - t0) * 1000

        objs = {tr.id: obj_json(tr, depth, w, h) for tr in tracks}
        state = {
            "type": "state", "t": round(t, 4), "frame": idx,
            "gaze": None if g is None else [round(g[0], 4), round(g[1], 4)],
            "target": sel.target_id, "dwell": round(sel.progress, 3), "best_guess": sel.best_guess,
            "objects": list(objs.values()),
            "health": {
                "fps": round(fps_ema, 1), "proc_ms": round(proc_ms, 1), "shape": backend,
                "rejected": det.rejected,
                "gaze_age_ms": gaze.age_ms() if hasattr(gaze, "age_ms") else None,
            },
        }
        if marker:
            state["markers"] = [{k: m[k] for k in ("id", "x", "y")} for m in markers]
        pub.send(state)
        out_events = []
        for e in events:
            e = {**e, "t": round(t, 4), "object": objs.get(e["id"])}
            out_events.append(e)
            pub.send(e)
            locks += 1
            flash_id, flash_until = e["id"], time.monotonic() + 0.25
            print(f"[lock] #{e['id']} {e['object']['color']} {e['object']['shape']}"
                  f"{' (best guess)' if e['best_guess'] else ''} t={t:.2f}", flush=True)
        if rec:
            rec.write(frame, {"t": state["t"], "gaze": state["gaze"], "target": sel.target_id,
                              "dwell": state["dwell"], "events": out_events})

        now = time.monotonic()
        dt = now - last_wall
        last_wall = now
        fps_ema = 0.9 * fps_ema + 0.1 * (1.0 / max(dt, 1e-6))
        want_snap = args.headless and args.snapshot_every and idx % args.snapshot_every == 0
        want_stream = stream is not None and stream.wants_frame()
        if show or want_snap or want_stream:
            hud = [f"{fps_ema:4.1f} fps  proc {proc_ms:4.1f} ms  shape:{backend}  gaze:{gaze.name}  "
                   f"objs:{len(tracks)}  rejected:{det.rejected}  locks:{locks}",
                   ("REC " if rec else "") + ("PAUSED " if paused else "") +
                   (f"depth ref {cfg['depth']['ref_distance_cm']}cm" if cfg['depth']['ref_distance_cm'] else "depth: uncalibrated (c)")]
            img = overlay.draw(frame, tracks, depth, cfg, gaze_px, sel, hud,
                               flash_id if now < flash_until else None, markers, show_feat)
            if view_mask:
                img = overlay.mask_view(det.label_map(frame), det.names, cfg)
            if want_stream:
                stream.publish(img)
            if want_snap:
                snap_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(snap_dir / f"frame_{idx:05d}.png"), img)
        if show:
            cv2.imshow(WIN, img)
            wait = 1
            if args.realtime and not src.live:
                wait = max(1, int(1000 / src.fps - (time.monotonic() - now) * 1000))
            k = cv2.waitKey(wait) & 0xFF
            if k == ord("q"):
                break
            elif k == ord("m"):
                view_mask = not view_mask
            elif k == ord("f"):
                show_feat = not show_feat
            elif k == ord("p"):
                paused = not paused
            elif k == ord("s"):
                snap_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(snap_dir / f"snap_{time.strftime('%H%M%S')}.png"), img)
            elif k == ord("r"):
                if rec:
                    rec.close()
                    print(f"[rec] saved {rec.dir}", flush=True)
                    rec = None
                else:
                    rec = Recorder(fps=src.fps, cfg=cfg)
                    print(f"[rec] recording to {rec.dir}", flush=True)
            elif k == ord("c"):
                cfg["depth"]["ref_distance_cm"] = args.ref_distance
                cfg["depth"]["ref_size"] = reference_sizes(tracks, w)
                config.save(cfg, args.config)
                print(f"[depth] reference at {args.ref_distance} cm: {cfg['depth']['ref_size']} -> {args.config}", flush=True)
        elif args.realtime and not src.live:
            time.sleep(max(0.0, 1.0 / src.fps - (time.monotonic() - now)))
        if args.max_frames and idx + 1 >= args.max_frames:
            break

    if rec:
        rec.close()
        print(f"[rec] saved {rec.dir}")
    if hasattr(src, "close"):
        src.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
