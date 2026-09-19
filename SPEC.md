# Outer Vision — Spec (living)

## Goal
Head-mounted, single-eye gaze tracker. Where you look on a table selects an object; the object plays a note.
- Colour → pitch · Shape → instrument · Distance → volume (farther = quieter)

## Decided
| Area | Decision |
|---|---|
| Rig | Head-mounted. 1 eye camera (one eye), 1 world camera, both rigid to head |
| Compute | Raspberry Pi on QNX (going for the QNX prize) + RDK X5 (going for its track); Mac for v0 display/audio |
| Targets | ~5 still, wallet-sized objects, no overlap, clean table, indoor controlled light |
| Shapes / colours | round, cylinder, square / red, yellow, green, blue (black/white dropped: shadows, glare) |
| Detection | Classical CV (colour threshold + contour shape), no VLM in the hot path |
| Selection | Continuous gaze stream; "lock" after 0.5 s dwell (tunable); low confidence → best guess |
| Calibration | Per user, short startup calibration OK; no input during use |
| Latency | 100–200 ms target |
| Output | Location only (no naming). Live debug overlay. Record sessions (compressed) |
| Language | Python first; port hot paths to C/C++ if QNX needs it |
| Success | Passes reliably in a live demo |

| Team | 4 people, 30 h. This repo = OUTER vision only; gaze tracking is a teammate's |
| Mapping | colour → pitch, shape → instrument, farther back → quieter; note plays once per look |
| Mode | Free play |
| Rig | Cameras on glasses; Pi worn on the body; minimal markers (market as "works anywhere") |

## TODO / later
- [ ] **Variable distance** (v0 = one fixed distance). Needed for "farther = quieter"
- [ ] Multi-user calibration robustness; glasses wearers
- [ ] Moving targets
- [ ] Servo output (shelved in favour of music game)

## Open questions
- Pi model + exact cameras (screenshots didn't arrive): 1 vs 2 CSI ports, QNX camera driver support
- Which board runs outer vision: QNX Pi or RDK X5?

See COMPROMISES.md for every v0 shortcut.
