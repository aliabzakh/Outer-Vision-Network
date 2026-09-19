# Outer Vision

World-camera half of a head-mounted, eye-played instrument. It finds coloured objects on a table, works out
which one the user is looking at (gaze comes from the inner-camera process), and fires a **lock** after a
0.5 s dwell. The game turns each lock into a note: **colour → pitch, shape → instrument, farther → quieter.**

Camera: Raspberry Pi 5 (Pi OS) with a Camera Module 3 on the glasses. The pipeline runs either on the Pi
or on a laptop fed by the Pi's camera stream. Shapes come from a small CNN (ONNX, run by OpenCV).

- Interfaces (gaze in, events out, calibration markers): **[INTERFACE.md](INTERFACE.md)**
- Getting it onto the Pi: **[Raspberry Pi](#raspberry-pi)** below
- Known shortcuts and their proper versions: **[COMPROMISES.md](COMPROMISES.md)**
- Decisions / to-dos: **[SPEC.md](SPEC.md)**

## How it works
```
Camera Module 3 ─► picamera2 (on the Pi) ─► run.py  (or Pi ─MJPEG over Ethernet/Wi-Fi─► run.py on a laptop)
   1. colour:  every pixel → nearest registered colour prototype (lookup table, ~1 ms)     WHERE
   2. shape:   crop of each blob → ShapeCNN (ONNX) → round/square/cylinder/reject     WHAT (+ drops hands)
   3. track:   stable IDs, shape majority vote, size-based distance → volume
   4. select:  gaze → nearest outline (sticky) → 0.5 s dwell → one LOCK per look
   ─► UDP events to the game/audio   ─► MJPEG debug overlay (http://<host>:8080)
```
Colour does the localisation because it's pixel-exact, deterministic and fast. The net only answers what
colour can't: which shape it is, and whether it's even an object (hands, pens and paper get rejected).
With no model file, contour-geometry rules take over, so the demo never depends on the net loading.

## Setup (Mac)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover tests -v
```

## Run
```bash
.venv/bin/python run.py --source synthetic --gaze synthetic --realtime   # no hardware
.venv/bin/python run.py --source 0 --gaze mouse                          # webcam; mouse = gaze
.venv/bin/python run.py --source 0 --gaze udp --record --stream 8080     # real gaze; overlay at :8080
.venv/bin/python tools/listen.py                                         # watch lock events
```
Keys: `q` quit · `m` colour-assignment view · `f` shape features + net scores · `r` record ·
`c` depth reference · `p` pause · `s` snapshot.

## On a real table (≈20 min, redo when the lighting changes)
1. **Colours:** `tools/tune_colors.py --source 0`. Press 1–8 for a slot, click that object. The right half
   shows what each pixel is assigned to; the table must stay black. Press `s` to save.
2. **Shape data:** put only ONE shape on the table and collect ~2 min each, moving your head
   (angles, distances): `tools/collect.py --source 0 --label round` (then `square`, `cylinder`).
   For `--label reject`, use an empty table and wave hands, pens and paper over it.
3. **Train** (Mac, ~3 min): `.venv-train/bin/python tools/train_shape.py --real data/real`. It exports
   `models/shape/*` and checks that the ONNX copy agrees with torch before writing.
4. **Depth:** all objects at one known distance → `run.py --ref-distance 60`, press `c`.
5. **Record sessions** (`r`) and replay them to tune without wearing the rig:
   `run.py --source recordings/<ts>/world.mp4 --gaze replay:recordings/<ts>/log.jsonl`

The shipped model is trained on **synthetic** images only, so step 2–3 on your real objects matters.

## Raspberry Pi
The Pi is at **192.168.2.2** on a direct Ethernet cable. The Mac needs an address on that cable too
(System Settings → Network → the USB/Thunderbolt LAN adapter → Details → TCP/IP → Manually:
IP `192.168.2.1`, mask `255.255.255.0`, no router).
```bash
ssh-copy-id <user>@192.168.2.2                   # once, so scripts can log in with a key
deploy/deploy_pi.sh <user>@192.168.2.2           # copies the code, installs deps, checks the camera
```
Then either run everything on the Pi, or stream the camera to the laptop:
```bash
ssh <user>@192.168.2.2 'cd outer-vision && .venv/bin/python run.py --source picam --gaze udp --headless --stream 8080 --send 192.168.2.1:5006'
#   overlay: http://192.168.2.2:8080/
ssh <user>@192.168.2.2 'cd outer-vision && .venv/bin/python tools/pi_camera_server.py --camera 0'
.venv/bin/python run.py --source http://192.168.2.2:8081/stream --gaze mouse     # on the laptop
```

## Objects
Matte, one solid colour each, 6–10 cm, no logos, ≥10 cm apart, on a grey table. 8 colour slots:
red, orange, yellow, green, cyan, blue, purple, pink (rainbow order = suggested C D E F G A B C').
| Shape | Good | Avoid |
|---|---|---|
| round | foam/stress balls, ball-pit balls, painted wooden balls | tennis balls, shiny ornaments |
| square | wooden/foam toy cubes, a Post-it pad, a box wrapped in coloured paper | Rubik's cube, printed faces |
| cylinder | a can or tube wrapped in matte paper, a plastic cup; taller than wide | bare metal cans |
Orange, pink and red sit near skin tones, so keep hands out of view or rely on the net's reject class.

## Layout
```
outer_vision/detector.py   colour LUT → contours → shape (net or rules)
outer_vision/shape_net.py  ONNX shape CNN via OpenCV DNN
outer_vision/tracker.py    stable IDs, shape voting, size-based depth → volume
outer_vision/selector.py   gaze → target → dwell → one lock per look
outer_vision/io.py         sources (webcam, video, synthetic, picam, MJPEG URL), gaze in, UDP out, MJPEG, recorder, ArUco
outer_vision/synthetic.py  3D-ish table renderer: tests, demos, bootstrap training data
deploy/                    Pi setup: deploy_pi.sh (Mac → Pi), setup_pi.sh (on the Pi)
tools/                     tune_colors, collect, train_shape, pi_camera_server, listen, send_gaze, make_marker
```
