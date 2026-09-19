# v0 compromises → future editions

Everything we knowingly cut for the hackathon, and what "done properly" looks like.

| # | Area | v0 compromise | Future edition |
|---|---|---|---|
| 1 | Audio / portability | Sound on the laptop; Pi camera or overlay streamed as MJPEG over Ethernet/Wi-Fi (~50–120 ms on Wi-Fi) | USB speaker on the wearable, everything on the Pi |
| 2 | Distance | Gaze calibrated at one fixed working distance | Variable depth: intersect the gaze ray with the table plane, or add a depth/stereo camera |
| 3 | Depth for volume | Distance from apparent size, needs one reference capture per object type (`c` key) | Table-plane geometry or a learned monocular depth model; no reference capture |
| 4 | "Farther back on the table" | Approximated as distance from the camera, so it shifts if the user leans | Position on the table (the table itself as the reference frame) |
| 5 | Colours | **v1:** 8 saturated colours via nearest-prototype matching; black, white, brown and grey impossible (they're the table/shadows) | Learned colour/material recognition |
| 6 | Pitches / instruments | **v1:** 8 colours = one octave; 3 shapes = 3 instruments | Position → octave, chords, more colours |
| 7 | Detection method | **v1:** colour finds objects; a CNN (ONNX) classifies shape and rejects hands. Still needs saturated objects on a grey table | Full learned detector for arbitrary objects in any setting |
| 8 | Shapes | **v1:** CNN trained on synthetic renders only until real crops are collected; rules as fallback | Train on real crops from the venue (tools/collect.py) |
| 9 | Motion | Still objects only; the tracker assumes small movement between frames | Motion model / proper multi-object tracker |
| 10 | Lighting | Camera auto-exposure and auto white balance; indoor only | Locked exposure/WB and auto colour recalibration |
| 11 | Eye | One eye only | Both eyes for convergence depth and robustness |
| 12 | Calibration | Per-user, per-session, uses a printed ArUco marker | Marker-free calibration off the objects themselves, plus continuous drift correction |
| 13 | Glasses | Users who wear glasses are not supported | Eye-camera placement / IR that works through or around lenses |
| 14 | Musicality | One note per look, one note at a time, 0.5 s dwell, so no real rhythm | Onset on fixation (~150 ms), sustained notes, tempo/quantisation, chords |
| 15 | Game | Free play only | Song mode with guidance and scoring |
| 16 | Hands | **v1:** CNN "reject" class drops hands, pens and paper (100% on synthetic; unproven on real) | Train reject class on real hands |
| 17 | Edges | Objects cut off by the frame edge are flagged `partial` and get no depth | Track through the edge; wider lens |
| 18 | Sync | Eye and world cameras are unsynchronised; gaze is paired with the newest frame (≤ 0.2 s old) | Hardware trigger or timestamp alignment |
| 19 | Transport | UDP JSON with no delivery guarantee | A reliable channel for lock events (they're rare) |
| 20 | Language | Python + OpenCV | Port hot paths only if the Pi can't keep up |
| 21 | Markers | ArUco marker only for calibration, to stay "works anywhere" | None at all |
| 22 | QNX | Dropped for feasibility (v3); standard Raspberry Pi OS instead | n/a |
| 23 | Reliability | Health numbers are reported, but nothing restarts a stalled process | systemd service with restart + heartbeat |
