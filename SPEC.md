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
| Calibration | Per user, short startup calibration OK; no input during use |
| Latency | 100–200 ms target |
| Output | Location only (no naming). Live debug overlay. Record sessions (compressed) |
| Language | Python |
| Success | Passes reliably in a live demo |

| Team | 4 people, 30 h. This repo = OUTER vision only; gaze tracking is a teammate's |
| Mapping | colour → pitch, shape → instrument, farther back → quieter; note plays once per look |
| Mode | Free play |
| Framing | Assistive instrument for people who can't use their hands (ALS, paralysis): gaze is their only input, so it must not fail |
| v2 outer vision | Colour LUT (8 colours) + ShapeCNN (ONNX/OpenCV) with rules fallback; Pi camera over MJPEG; Maestro voice assistant on OMNI; lessons; synth; optional Sentry |
| Rig | Cameras on glasses; Pi worn on the body; minimal markers (market as "works anywhere") |

## TODO / later
- [ ] **Variable distance** (v0 = one fixed distance). Needed for "farther = quieter"
- [ ] Multi-user calibration robustness; glasses wearers
- [ ] Moving targets
- [ ] Servo output (shelved in favour of music game)

## Hardware
- Raspberry Pi 5 (Pi OS) + Camera Modules on glasses → MJPEG over Wi-Fi → laptop runs vision, Maestro, audio
- QNX dropped (2026-09-19) for feasibility; RDK X5 dropped

## Prize targets (re-checked 2026-09-19)
| Track | Status |
|---|---|
| Finalist | primary: playful + assistive |
| Huawei OMNI Live | **built**: Maestro (vision + speech + language, gaze-resolved "this"). Apply for credits at https://luma.com/0fhypcu0 (200 keys, first come); taking them requires submitting to this track |
| Solana ($5k) + Badge Hack ($2.5k) | ideas in the chat thread; nothing built yet |
| Sentry | hooks built (`SENTRY_DSN`): frame/stage traces, lock + utterance logs, profiling. Judged on how the data changed the project, so actually use it to find and fix something |
| LeLamp / Bracket Bot | only if their hardware is free: a lamp that spotlights the object you're looking at (the original servo idea) |
| OpenAI / Baseten / Backboard | not a fit unless we route a model call through them |

## Open questions

See COMPROMISES.md for every v0 shortcut.
