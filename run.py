#!/usr/bin/env python3
"""Outer vision: world camera -> objects -> gaze target -> dwell -> note events (+ Maestro voice assistant).

Examples:
  python run.py --source synthetic --gaze synthetic                  # no hardware at all
  python run.py --source 0 --gaze mouse --omni                       # webcam, mouse = gaze, voice assistant
  python run.py --source http://pi.local:8081/stream --gaze udp       # Pi camera streamed to the Mac
  python run.py --source recordings/X/world.mp4 --gaze replay:recordings/X/log.jsonl

Keys: q quit | v talk to Maestro | m colour view | f features | r record | c depth-calibrate | p pause | s snapshot
Env:  OMNI_API_KEY (voice assistant), SENTRY_DSN (tracing/logs, optional)
"""
from __future__ import annotations

import argparse
import collections
import time
from pathlib import Path

import cv2

from outer_vision import config, overlay, shape_net, telemetry
from outer_vision.detector import Detector
from outer_vision.io import CalibMarker, MjpegServer, MouseGaze, Publisher, Recorder, open_gaze, open_source
from outer_vision.music import Music
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


def point_in(poly, pt):
    import numpy as np
    return pt is not None and cv2.pointPolygonTest(np.array(poly, np.float32), (float(pt[0]), float(pt[1])), False) >= 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="0", help="camera index | video file | 'synthetic' | 'picam[:N]' | MJPEG URL")
    ap.add_argument("--gaze", default="mouse", help="mouse | udp | synthetic | replay:<log.jsonl> | none")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--send", default=None, help="host:port for events (default from config)")
    ap.add_argument("--record", action="store_true", help="start recording immediately")
    ap.add_argument("--headless", action="store_true", help="no window")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--snapshot-every", type=int, default=0, help="headless: save annotated frame every N frames")
    ap.add_argument("--calib-marker", action="store_true", help="report ArUco markers for gaze calibration")
    ap.add_argument("--ref-distance", type=float, default=60.0, help="cm, used by the 'c' depth calibration")
    ap.add_argument("--realtime", action="store_true", help="pace file/synthetic sources to their fps")
    ap.add_argument("--stream", type=int, default=0, help="serve the debug overlay as MJPEG on this port")
    ap.add_argument("--no-net", action="store_true", help="ignore the shape net; contour rules only")
    ap.add_argument("--omni", action="store_true", help="enable the Maestro voice assistant (mic + speaker)")
    args = ap.parse_args()

    cfg = config.load(args.config)
    if args.no_net:
        cfg["shape_net"]["enabled"] = False
    tele = telemetry.init()
    src = open_source(args.source)
    gaze = open_gaze(args.gaze, cfg)
    net = shape_net.load(cfg)
    det, trk, sel = Detector(cfg, net), Tracker(cfg), Selector(cfg)
    music = Music(cfg)
    backend = net.backend if net else "rules"
    host, port = (args.send.split(":") if args.send else (cfg["events"]["host"], cfg["events"]["port"]))
    pub = Publisher(host, int(port))
    rec = Recorder(fps=src.fps, cfg=cfg) if args.record else None
    stream = MjpegServer(args.stream) if args.stream else None
    print(f"[shape] {backend}  [sentry] {'on' if tele else 'off'}", flush=True)
    if stream:
        print(f"[stream] http://0.0.0.0:{args.stream}/", flush=True)

    ocfg = cfg["omni"]
    triggers = set(ocfg["trigger"])
    use_markers = args.calib_marker or (args.omni and "marker" in triggers)
    marker = CalibMarker(cfg["calib_marker"]["dictionary"]) if use_markers else None

    # Latest scene, shared with the assistant thread (it renders its own annotated snapshot on demand).
    latest = {}
    focus_counts = collections.Counter()
    assistant = mic = None
    if args.omni:
        from outer_vision.audio import Mic, Player
        from outer_vision.omni import Assistant

        def context():
            s = dict(latest)
            voices = {tid: music.voice_of(o["color"], o["shape"]) for tid, o in s["objects"].items()}
            img = overlay.draw(s["frame"], s["tracks"], s["depth"], cfg, s["gaze_px"], sel, [], voices=voices)
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            objs = {tid: {**o, **voices[tid]} for tid, o in s["objects"].items()}
            focus = focus_counts.most_common(1)[0][0] if focus_counts else s["target"]
            return {"jpeg": jpg.tobytes(), "objects": objs, "focus": focus, "selector": sel}

        def on_utterance(wav):
            focus = next((k for k, _ in focus_counts.most_common() if k is not None), latest.get("target"))
            telemetry.log("utterance", seconds=round(len(wav) / 32000, 2), focus=str(focus))
            assistant.submit(wav, focus)

        assistant = Assistant(cfg, music, context, Player(), pub.send, log=lambda m: print(m, flush=True))
        mic = Mic(on_utterance, vad="vad" in triggers, max_s=ocfg["max_utterance_s"])
        print(f"[omni] {assistant.client.model} via {assistant.client.url}; talk with: {sorted(triggers)}", flush=True)

    show = not args.headless
    view_mask = show_feat = paused = False
    flash_id, flash_until = None, 0.0
    snap_dir = Path("recordings/snapshots")
    fps_ema, last_wall = 0.0, time.monotonic()
    frame = t = idx = None
    locks = 0
    talk_progress, talk_armed_ok, last_t = 0.0, True, None

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
        dt = 0.0 if last_t is None else max(0.0, t - last_t)
        last_t = t
        if show and isinstance(gaze, MouseGaze):
            cv2.setMouseCallback(WIN, gaze.on_mouse, (w, h))

        t0 = time.perf_counter()
        with telemetry.frame_trace(idx) as tx:
            with telemetry.span(tx, "detect", n_colors=len(cfg["colors"])):
                dets = det.detect(frame)
            with telemetry.span(tx, "track"):
                tracks = trk.update(dets, t, w)
            g = gaze.get(idx)
            gaze_px = (g[0] * w, g[1] * h) if g is not None else None
            with telemetry.span(tx, "select"):
                events = sel.update(tracks, gaze_px, t, w)
            depth = {tr.id: estimate_depth(tr, cfg, w) for tr in tracks}
            markers = marker.detect(frame) if marker else []
            proc_ms = (time.perf_counter() - t0) * 1000
            if tx is not None:
                tx.set_data("proc_ms", proc_ms)
                tx.set_data("objects", len(tracks))

        objs = {tr.id: obj_json(tr, depth, w, h) for tr in tracks}
        voices = {tid: music.voice_of(o["color"], o["shape"]) for tid, o in objs.items()}
        latest.update(frame=frame, tracks=tracks, depth=depth, gaze_px=gaze_px, objects=objs, target=sel.target_id)

        # ---- voice assistant triggers: dwell on the TALK card, or 'v'
        talk = None
        if assistant is not None:
            mic.muted = assistant.status == "speaking"
            if mic.capturing:
                focus_counts[sel.target_id] += 1
            elif assistant.status == "idle":
                focus_counts.clear()
            card = next((m for m in markers if m["id"] == ocfg["talk_marker_id"]), None)
            if card is not None and "marker" in triggers:
                if point_in(card["corners"], gaze_px):
                    talk_progress = min(1.0, talk_progress + dt / max(cfg["selector"]["dwell_s"], 0.3))
                    if talk_progress >= 1.0 and talk_armed_ok and assistant.status == "idle" and not mic.capturing:
                        mic.arm()
                        talk_armed_ok = False
                else:
                    talk_progress, talk_armed_ok = 0.0, True
                talk = (card, talk_progress)

        # ---- locks = notes. Muted while the user is talking so the note isn't recorded into the request.
        out_events = []
        for e in events:
            if mic is not None and mic.capturing:
                continue
            o = {**objs.get(e["id"], {}), **voices.get(e["id"], {})}
            e = {**e, "t": round(t, 4), "object": o}
            fb = music.on_lock(o)
            if fb is not None:
                e["lesson"] = fb
            out_events.append(e)
            pub.send(e)
            locks += 1
            flash_id, flash_until = e["id"], time.monotonic() + 0.25
            telemetry.log("lock", note=o.get("note"), instrument=o.get("instrument"), best_guess=e["best_guess"])
            print(f"[lock] #{e['id']} {o.get('color')} {o.get('shape')} -> {o.get('note')} {o.get('instrument')}"
                  f"{' (best guess)' if e['best_guess'] else ''}"
                  f"{'' if fb is None else ('  lesson ' + ('✓' if fb['correct'] else '✗ want ' + fb['expected']))}"
                  f" t={t:.2f}", flush=True)

        mstate = music.state()
        state = {
            "type": "state", "t": round(t, 4), "frame": idx,
            "gaze": None if g is None else [round(g[0], 4), round(g[1], 4)],
            "target": sel.target_id, "dwell": round(sel.progress, 3), "best_guess": sel.best_guess,
            "objects": [{**o, **voices[tid]} for tid, o in objs.items()],
            "lesson": mstate["lesson"],
            "assistant": None if assistant is None else {"status": "listening" if mic.capturing else assistant.status,
                                                         "caption": assistant.caption, **assistant.last_metrics},
            "health": {
                "fps": round(fps_ema, 1), "proc_ms": round(proc_ms, 1), "shape": backend,
                "rejected": det.rejected,
                "gaze_age_ms": gaze.age_ms() if hasattr(gaze, "age_ms") else None,
            },
        }
        if args.calib_marker:
            state["markers"] = [{k: m[k] for k in ("id", "x", "y")} for m in markers]
        pub.send(state)
        if rec:
            rec.write(frame, {"t": state["t"], "gaze": state["gaze"], "target": sel.target_id,
                              "dwell": state["dwell"], "events": out_events})

        now = time.monotonic()
        fps_ema = 0.9 * fps_ema + 0.1 * (1.0 / max(now - last_wall, 1e-6))
        last_wall = now
        want_snap = args.headless and args.snapshot_every and idx % args.snapshot_every == 0
        want_stream = stream is not None and stream.wants_frame()
        if show or want_snap or want_stream:
            hud = [f"{fps_ema:4.1f} fps  proc {proc_ms:4.1f} ms  shape:{backend}  gaze:{gaze.name}  "
                   f"objs:{len(tracks)}  rejected:{det.rejected}  locks:{locks}",
                   ("REC " if rec else "") + ("PAUSED " if paused else "") +
                   (f"depth ref {cfg['depth']['ref_distance_cm']}cm" if cfg['depth']['ref_distance_cm'] else "depth: uncalibrated (c)")
                   + (f"   lesson: {mstate['lesson']['title']} {mstate['lesson']['index']}/{len(mstate['lesson']['notes'])}"
                      if mstate["lesson"] else "")]
            next_id = None
            if mstate["lesson"]:
                next_id = next((tid for tid, v in voices.items() if v["note"] == mstate["lesson"]["next"]), None)
            caption = None
            if assistant is not None:
                caption = ("listening" if mic.capturing else assistant.status,
                           "Listening…" if mic.capturing else assistant.caption)
            img = overlay.draw(frame, tracks, depth, cfg, gaze_px, sel, hud,
                               flash_id if now < flash_until else None, markers, show_feat,
                               voices=voices, next_id=next_id, talk=talk, caption=caption, t=now)
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
            elif k == ord("v") and mic is not None:
                mic.arm()
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
