# Outer Vision

**An instrument you play with your eyes.** Look at a coloured object on the table and it plays a note:
**colour → pitch, shape → instrument, farther → quieter**. Talk to **Maestro**, the voice assistant on
Huawei's OMNI model, to reconfigure it hands-free: *"make this one a drum"*, *"teach me Mary Had a Little
Lamb"*, *"what can I play?"* Built for people who can't use their hands; fun for anyone.

This repo is the **world-camera ("outer") half**: objects, gaze target, notes, and the voice assistant.
Gaze comes from the inner (eye) camera over UDP.

- Interfaces (gaze in, events out): **[INTERFACE.md](INTERFACE.md)**
- Shortcuts and their proper versions: **[COMPROMISES.md](COMPROMISES.md)** · Decisions, prizes: **[SPEC.md](SPEC.md)**

## How it works
```
Pi camera ─MJPEG/Wi-Fi─► run.py (laptop)
  1. colour   every pixel → nearest registered colour (lookup table, ~1 ms)            WHERE
  2. shape    crop → small CNN (ONNX, OpenCV) → round/square/cylinder/reject(hands)    WHAT
  3. track    stable IDs, shape vote, size → distance → volume
  4. select   gaze → nearest outline → 0.5 s dwell → LOCK = note (colour/shape → note/instrument)
  5. Maestro  (v key, or look at the TALK card) mic → OMNI: audio + annotated frame + gaze focus
              → validated actions (instrument, notes, lessons, dwell) → reply in OMNI's own voice
  ─► UDP events → tools/synth.py (sound) / game     ─► MJPEG overlay (--stream)     ─► Sentry traces (optional)
```
Why OMNI and not a chatbot: **"this one"** is resolved from where you're LOOKING while you speak. Speech
alone can't say which object, vision alone can't hear the request, and a hands-free user can't point.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover tests -v          # 21 tests, no hardware or API key needed
export OMNI_API_KEY=...                                 # Huawei OMNI Live credits (yibuapi)
.venv/bin/python tools/omni_check.py                    # verifies the key, model and voice
```

## Run
```bash
.venv/bin/python tools/synth.py &                                        # sound for the notes
.venv/bin/python run.py --source synthetic --gaze synthetic --realtime   # no hardware
.venv/bin/python run.py --source 0 --gaze mouse --omni                   # webcam, mouse = gaze, Maestro on
.venv/bin/python run.py --source http://pi.local:8081/stream --gaze udp --omni --stream 8080
```
On the Pi: `python3 tools/pi_camera_server.py --camera 0` serves the world camera.
Keys: `v` talk · `q` quit · `m` colour view · `f` features · `r` record · `c` depth ref · `p` pause · `s` snapshot.
**Hands-free talk:** print `tools/make_marker.py --id 7` (the TALK card), put it at the table edge, and look at it.

## Demo script (≈90 s, covers all three OMNI modalities)
1. Look at red, yellow, blue → notes play (marimba, marimba, piano).
2. Look at the TALK card: *"What can I play here?"* → Maestro describes the table from the camera view.
3. Look at the green cylinder: *"Make this one a drum."* → the label changes, and it now plays a drum.
4. *"Teach me Mary Had a Little Lamb."* → Maestro fits the song to the notes on the table; a "next" ring
   guides your eyes, and a fanfare plays at the end.
5. *"That's too fast for me."* → dwell time goes up; the instrument adapts to the player.

## On a real table (≈20 min, redo when the lighting changes)
1. **Colours:** `tools/tune_colors.py --source 0`: press 1–8, click the object, `s` to save.
2. **Shape data:** one shape on the table at a time, ~2 min each, moving your head:
   `tools/collect.py --source 0 --label round` (then `square`, `cylinder`, and `reject`: wave hands/pens).
3. **Train** (~3 min): `.venv-train/bin/python tools/train_shape.py --real data/real`
   (training env: `uv venv --python 3.12 .venv-train && uv pip install --python .venv-train/bin/python -r requirements-train.txt`).
4. **Depth:** all objects at one known distance, `run.py --ref-distance 60`, press `c`.

The shipped shape model is trained on synthetic renders only, so steps 2–3 matter for real objects.

## Objects
Matte, single saturated colour, 6–10 cm, ≥10 cm apart, on a grey table. Colour slots (rainbow = scale):
red C4, orange D4, yellow E4, green F4, cyan G4, blue A4, purple B4, pink C5.
| Shape → default instrument | Good | Avoid |
|---|---|---|
| round → marimba | foam/stress balls, ball-pit balls | tennis balls, shiny ornaments |
| square → piano | wooden/foam cubes, Post-it pad, paper-wrapped box | Rubik's cube, printed faces |
| cylinder → flute | paper-wrapped can/tube, plastic cup; taller than wide | bare metal cans |

## Layout
```
outer_vision/detector.py   colour LUT → contours → shape (net or rules)
outer_vision/shape_net.py  ONNX shape CNN via OpenCV DNN
outer_vision/tracker.py    IDs, shape voting, size → distance → volume
outer_vision/selector.py   gaze → target → dwell → one lock per look
outer_vision/music.py      colour/shape → note/instrument, voice overrides, lessons (validated actions)
outer_vision/omni.py       Maestro: OMNI client (streaming text+audio), understand → act → speak
outer_vision/audio.py      mic endpointing, streaming speech player
outer_vision/telemetry.py  optional Sentry traces/logs
outer_vision/io.py         sources (webcam, video, synthetic, picam, MJPEG URL), gaze in, UDP out, MJPEG, recorder, ArUco
tools/                     synth, omni_check, pi_camera_server, tune_colors, collect, train_shape, listen, send_gaze, make_marker
```
