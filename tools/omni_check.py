#!/usr/bin/env python3
"""One-shot check of the OMNI API before the demo: sends the synthetic table + a question, prints the
parsed reply, and speaks it in the model's voice.

  export OMNI_API_KEY=...        # from the Huawei OMNI Live credits (yibuapi)
  python tools/omni_check.py "what can I play here?"
  python tools/omni_check.py --wav question.wav        # test with a real spoken question
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outer_vision import config, omni, synthetic  # noqa: E402
from outer_vision.music import Music  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("text", nargs="?", default="What can I play here, and what does the green one sound like?")
ap.add_argument("--wav", help="spoken question instead of text")
ap.add_argument("--no-voice", action="store_true")
args = ap.parse_args()

cfg = config.load("config.json")
o = cfg["omni"]
if not os.environ.get("OMNI_API_KEY"):
    sys.exit("set OMNI_API_KEY first")
client = omni.OmniClient(os.environ.get("OMNI_BASE_URL", o["base_url"]), os.environ["OMNI_API_KEY"],
                         os.environ.get("OMNI_MODEL", o["model"]), os.environ.get("OMNI_VOICE", o["voice"]))
music = Music(cfg)
img = synthetic.render(0)
objs = {i + 1: {"id": i + 1, "color": c, "shape": s, **music.voice_of(c, s)} for i, (c, s, *_) in enumerate(synthetic.DEFAULT_OBJECTS)}
jpg = cv2.imencode(".jpg", img)[1].tobytes()
content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}}]
if args.wav:
    content.append({"type": "input_audio", "input_audio": {"data": "data:;base64," + base64.b64encode(Path(args.wav).read_bytes()).decode(), "format": "wav"}})
else:
    content.append({"type": "text", "text": "USER SAID: " + args.text})
content.append({"type": "text", "text": "SCENE " + json.dumps({"objects": list(objs.values()), "focus": 4,
                                                               "available_instruments": music.available})})
msgs = [{"role": "system", "content": omni.UNDERSTAND_PROMPT.replace("AVAILABLE_INSTRUMENTS", "/".join(music.available))},
        {"role": "user", "content": content}]
t0 = time.monotonic()
text = "".join(d for k, d in client.stream(msgs) if k == "text")
print(f"understand: {time.monotonic() - t0:.2f}s\n{text}\n")
reply = omni.parse_reply(text)
for a in reply["actions"]:
    print("action:", a, "->", music.apply(a, objs))
if not args.no_voice:
    from outer_vision.audio import Player
    p = Player()
    t1, first = time.monotonic(), None
    msgs = [{"role": "system", "content": omni.SPEAK_PROMPT.format(tone=reply["tone"])}, {"role": "user", "content": reply["say"]}]
    for kind, data in client.stream(msgs, audio_out=True):
        if kind == "audio":
            first = first or time.monotonic()
            p.feed(data)
    print(f"voice: first audio after {None if first is None else round(first - t1, 2)}s")
    while p.playing:
        time.sleep(0.05)
