# Outer Vision

**An instrument you play with your eyes.** Look at a coloured object on the table and it plays a note:
**colour → pitch, shape → instrument, farther → quieter**. Reconfigure it with **blinks**: a long blink
opens a spoken menu from **Maestro** (Huawei's OMNI model), and a long wink of the left eye, the right eye,
or both picks an option. OMNI looks at the table and decides the details (which instrument, which note,
which song, how much faster). No hands, no voice: built for people with ALS or paralysis, fun for anyone.

This repo is the **world-camera ("outer") half**: objects, gaze target, notes, blink menu, Maestro.
Gaze and eye state come from the inner (eye) camera over UDP.

- Interfaces (gaze + eye state in, events out): **[INTERFACE.md](INTERFACE.md)**
- Shortcuts and their proper versions: **[COMPROMISES.md](COMPROMISES.md)** · Decisions, prizes: **[SPEC.md](SPEC.md)**

## How it works
```
Camera Module 3 ─► picamera2 (Pi) ─► run.py   (or Pi ─MJPEG─► run.py on a laptop)
  1. colour   every pixel → nearest registered colour (lookup table, ~1 ms)                 WHERE
  2. shape    crop → CNN (ONNX, OpenCV) → round/square/cylinder/reject(hands)               WHAT
  3. track    stable IDs, shape vote, size → distance → volume
  4. select   gaze → nearest outline → 0.5 s dwell → LOCK = note (colour/shape → note/instrument)
  5. blinks   eye state → long blink / left wink / right wink / double blink (closed eyes freeze dwell)
  6. Maestro  blink menu → command → OMNI (camera frame + scene + recent notes) → one validated action
              → spoken reply in OMNI's voice. Offline defaults if OMNI is unreachable.
  ─► UDP events → tools/synth.py (ElevenLabs-generated instrument samples) / game   ─► MJPEG overlay (--stream)
```

### The blink menu
| Look at… and long-blink (0.6–2 s) | Maestro says | Left eye | Right eye | Both eyes |
|---|---|---|---|---|
| an object | *"Left eye, new instrument. Right eye, new note. Both eyes, swap it."* | OMNI picks a new instrument | OMNI picks a new note | look at another object + blink → swap notes |
| empty table | *"Left eye, slower. Right eye, faster. Both eyes, teach me a song."* | OMNI lengthens the look-to-play time | OMNI shortens it | OMNI picks a song from the notes on the table |

A **double blink** cancels; the menu also closes after 8 s. Natural blinks (<0.4 s) are ignored. No
notes play while a menu is open, and the object you answered on won't play until you look away and back.
Winks need per-eye lid state from the eye tracker; with a one-eye tracker every long blink counts as "both".

Why OMNI: the user can only give a coarse command ("new note for this one"), so the model has to fill
in the rest from what it **sees** (the table, the object's look, the other notes) and what the player has
been doing, then **say** what it did. Every reply is checked against the command: a "faster" command
can only make the look-to-play time shorter, never change an instrument.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover tests -v     # no hardware or API keys needed
cat > .env <<'EOF'                                  # gitignored; env variables override it
OMNI_API_KEY=...                                    # Huawei OMNI Live credits (yibuapi)
ELEVENLABS_API_KEY=...
EOF
.venv/bin/python tools/gen_audio.py                # ElevenLabs: instrument samples + menu voice clips (once)
.venv/bin/python tools/omni_check.py teach         # verifies the OMNI key, model, reply format and voice
```

## Run
```bash
.venv/bin/python tools/synth.py &                                        # sound for the notes
.venv/bin/python run.py --source synthetic --gaze synthetic --realtime   # no hardware
.venv/bin/python run.py --source 0 --gaze mouse                          # webcam, mouse = gaze, keys = blinks
.venv/bin/python run.py --source http://192.168.2.2:8081/stream --gaze udp --stream 8080
```
Keys: `1`/`2`/`3` long blink left/right/both · `x` double blink · `q` quit · `m` colour view · `f` features
· `r` record · `c` depth ref · `p` pause · `s` snapshot.
Flags: `--offline` (never call OMNI; built-in defaults), `--no-audio` (menu shown on the overlay only).
Simulate the eye tracker: `tools/send_gaze.py --at 0.4 0.6 --blink both` (then `--blink left`, …).

## Demo script (≈90 s)
1. Look at red, yellow, blue → notes play (marimba, marimba, piano).
2. Look at the green cylinder, long blink → Maestro reads the object menu. Wink left → OMNI replies with something like *"That tall
   one sounds like strings now, to go with the piano."* It now plays strings.
3. Look at the empty table, long blink, both eyes → OMNI picks a song from the notes on the table. A
   "next" ring guides your eyes and a fanfare plays at the end.
4. Long blink on the table, wink left → something like *"I'll give you a little more time on each note."* The instrument
   adapts to the player.

## On a real table (≈20 min, redo when the lighting changes)
1. **Colours:** `tools/tune_colors.py --source 0`: press 1–8, click the object, `s` to save.
2. **Shape data**, either way (or both):
   - *OMNI labels (recommended):* the real mixed table, plus hands and pens waved over it, ~3 min of
     moving your head: `tools/collect.py --source 0 --label auto`, then `tools/omni_label.py`. OMNI labels
     4×4 sheets of crops twice, shuffled, and keeps only the crops both passes agree on. The rest go to
     `data/review/` for a human look.
   - *Session labels:* one shape on the table at a time: `tools/collect.py --source 0 --label round`
     (then `square`, `cylinder`, `reject`).
3. **Train** (Mac): `.venv-train/bin/python tools/train_shape.py --real data/real`. The default is an
   ImageNet-pretrained MobileNetV3-small (96 px); `--arch tiny` is the small 64 px CNN. Train both into
   separate `--out` folders and point `config.json` → `shape_net.model_dir` at the one with the higher
   `real_val_acc` (it holds out whole object tracks, so near-duplicate crops can't inflate it). Training env: `uv venv --python 3.12 .venv-train && uv pip install --python .venv-train/bin/python -r requirements-train.txt`.
   The ONNX copy is checked against torch before it's written.
4. **Depth:** all objects at one known distance, `run.py --ref-distance 60`, press `c`.
5. **Record sessions** (`r`), including blinks, and replay them to tune without wearing the rig:
   `run.py --source recordings/<ts>/world.mp4 --gaze replay:recordings/<ts>/log.jsonl`

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
The Pi 5 has no audio jack: Maestro's voice and the notes play on the laptop (or a USB speaker).

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
outer_vision/selector.py   gaze → target → dwell → one lock per look (held while the eyes are closed)
outer_vision/blink.py      eye state → long blink / wink / double blink
outer_vision/menu.py       the blink menu (prompts, options, swap, cancel, timeout)
outer_vision/music.py      colour/shape → note/instrument, overrides, lessons, offline defaults
outer_vision/omni.py       Maestro: OMNI client (streaming text+audio), decide → check → act → speak
outer_vision/voice.py      menu prompt clips, fallback speech (ElevenLabs, then local TTS)
outer_vision/eleven.py     ElevenLabs client (text to speech, sound effects), PCM 24 kHz
outer_vision/audio.py      streaming speech player (output only: there is no microphone)
outer_vision/pitch.py      trim / pitch estimate / repitch for generated samples
outer_vision/telemetry.py  optional Sentry traces/logs
outer_vision/io.py         sources (webcam, video, synthetic, picam, MJPEG URL), gaze + blinks in, UDP out, MJPEG, recorder, ArUco
deploy/                    Pi setup: deploy_pi.sh (Mac → Pi), setup_pi.sh (on the Pi)
tools/                     synth, gen_audio, omni_check, omni_label, collect, train_shape, tune_colors,
                           pi_camera_server, listen, send_gaze, make_marker
```
