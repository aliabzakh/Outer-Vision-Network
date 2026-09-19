# Outer Vision — Spec (living)

## Goal
Head-mounted, single-eye gaze tracker. Where you look on a table selects an object; the object plays a note.
- Colour → pitch · Shape → instrument · Distance → volume (farther = quieter)

## Decided
| Area | Decision |
|---|---|
| Rig | Head-mounted. 1 eye camera (one eye), 1 world camera, both rigid to head |
| Compute | Raspberry Pi 5 on Pi OS (192.168.2.2, direct Ethernet); a laptop can take over processing via the Pi's MJPEG camera stream |
| Targets | ~5 still, wallet-sized objects, no overlap, clean table, indoor controlled light |
| Shapes / colours | round, cylinder, square / red, yellow, green, blue (black/white dropped: shadows, glare) |
| Detection | Classical CV (colour threshold + contour shape), no VLM in the hot path |
| Selection | Continuous gaze stream; "lock" after 0.5 s dwell (tunable); low confidence → best guess |
| Calibration | Per user, short startup calibration OK; no hand or voice input during use |
| Commands | Blinks only (2026-09-19): long blink opens a spoken menu, left / right / both-eye winks pick, double blink cancels. No microphone |
| Latency | 100–200 ms target |
| Output | Location only (no naming). Live debug overlay. Record sessions (compressed) |
| Language | Python |
| Success | Passes reliably in a live demo |

| Team | 4 people, 30 h. This repo = OUTER vision only; gaze tracking is a teammate's |
| Mapping | colour → pitch, shape → instrument, farther back → quieter; note plays once per look |
| Mode | Free play |
| Framing | Assistive instrument for people who can't use their hands (ALS, paralysis): gaze is their only input, so it must not fail |
| v3 outer vision | Colour LUT (8 colours) + ShapeCNN (ONNX/OpenCV) with rules fallback; picamera2 source; MJPEG camera/overlay streams; health in state. QNX removed |
| v4 (OMNI merged) | Blink menu → Maestro on OMNI (decides details from the camera view, checked against the command, offline defaults); ElevenLabs instrument samples + menu voice; lessons; optional Sentry. Shape labels from OMNI offline, pretrained MobileNetV3-small |
| Rig | Cameras on glasses; Pi worn on the body; minimal markers (market as "works anywhere") |

## TODO / later
- [ ] **Variable distance** (v0 = one fixed distance). Needed for "farther = quieter"
- [ ] Multi-user calibration robustness; glasses wearers
- [ ] Moving targets
- [ ] Servo output (shelved in favour of music game)

## Hardware
- Raspberry Pi 5 (Pi OS) at 192.168.2.2 on a direct Ethernet cable to the Mac; 2× Camera Module 3 + 2× older Pi cameras
- Pi 5 has 2 CSI ports and **no 3.5 mm audio jack**, so sound plays on the laptop (or a USB speaker)
- Winks need both eyes' lid state: one eye camera can only report "both"
- QNX dropped (2026-09-19) for feasibility; RDK X5 dropped

## Prize targets (re-checked 2026-09-19)
| Track | Status |
|---|---|
| Finalist | primary: playful + assistive |
| Huawei OMNI Live | **built**, key in hand: Maestro decides instrument / note / dwell / song from the camera view + gaze focus + play history and speaks in OMNI's voice; OMNI also labels real shape crops offline |
| ElevenLabs | **built**: instrument samples (sound effects API) repitched per note, menu voice prompts (TTS), live TTS fallback |
| Solana ($5k) | **built (devnet)**: songs saved with a layout fingerprint (object bearings from the head) + timestamp; `marketplace/` mints them as Metaplex Core assets to the player's Privy embedded wallet, list/buy with atomic SOL-for-song swaps |
| Badge Hack ($2.5k) | on hold |
| Sentry | hooks built (`SENTRY_DSN`): frame/stage traces, lock + gesture logs, profiling. Judged on how the data changed the project, so actually use it to find and fix something |
| LeLamp / Bracket Bot | only if their hardware is free: a lamp that spotlights the object you're looking at (the original servo idea) |
| OpenAI / Baseten / Backboard | not a fit unless we route a model call through them |

## Open questions

See COMPROMISES.md for every v0 shortcut.
