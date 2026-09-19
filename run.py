#!/usr/bin/env python3
"""Outer vision: world camera -> objects -> gaze target -> dwell -> note events, plus Maestro (blink menu + OMNI).

Examples:
  python run.py --source synthetic --gaze synthetic                  # no hardware at all
  python run.py --source 0 --gaze mouse                              # webcam, mouse = gaze, keys 1/2/3/x = blinks
  python run.py --source recordings/X/world.mp4 --gaze replay:recordings/X/log.jsonl
  python run.py --source http://192.168.2.2:8081/stream --gaze udp --stream 8080      # Pi camera -> laptop
  python run.py --source picam --gaze udp --headless --stream 8080                     # everything on the Pi

Keys: q quit | 1/2/3 long blink left/right/both | x double blink | m colour view | f features | r record
      c depth-calibrate | p pause | s snapshot | w wallet menu for the last song
Env (or .env): OMNI_API_KEY (Maestro's decisions + voice), ELEVENLABS_API_KEY (fallback voice), SENTRY_DSN (optional)
"""
from __future__ import annotations

import argparse
import collections
import time
from pathlib import Path

import cv2

from outer_vision import config, overlay, shape_net, telemetry
from outer_vision.detector import Detector
from outer_vision.eleven import Eleven
from outer_vision.io import CalibMarker, MjpegServer, MouseGaze, Publisher, Recorder, open_gaze, open_source
from outer_vision.menu import Menu
from outer_vision.music import Music
from outer_vision.omni import Assistant
from outer_vision.selector import Selector
from outer_vision.song import SongRecorder
from outer_vision.tracker import Tracker, estimate_depth, reference_sizes
from outer_vision.voice import Voice

