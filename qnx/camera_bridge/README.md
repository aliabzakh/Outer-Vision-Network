# camera_bridge (QNX)

Streams Camera Module 3 frames from the QNX Sensor Framework into `run.py` through a pipe.
**This hasn't been compiled here (it needs the QNX SDP).** It follows QNX's own `camera_example1_callback`,
so the API calls match that example.

```bash
# host, with SDP 8.0 sourced
make
scp camera_bridge qnxuser@qnxpi.local:/data/home/qnxuser/bin/

# on the Pi: the sensor service must be running (as root), per QNX's camera examples
sensor -U 521:521,1001 -b external -r /data/share/sensor -c /system/etc/config/camera_module3.conf
# (paths differ between images; check `pidin ar | grep sensor` first)

# world camera = unit 1 (the eye camera uses the other unit)
python3 run.py --source "pipe:camera_bridge -u 1 -w 1280 -h 720" --gaze udp --headless --stream 8080
```

Sanity checks if nothing appears:
- `camera_bridge -u 1 | head -c 16 | xxd`: should start with `OVF1`
- QNX's `camera-list` example prints the formats/resolutions each unit supports
