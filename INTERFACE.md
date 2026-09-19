# Interfaces between the three pieces

```
inner camera (eye)  ──gaze UDP :5005──►  outer vision (run.py)  ──events UDP :5006──►  game / audio
                    ◄── "markers" in state stream (for calibration) ──┘
```

All coordinates are **normalized world-camera image coordinates**: `x, y ∈ [0, 1]`, origin **top-left**,
measured on the *outer* camera's full frame (any resolution / crop must be undone first).

## 1. Gaze in → outer vision  (inner-camera team sends, UDP port 5005)

One JSON object per datagram, as often as you have samples (30–120 Hz):

```json
{"x": 0.512, "y": 0.430, "valid": true, "conf": 0.9, "t": 1726722000.123}
```

| field | meaning |
|---|---|
| `x`, `y` | where the user looks, already mapped into the **world** camera image |
| `valid` | `false` during blinks / pupil lost → outer vision treats it as "no gaze" (dwell survives short gaps) |
| `conf` | optional, 0–1, currently logged only |
| `t` | optional, sender clock; ignored (receive time is used) |

Samples older than `selector.gaze_max_age_s` (0.2 s) are dropped. Reference sender: `tools/send_gaze.py`.

## 2. Calibration pairing (outer vision provides)

Mapping pupil → world pixel needs world positions of calibration targets. Run
`run.py --calib-marker`; the state stream then carries every visible ArUco marker (DICT_4X4_50, print with
`tools/make_marker.py`):

```json
"markers": [{"id": 0, "x": 0.61, "y": 0.44}]
```

Suggested procedure (one marker, no clicks): the user stares at the marker while it is moved to ~9 spots
(or they move their head); pair each pupil position with the marker `x, y` from the same moment, and fit a
2nd-order polynomial pupil→world.

## 3. Events out → game / audio  (outer vision sends, UDP port 5006)

**`state`**, every frame:

```json
{"type": "state", "t": 12.34, "frame": 370, "gaze": [0.51, 0.43],
 "target": 4, "dwell": 0.62, "best_guess": false,
 "objects": [{"id": 4, "color": "green", "shape": "cylinder", "x": 0.78, "y": 0.66,
              "bbox": [0.74, 0.55, 0.08, 0.22], "distance_cm": 71.3, "volume": 0.62, "partial": false}]}
```

`state.health` = `{fps, proc_ms, shape: "onnx"|"rules", rejected, gaze_age_ms}`. Use it for a "system OK" indicator; if `gaze_age_ms` is null or large,
the gaze tracker is down.

**`lock`**, once per visit when dwell completes. **This is the "play a note" trigger**:

```json
{"type": "lock", "t": 12.71, "id": 4, "best_guess": false,
 "object": {"id": 4, "color": "green", "shape": "cylinder", "volume": 0.62, "note": "F4", "midi": 65, "instrument": "drum", ...},
 "lesson": {"title": "Mary Had a Little Lamb", "correct": true, "expected": "E4", "index": 3, "total": 7, "done": false}}
```
`note`/`midi`/`instrument` already include the user's voice overrides, so the game just plays them
(`tools/synth.py` is a reference player). `lesson` is present only during a lesson.

**`assistant`**, after each Maestro request: `{"type":"assistant","heard","say","actions","results","focus"}`
(or `{"type":"assistant","error"}`). **`music`**: `{"type":"music","overrides":{...},"lesson":{...}}` after changes.

`state` also carries `lesson` (with `next`: the note to look at) and
`assistant: {status: idle|listening|thinking|speaking, caption, understand_ms, first_audio_ms}`.

- `color` → pitch, `shape` → instrument (`round` | `square` | `cylinder`), `volume` → loudness
  (`null` until depth is calibrated; treat as 1.0).
- Default notes (rainbow order): `red` C4, `orange` D4, `yellow` E4, `green` F4, `cyan` G4, `blue` A4,
  `purple` B4, `pink` C5; instruments: round marimba, square piano, cylinder flute (`config.json` → `music`).
- To play the same object again the user must look away (> `grace_s`) and back.
- `id` is stable while the object stays in view; it can change if the object leaves view for > 0.5 s.

Reference receiver: `tools/listen.py`.