WIN = "outer-vision"
KEY_BLINKS = {ord("1"): "left", ord("2"): "right", ord("3"): "both"}


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
    ap.add_argument("--headless", action="store_true", help="no window")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--snapshot-every", type=int, default=0, help="headless: save annotated frame every N frames")
    ap.add_argument("--calib-marker", action="store_true", help="report ArUco markers for gaze calibration")
    ap.add_argument("--ref-distance", type=float, default=60.0, help="cm, used by the 'c' depth calibration")
    ap.add_argument("--realtime", action="store_true", help="pace file/synthetic sources to their fps")
    ap.add_argument("--stream", type=int, default=0, help="serve the debug overlay as MJPEG on this port")
    ap.add_argument("--no-net", action="store_true", help="ignore the shape net; contour rules only")
    ap.add_argument("--offline", action="store_true", help="never call OMNI; blink commands use the built-in defaults")
    ap.add_argument("--no-audio", action="store_true", help="no speaker: menu prompts and replies are shown, not spoken")
    ap.add_argument("--no-songs", action="store_true", help="don't save played phrases to songs/ (Solana marketplace)")
    ap.add_argument("--wallet", action="store_true", help="eye wallet: the phone authorises the headset to mint/tip by blink")
    ap.add_argument("--wallet-link", default="http,ble", help="phone link transports: http, ble or both")
    ap.add_argument("--wallet-port", type=int, default=8765, help="HTTP port for the phone link")
    ap.add_argument("--rig-id", default="outer-vision", help="name the phone sees and signs for")
    ap.add_argument("--market", default="http://localhost:8787", help="marketplace server (mints songs)")
    ap.add_argument("--rpc", default="https://api.devnet.solana.com", help="Solana RPC for the eye wallet")
    args = ap.parse_args()

    config.load_env()
    cfg = config.load(args.config)
    if args.no_audio:
        cfg["omni"]["speak_with"] = "none"
    if args.no_net:
        cfg["shape_net"]["enabled"] = False
    tele = telemetry.init()
    src = open_source(args.source)
    gaze = open_gaze(args.gaze, cfg)
    net = shape_net.load(cfg)
    det, trk, sel = Detector(cfg, net), Tracker(cfg), Selector(cfg)
    music = Music(cfg)
    songs = None if args.no_songs else SongRecorder(cfg)
    backend = net.backend if net else "rules"
    host, port = (args.send.split(":") if args.send else (cfg["events"]["host"], cfg["events"]["port"]))
    pub = Publisher(host, int(port))
    rec = Recorder(fps=src.fps, cfg=cfg) if args.record else None
    stream = MjpegServer(args.stream) if args.stream else None
    marker = CalibMarker(cfg["calib_marker"]["dictionary"]) if args.calib_marker else None
    print(f"[shape] {backend}  [sentry] {'on' if tele else 'off'}", flush=True)
    if stream:
        print(f"[stream] http://0.0.0.0:{args.stream}/", flush=True)

    # ---- Maestro: blink menu -> OMNI decides (offline defaults if unreachable) -> speech. No microphone.
    log = lambda m: print(m, flush=True)  # noqa: E731
    latest = {}                                   # newest scene, read by the assistant thread
    recent = collections.deque(maxlen=12)         # notes just played, so OMNI can judge "faster"/"slower"
    player = None
    if not args.no_audio:
        try:
            from outer_vision.audio import Player
            player = Player()
        except Exception as e:                   # no output device: prompts fall back to local TTS
            log(f"[audio] no speaker ({e})")
    voice = Voice(player, Eleven(cfg), log=log)

    def context():
        s = dict(latest)
        voices = {tid: music.voice_of(o["color"], o["shape"]) for tid, o in s["objects"].items()}
        img = overlay.draw(s["frame"], s["tracks"], s["depth"], cfg, s["gaze_px"], sel, [], voices=voices)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        now = time.monotonic()
        return {"jpeg": jpg.tobytes(), "objects": {tid: {**o, **voices[tid]} for tid, o in s["objects"].items()},
                "selector": sel,
                "recent": [{**{k: v for k, v in r.items() if k != "at"}, "ago_s": round(now - r["at"], 1)} for r in recent]}

    assistant = Assistant(cfg, music, context, player, pub.send, log=log, voice=voice)
    if args.offline:
        assistant.client.key = ""

    def apply_local(action):                      # swap / stop lesson: nothing for OMNI to decide
        r = music.apply(action, latest.get("objects", {}), sel)
        log(f"[menu] {action} -> {r}")
        return r

    wallet = bridge = None
    if args.wallet:
        from outer_vision.wallet import ble, http_link
        from outer_vision.wallet.bridge import MenuBridge
        from outer_vision.wallet.policy import Rpc, Wallet
        wallet = Wallet("wallet", rpc=Rpc(args.rpc), market_url=args.market, rig_id=args.rig_id, log=log,
                        explorer_cluster="devnet" if "devnet" in args.rpc else "mainnet-beta")
        bridge = MenuBridge(wallet, log if args.no_audio else voice.say)
        links = args.wallet_link.split(",")
        # BLE forwards to the HTTP link, so it's always served (on localhost only if the phone uses BLE alone)
        http_link.serve(wallet, args.wallet_port, host="0.0.0.0" if "http" in links else "127.0.0.1")
        log(f"[wallet] phone link http://{'0.0.0.0' if 'http' in links else '127.0.0.1'}:{args.wallet_port}/wallet")
        if "ble" in links:
            ble.start(f"http://127.0.0.1:{args.wallet_port}", f"OV-{wallet.rig}", log=log)
        log(f"[wallet] rig {wallet.rig}  headset key {wallet.address}  "
            f"{'active for ' + wallet.delegation.owner if wallet.active() else 'not authorised yet'}")
    say_prompt = (lambda key, text=None: None) if args.no_audio else voice.prompt
    menu = Menu(cfg, say_prompt, assistant.submit, apply_local, wallet=bridge)
    print(f"[maestro] {'offline defaults' if not assistant.client.key else assistant.client.model + ' via ' + assistant.client.url}"
          f"  [voice] {'ElevenLabs' if voice.eleven.ok else 'local'} fallback, {len(voice.clips)} prompt clips", flush=True)

    show = not args.headless
    view_mask = show_feat = paused = False
    flash_id, flash_until = None, 0.0
    snap_dir = Path("recordings/snapshots")
    fps_ema, last_wall = 0.0, time.monotonic()
    frame = t = idx = None
    locks = 0
    key_gestures = []
    last_song = None

    def song_done(song):
        nonlocal last_song
        if song is None:
            return
        last_song = song
        path = songs.save(song)
        pub.send({"type": "song", "song_id": song["song_id"], "fingerprint": song["fingerprint"],
                  "captured_at_ms": song["captured_at_ms"], "notes": len(song["notes"]), "path": str(path)})
        print(f"[song] {len(song['notes'])} notes, fingerprint {song['fingerprint'][:12]} -> {path}", flush=True)
        menu.offer_song(song, time.monotonic())

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
        with telemetry.frame_trace(idx) as tx:
            with telemetry.span(tx, "detect", n_colors=len(cfg["colors"])):
                dets = det.detect(frame)
            with telemetry.span(tx, "track"):
                tracks = trk.update(dets, t, w)
            g = gaze.get(idx)
            gaze_px = (g[0] * w, g[1] * h) if g is not None else None
            closed = gaze.eyes_closed(idx)
            with telemetry.span(tx, "select"):
                if closed:                      # blinking: keep the target and freeze its dwell
                    sel.hold(t)
                    events = []
                else:
                    events = sel.update(tracks, gaze_px, t, w)
            depth = {tr.id: estimate_depth(tr, cfg, w) for tr in tracks}
            markers = marker.detect(frame) if marker else []
            proc_ms = (time.perf_counter() - t0) * 1000
            if tx is not None:
                tx.set_data("proc_ms", proc_ms)
                tx.set_data("objects", len(tracks))

        objs = {tr.id: obj_json(tr, depth, w, h) for tr in tracks}
        latest.update(frame=frame, tracks=tracks, depth=depth, gaze_px=gaze_px, objects=objs)

        # ---- blink gestures drive the Maestro menu (the object under gaze is what "this one" means)
        gestures = gaze.gestures(idx) + key_gestures
        key_gestures = []
        focus = sel.target_id if sel.target_id in objs else None
        was_open = menu.active
        for gs in gestures:
            log(f"[blink] {gs['kind']} {gs.get('side', '')} on {focus}  menu={menu.state}")
            telemetry.log("gesture", kind=gs["kind"], side=gs.get("side"), menu=str(menu.state))
            pub.send({"type": "gesture", **gs, "focus": focus})
            menu.on_gesture(gs, focus, music.lesson is not None, time.monotonic())
        menu.tick(time.monotonic())
        if was_open and not menu.active:
            sel.locked = True                   # don't play the object you were answering on; look away first
        voices = {tid: music.voice_of(o["color"], o["shape"]) for tid, o in objs.items()}

        # ---- locks = notes. None while a menu is open: the eyes are answering, not playing.
        out_events = []
        for e in events:
            if menu.active:
                continue
            o = {**objs.get(e["id"], {}), **voices.get(e["id"], {})}
            e = {**e, "t": round(t, 4), "object": o}
            fb = music.on_lock(o)
            if fb is not None:
                e["lesson"] = fb
            out_events.append(e)
            pub.send(e)
            locks += 1
            recent.append({"note": o.get("note"), "instrument": o.get("instrument"), "best_guess": e["best_guess"],
                           "lesson_correct": None if fb is None else fb["correct"], "at": time.monotonic()})
            flash_id, flash_until = e["id"], time.monotonic() + 0.25
            if songs:
                songs.on_lock(o, [{**v, **voices.get(k, {})} for k, v in objs.items()], t)
            telemetry.log("lock", note=o.get("note"), instrument=o.get("instrument"), best_guess=e["best_guess"])
            print(f"[lock] #{e['id']} {o.get('color')} {o.get('shape')} -> {o.get('note')} {o.get('instrument')}"
                  f"{' (best guess)' if e['best_guess'] else ''}"
                  f"{'' if fb is None else ('  lesson ' + ('✓' if fb['correct'] else '✗ want ' + fb['expected']))}"
                  f" t={t:.2f}", flush=True)

        if songs:
            song_done(songs.tick(t))
        mstate = music.state()
        state = {
            "type": "state", "t": round(t, 4), "frame": idx,
            "gaze": None if g is None else [round(g[0], 4), round(g[1], 4)],
            "eyes_closed": closed,
            "target": sel.target_id, "dwell": round(sel.progress, 3), "best_guess": sel.best_guess,
            "dwell_s": sel.p["dwell_s"],
            "objects": [{**o, **voices[tid]} for tid, o in objs.items()],
            "lesson": mstate["lesson"],
            "menu": None if not menu.active else {"state": menu.state, "focus": menu.focus, "options": menu.options()},
            "assistant": {"status": assistant.status, "caption": assistant.caption, **assistant.last_metrics},
            "wallet": None if wallet is None else {"active": wallet.active(), "rig": wallet.rig,
                                                   "remaining_today_lamports": wallet.status()["remaining_today_lamports"]},
            "health": {
                "fps": round(fps_ema, 1), "proc_ms": round(proc_ms, 1), "shape": backend,
                "rejected": det.rejected,
                "gaze_age_ms": gaze.age_ms() if hasattr(gaze, "age_ms") else None,
            },
        }
        if marker:
            state["markers"] = [{k: m[k] for k in ("id", "x", "y")} for m in markers]
        pub.send(state)
        if rec:
            rec.write(frame, {"t": state["t"], "gaze": state["gaze"], "eyes_closed": closed, "gestures": gestures,
                              "target": sel.target_id, "dwell": state["dwell"], "events": out_events})

        now = time.monotonic()
        fps_ema = 0.9 * fps_ema + 0.1 * (1.0 / max(now - last_wall, 1e-6))
        last_wall = now
        want_snap = args.headless and args.snapshot_every and idx % args.snapshot_every == 0
        want_stream = stream is not None and stream.wants_frame()
        if show or want_snap or want_stream:
            hud = [f"{fps_ema:4.1f} fps  proc {proc_ms:4.1f} ms  shape:{backend}  gaze:{gaze.name}  "
                   f"objs:{len(tracks)}  rejected:{det.rejected}  locks:{locks}  dwell {sel.p['dwell_s']:.2f}s",
                   ("REC " if rec else "") + ("PAUSED " if paused else "") + ("EYES CLOSED " if closed else "") +
                   (f"depth ref {cfg['depth']['ref_distance_cm']}cm" if cfg['depth']['ref_distance_cm'] else "depth: uncalibrated (c)")
                   + (f"   lesson: {mstate['lesson']['title']} {mstate['lesson']['index']}/{len(mstate['lesson']['notes'])}"
                      if mstate["lesson"] else "")]
            next_id = None
            if mstate["lesson"]:
                next_id = next((tid for tid, v in voices.items() if v["note"] == mstate["lesson"]["next"]), None)
            if menu.active:
                opts = menu.options()
                caption = ("menu", f"L: {opts['left']}   R: {opts['right']}   both: {opts['both']}   (double blink = cancel)")
            else:
                caption = (assistant.status, assistant.caption)
            img = overlay.draw(frame, tracks, depth, cfg, gaze_px, sel, hud,
                               flash_id if now < flash_until else None, markers, show_feat,
                               voices=voices, next_id=next_id, menu_focus=menu.focus, caption=caption, t=now)
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
            elif k in KEY_BLINKS:                  # stand-ins for blink gestures (testing without the eye tracker)
                key_gestures.append({"kind": "long", "side": KEY_BLINKS[k], "t": round(t, 3), "source": "key"})
            elif k == ord("w"):
                if last_song is None or not menu.offer_song(last_song, time.monotonic()):
                    log("[wallet] no song to offer, or the wallet isn't authorised")
            elif k == ord("x"):
                key_gestures.append({"kind": "double", "t": round(t, 3), "source": "key"})
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

    if songs:
        song_done(songs.finish())
    if rec:
        rec.close()
        print(f"[rec] saved {rec.dir}")
    if hasattr(src, "close"):
        src.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
