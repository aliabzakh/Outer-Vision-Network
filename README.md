# Outer Vision

World-camera half of a head-mounted gaze → music system. It finds coloured objects on a table, works out
which one the user is looking at (gaze supplied by the inner-camera process), and fires a **lock**
event after a 0.5 s dwell. The game turns that event into a note.

- Interfaces (gaze in, events out, calibration markers): **[INTERFACE.md](INTERFACE.md)**
- Known shortcuts and their proper versions: **[COMPROMISES.md](COMPROMISES.md)**
- Decisions / to-dos: **[SPEC.md](SPEC.md)**

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run
```bash
.venv/bin/python run.py --source synthetic --gaze synthetic --realtime   # no hardware needed
.venv/bin/python run.py --source 0 --gaze mouse                          # webcam; mouse = gaze
.venv/bin/python run.py --source 0 --gaze udp --record                   # real gaze from the inner camera
.venv/bin/python tools/listen.py                                         # watch lock events (2nd terminal)
.venv/bin/python -m unittest discover tests -v
```
Keys in the window: `q` quit · `m` colour masks · `f` shape features · `r` record · `c` depth reference ·
`p` pause · `s` snapshot.

## Set up on a real table (≈10 min)
1. **Colours:** run `tools/tune_colors.py --source 0`, press 1–4 to pick a colour, click that object, adjust
   until only it is white in the mask, then press `s`. Repeat whenever the lighting changes.
2. **Shapes:** in `run.py`, press `f` to see `cf` (circle fill), `rf` (rect fill), `ar` (aspect) and `v`
   (vertices) per object. Adjust the `detector` thresholds in `config.json` if a shape is misread.
3. **Depth / volume:** put every object at the same known distance, e.g. 60 cm, then run
   `run.py --ref-distance 60` and press `c`.
4. **Record** a session (`r`) and replay it while tuning, so nobody has to wear the rig:
   `run.py --source recordings/<ts>/world.mp4 --gaze replay:recordings/<ts>/log.jsonl`

## v0 objects
Matte, one solid colour each (red, yellow, green or blue), 6–10 cm (wallet-sized), no logos, ≥10 cm apart,
on a grey table.
| Shape | Good choices | Avoid |
|---|---|---|
| round | foam/stress balls, ball-pit balls, painted wooden balls | tennis balls (between yellow and green), shiny ornaments |
| square | wooden/foam toy cubes, a Post-it pad (a flat square works), a small box wrapped in coloured paper | Rubik's cube, anything with printed faces |
| cylinder | a can or Pringles tube wrapped in matte construction paper, a plastic cup; **taller than it is wide** | bare metal cans (reflections), short/squat cylinders |

## Layout
```
outer_vision/detector.py   HSV masks → contours → shape (round/square/cylinder)
outer_vision/tracker.py    stable IDs + shape voting; size-based depth → volume
outer_vision/selector.py   gaze → nearest outline (sticky) → dwell → one lock per visit
outer_vision/io.py         camera/video/synthetic sources, gaze inputs, UDP out, recorder, ArUco
outer_vision/overlay.py    debug drawing
outer_vision/synthetic.py  fake scene + fake gaze for tests and demos
run.py                     main loop
```
The code is plain Python + OpenCV with no other dependencies, so each module can be ported directly to
OpenCV C++ if the QNX target needs it.
