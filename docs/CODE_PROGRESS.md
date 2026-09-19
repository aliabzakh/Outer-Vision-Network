# Outer Vision: code progress

*As of 2026-09-19, ~12:30. Covers the outer (world-camera) vision + Maestro; inner gaze tracking is a teammate's.*

## What it is
A head-mounted, eye-played instrument. The world camera finds coloured objects on a table, gaze (from
the inner camera, over UDP) picks one, and after a 0.5 s dwell it fires a **lock**, which is a note:
**colour → pitch, shape → instrument, farther → quieter.** The user reconfigures it with **blinks only**:
a long blink opens a spoken menu, a left / right / both-eye wink picks, and **Maestro** (Huawei OMNI)
decides the details from the camera view and says what it did.

```
camera ─► colour lookup (8 colours, ~1 ms)          WHERE: pixel-exact outlines
       ─► shape CNN (ONNX via OpenCV) or rules      WHAT: round / square / cylinder / reject (hands, pens)
       ─► tracker (stable IDs, shape vote, size → distance → volume)
       ─► selector (gaze → nearest outline → dwell → one lock per look; frozen while eyes are closed)
eye lids ─► blink detector (long / left wink / right wink / double) ─► blink menu ─► OMNI ─► checked action + speech
       ─► UDP events (:5006) ─► synth (ElevenLabs samples)   ─► MJPEG overlay (--stream)   ─► recordings for replay
```

## Branches
| Branch | Commit | State | Tests |
|---|---|---|---|
| `main` | local, **not pushed** (4 commits ahead of `origin/main`) | v3 + OMNI merged + blink Maestro + ElevenLabs + shape-labelling pipeline | 41 pass |
| `OMNI-branch` | `7d9b753` | merged into `main`; can be deleted | n/a |

## Version history
| Version | Commit | What changed |
|---|---|---|
| v0 | `7373e65` | Colour-range detection, contour shape rules, tracker, dwell selector, UDP events, overlay, record/replay, depth from size, ArUco calibration, synthetic scene + tests |
| v2 | `5971950` | 8 colours (nearest prototype); shape CNN with "reject" class; auto-labelled data + training; QNX camera bridge; MJPEG overlay; health stats |
| v3 | `5f324cf` | QNX removed; shape net on ONNX/OpenCV; `picam` source; Pi camera MJPEG server; Pi deploy scripts |
| merge | `3d5fcb4` | `OMNI-branch` merged: Maestro on OMNI, note/instrument mapping, lessons, `tools/synth.py`, Sentry |
| v4 | `ba60e5a` | **No microphone.** Blink detection (`blink.py`), blink menu (`menu.py`), OMNI decides from command + frame + scene + recent notes, actions checked against the command, offline defaults; selector freezes during blinks; ElevenLabs samples + menu prompts; lessons match enharmonics |
| v4.1 | `1e5d9df` | Shapes: `collect.py --label auto` → `omni_label.py` (OMNI labels 4×4 sheets, 2 shuffled passes must agree) → `train_shape.py --arch mobilenet` (pretrained MobileNetV3-small, camera-style augmentation); docs |

## Blink menu (what the user can do)
| Long blink while looking at… | Left wink | Right wink | Both eyes |
|---|---|---|---|
| an object | OMNI picks a new instrument | OMNI picks a new note | look at another object + blink → swap notes |
| empty table | OMNI makes it slower | OMNI makes it faster | OMNI picks and teaches a song (during a lesson: stop it) |

Double blink = cancel, menu times out after 8 s, no notes while it's open. Keyboard stand-ins: `1`/`2`/`3`/`x`.

## Verified
- Synthetic scenes: **300/300** shapes with the CNN and with the rules; **100%** of distractors rejected;
  grey table never detected. Also 600/600 on blurred / noisy / half-resolution synthetic scenes.
- Dwell: locks once after 0.5 s; blinks don't reset it; a 1 s closed-eye hold freezes dwell and keeps the target.
- Blinks: natural blinks ignored; long both / left / right classified; a brief squint of the other eye
  doesn't flip a wink; double blink; 3 s eye rest and 0.5 s half-blinks ignored; tracker dropout discarded.
- Menu: every path (instrument, note, swap incl. "same object" and "nothing", faster, slower, teach, stop
  lesson, cancel, timeout).
- Maestro against a mock OMNI server: no audio sent; command + scene (dwell, recent notes) reach the model;
  an action that doesn't fit the command is rejected and the offline default runs; OMNI down → default +
  fallback speech; no key → no network call.
