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
| 8 | Shapes | CNN trained on synthetic renders only until real crops are collected; rules as fallback. Real crops can now be labelled by OMNI offline (two shuffled passes must agree), and the default model is a pretrained MobileNetV3-small | Human-checked labels from many venues and lighting conditions; an active-learning loop that sends only the crops the model is unsure about |
| 9 | Motion | Still objects only; the tracker assumes small movement between frames | Motion model / proper multi-object tracker |
| 10 | Lighting | Camera auto-exposure and auto white balance; indoor only | Locked exposure/WB and auto colour recalibration |
| 11 | Eye | One eye camera; blink winks (left vs right) need lid state for both eyes, so with one camera every long blink counts as "both" | Both eyes for winks, convergence depth and robustness |
| 12 | Calibration | Per-user, per-session, uses a printed ArUco marker | Marker-free calibration off the objects themselves, plus continuous drift correction |
| 13 | Glasses | Users who wear glasses are not supported | Eye-camera placement / IR that works through or around lenses |
| 14 | Musicality | One note per look, one note at a time, 0.5 s dwell, so no real rhythm | Onset on fixation (~150 ms), sustained notes, tempo/quantisation, chords |
| 15 | Game | Free play only | Song mode with guidance and scoring |
| 16 | Hands | **v1:** CNN "reject" class drops hands, pens and paper (100% on synthetic; unproven on real) | Train reject class on real hands |
| 17 | Edges | Objects cut off by the frame edge are flagged `partial` and get no depth | Track through the edge; wider lens |
| 18 | Sync | Eye and world cameras are unsynchronised; gaze is paired with the newest frame (≤ 0.2 s old) | Hardware trigger or timestamp alignment |
| 19 | Transport | UDP JSON with no delivery guarantee | A reliable channel for lock events (they're rare) |
| 20 | Language | Python + OpenCV | Fine for a laptop; port hot paths if it moves onto the wearable |
| 21 | Markers | ArUco marker only for calibration, to stay "works anywhere" | None at all |
| 22 | QNX | Dropped for feasibility; standard Pi OS + laptop instead | n/a |
| 23 | Maestro latency | Two calls (decide, then speak) so the voice matches the checked action: ~1–3 s; a pre-generated "One moment" covers the gap | One streaming call with tool-calling, or the OMNI realtime API |
| 24 | Commands | Blink menu with 3 options per menu (left / right / both), two menus picked by where you look; fixed blink thresholds for everyone | Per-user blink calibration; deeper menus or dwell-scanning for more commands |
| 25 | Privacy | No microphone at all. A downscaled frame + scene JSON leave the device only when the user blinks a command; faces aren't blurred yet | On-device blur of faces/people before upload |
| 26 | Assistant memory | Last 6 commands only, lost on restart | Per-user profile (preferred dwell, instruments, songs learned) |
| 27 | Reliability | Health numbers are reported, but nothing restarts a stalled process | Supervisor process + heartbeat |
| 28 | Voices | Menu prompts in an ElevenLabs voice (pre-generated clips), OMNI's replies in OMNI's voice: two different voices | Clone one voice for both, or have OMNI pre-render the prompts |
| 29 | Instrument sound | One ElevenLabs sample per instrument, repitched by resampling (notes far from middle C sound shorter/brighter or longer/duller) | A sample per octave, or a proper sampler with time-stretching |
| 30 | Song uniqueness | Fingerprint = table layout + played path as bearings from the head camera, binned to 3° / 10 cm. Jitter right on a bin edge can split one arrangement into two fingerprints; turning the head between takes changes the layout. Timestamp is only the local laptop clock until the mint's block time anchors it | Positions in the table's own frame (not the head's), fuzzy matching instead of exact bins, a signed timestamp from the rig |
| 31 | Song ownership | Whoever pairs the rig (one wallet per rig) is the creator of every song it records | Per-session pairing, e.g. the player blinks to confirm a QR shown by the web page |
| 32 | Marketplace trust | Listed songs sit in the marketplace's escrow wallet; the server co-signs sales (payment and transfer are still one atomic transaction). Registry is a JSON file; metadata and art are served by the laptop | An on-chain escrow program (Anchor) or Metaplex Core's delegate plugins, metadata on Arweave/Irys, a real database |
| 33 | Network | Solana devnet; a devnet "faucet" button tops up new wallets from the marketplace's balance | Mainnet with real prices; no faucet |
