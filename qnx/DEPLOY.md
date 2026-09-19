# Running outer vision on the QNX Raspberry Pi 5

Unverified steps are marked **(check)**. Nobody has run this on the Pi yet.

## 1. Packages on the Pi
Everything the pipeline needs is ported on [oss.qnx.com](https://oss.qnx.com) for QNX 8.0 / aarch64:

| package | why |
|---|---|
| `python3-opencv` (4.12) | image processing, contours, ArUco |
| `python3-numpy` | arrays |
| `python3-ncnn` | **the AI module**: runs the shape CNN |
| `qnx-sf-camera-imx708`, `qnx-sf-camapi` | Camera Module 3 driver + camera API |

The site lists them as `apk` packages, so installing is probably `apk add python3-opencv python3-numpy python3-ncnn`
**(check how your image installs packages)**. Only the older Pi cameras lack a QNX driver, so use the two
Camera Module 3s here.

## 2. Camera bridge
Build on a host with QNX SDP 8.0 (see `camera_bridge/README.md`), copy `camera_bridge` to the Pi, and
start the sensor service with the Camera Module 3 config. First check: `camera_bridge -u 1 | head -c 4`
prints `OVF1`.

## 3. Run
```bash
scp -r outer_vision run.py config.json models qnxuser@qnxpi.local:/data/home/qnxuser/outer-vision/
ssh qnxuser@qnxpi.local
cd outer-vision
python3 run.py --source "pipe:camera_bridge -u 1 -w 1280 -h 720" --gaze udp --headless \
               --stream 8080 --send <mac-ip>:5006
```
On the Mac, open `http://qnxpi.local:8080/` for the live overlay, and run `tools/listen.py` (or the game)
for events. The HUD / `state.health` show fps, processing ms, frame age, dropped frames and gaze age.

## 4. If the camera bridge fights you
Fallback that keeps QNX in the loop: stream from the second (Pi OS) Pi 5's camera to the QNX Pi as a
network video stream, e.g. `--source "tcp://<pi-os-ip>:8888"`. That's weaker for the "runs on embedded
QNX" criterion, so only use it as a stopgap.