- **End-to-end over real UDP** (`run.py` + a fake eye tracker): long blink on an object → object menu →
  left wink → instrument changed; `gesture`, `assistant`, `music` events published.
- ElevenLabs sampler: pitch estimate within 1% on harmonic notes, no octave error with a strong 2nd
  harmonic; a C4 sample repitched to A4 measures 440.0 Hz at the mixer rate.
- MobileNetV3-small exports to ONNX and runs in the runtime OpenCV 4.10 (max diff vs torch ~1e-6),
  ~1 ms/crop on the Mac vs 0.2 ms for the tiny CNN.

- **Live APIs (2026-09-19 ~13:00).** ElevenLabs: 8 instrument samples + 8 menu prompts generated (1.7 MB, in
  `assets/`). The sound effects API returns *stereo* PCM (read as mono it was an octave low and half speed),
  now mixed down; piano/flute/synth measure 261–263 Hz (middle C). OMNI (`qwen3.5-omni-flash`, voice `Serena`;
  `Cherry` is not supported): decide 1.5–3.3 s (one outlier 7.4 s), first voice audio ~1 s after that.
  Live quirks handled: actions sent as an object instead of a list, command names used as action types, errors
  inside an HTTP 200 stream. 15 of 17 live commands decided by OMNI; the rest used the offline defaults.
  "teach" picks real songs (Twinkle Twinkle), and an unplayable song is sent back once with the missing notes.
- **Live end to end:** `run.py` + a fake eye tracker + `synth.py`: long blink → spoken menu → left wink →
  OMNI switched the object to piano and said why, in its voice (first audio 3.3 s after the wink).

## Not verified yet
- **Real blinks.** Needs the eye tracker to send `left_closed` / `right_closed` (INTERFACE.md). With one
  eye camera, only "both" is possible → left/right options unreachable.
- **Real objects under real lighting**, and whether MobileNet beats the tiny CNN on them. Synthetic tests are
  saturated (the tiny CNN scores 100% even on degraded renders), so this can only be decided on real crops.
- Anything on the Pi (camera, speed, MobileNet speed on its CPU). See `docs/PI_DEBUGGING.md`.
- Speaker in the live loop.

## Next steps
1. Listen to the samples (`tools/synth.py --test`); regenerate any you don't like with
   `tools/gen_audio.py --force --only <name>`.
2. Eye-tracker teammate: send per-eye lid state; tune `blink` thresholds on their real blinks (record with `r`,
   blinks are replayed from the log).
3. Real shapes: `tools/collect.py --label auto` on the real table (≈3 min, with hands) → `tools/omni_label.py`
   → train both: `train_shape.py --real data/real --arch tiny --out models/shape_tiny` and
   `--arch mobilenet --out models/shape_mnv3`. Ship the one with the higher `real_val_acc` (validation holds
   out whole object tracks, so near-duplicate crops can't inflate it) by pointing `config.json` →
   `shape_net.model_dir` at it. Check `data/review/` for crops OMNI wasn't sure about.
4. Pi: deploy, check fps with the chosen model.
5. Push `main` once the above is checked; Solana after that.

## Prize tracks
| Track | Status |
|---|---|
| Finalist | Primary target (playful + assistive: blinks are the only input) |
| Huawei OMNI Live | Built: Maestro decides from the camera view + gaze focus + play history and speaks; OMNI also labels shape data. Key in hand, not yet run live |
| ElevenLabs | Built: generated instrument samples, menu voice, fallback TTS. Key in hand, not yet run live |
| Solana ($5k) / Badge Hack ($2.5k) | On hold until the core demo is solid |
| Sentry | Hooks built; only worth it if the traces are used to fix something |
| QNX | Dropped for feasibility (v3) |

## Where things are
| Path | Purpose |
|---|---|
| `run.py` | Main loop (sources, gaze + blinks, detection, dwell, menu, Maestro, events, overlay, recording) |
| `outer_vision/` | `detector`, `shape_net`, `tracker`, `selector`, `blink`, `menu`, `music`, `omni`, `voice`, `eleven`, `audio`, `pitch`, `io`, `overlay`, `synthetic`, `telemetry`, `config` |
| `tools/` | `synth`, `gen_audio`, `omni_check`, `omni_label`, `collect`, `train_shape`, `tune_colors`, `pi_camera_server`, `listen`, `send_gaze`, `make_marker` |
| `deploy/` | `deploy_pi.sh` (Mac → Pi), `setup_pi.sh` (on the Pi) |
| `README.md` · `INTERFACE.md` · `COMPROMISES.md` · `SPEC.md` | Usage · message formats · known shortcuts · decisions |
