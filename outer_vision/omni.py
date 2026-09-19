"""Maestro: the voice of the instrument, on an OMNI (vision + audio + language) model.

The user PLAYS with their eyes and TALKS to reconfigure: "make this one a drum", "teach me Twinkle
Twinkle", "what can I play?". Each request:
  1. UNDERSTAND (one call): user's audio + the camera view (objects outlined and labelled) + scene JSON
     + which object they were looking at while speaking -> JSON {heard, say, tone, actions}.
     Gaze resolves "this"/"that one", which neither vision nor speech can do alone.
  2. ACT: actions are validated by Music.apply(); invalid ones are dropped, never half-applied.
  3. SPEAK (streaming): the model voices `say` in the requested tone; audio plays as it arrives.
     If the voice call fails, a local TTS says it instead, so the user always gets an answer.
Privacy: nothing leaves the device unless the user triggers a request; frames are downscaled JPEGs.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

UNDERSTAND_PROMPT = """You are Maestro, the voice of an eye-played musical instrument worn by someone who
may not be able to use their hands. They play notes by LOOKING at coloured objects on a table
(colour = pitch, shape = instrument, farther away = quieter). They talk to you to change the setup,
learn songs, or ask about what they see.

You get: the user's spoken request (audio); the current camera view with each object outlined and
labelled "#id colour shape" and the gaze point as a white circle; JSON describing each object's
current note and instrument; and FOCUS, the object the user was looking at while speaking.
"this", "that", "it", "this one" mean the FOCUS object unless the user names another.

Reply with ONLY one JSON object, no prose, no code fences:
{"heard": "<what the user said>", "say": "<spoken reply: warm, at most 2 short sentences, never mention ids or JSON>",
 "tone": "cheerful|calm|encouraging|playful", "actions": [<zero or more actions>]}

Actions (use object ids from the JSON; target may be "all"):
{"type":"set_instrument","target":<id>,"instrument":<one of AVAILABLE_INSTRUMENTS>}
{"type":"set_note","target":<id>,"note":"<scientific pitch like C4 or F#4>"}
{"type":"swap_notes","a":<id>,"b":<id>}
{"type":"start_lesson","title":"<song>","notes":["C4","C4","G4",...]}   (only notes currently on the table; simplify the song if needed and say so)
{"type":"stop_lesson"}
{"type":"set_dwell","seconds":<0.2-1.5>}   (how long a look must last to play; "faster" = shorter)
{"type":"reset"}

Never invent objects. If the request is unclear, ask one short question in "say" and use no actions."""

SPEAK_PROMPT = "You are Maestro's voice. Read the user's text aloud exactly as written, in a {tone} tone. Add nothing."


class OmniError(RuntimeError):
    pass


class OmniClient:
    """Minimal streaming client for OpenAI-compatible /chat/completions with Qwen-Omni audio output."""

    def __init__(self, base_url, api_key, model, voice, timeout=30):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key, self.model, self.voice, self.timeout = api_key, model, voice, timeout

    def stream(self, messages, audio_out=False):
        """Yields ("text", str) and ("audio", pcm16 24 kHz bytes) as they arrive."""
        body = {"model": self.model, "messages": messages, "stream": True,
                "modalities": ["text", "audio"] if audio_out else ["text"]}
        if audio_out:
            body["audio"] = {"voice": self.voice, "format": "wav"}
        req = urllib.request.Request(self.url, json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise OmniError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise OmniError(f"network: {e}") from None
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        yield "text", delta["content"]
                    audio = delta.get("audio") or {}
                    if audio.get("data"):
                        yield "audio", base64.b64decode(audio["data"])


def parse_reply(text: str) -> dict:
    """Pull the JSON object out of the model's text (tolerates code fences / stray prose)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise OmniError(f"no JSON in reply: {text[:200]!r}")
    r = json.loads(m.group(0))
    r.setdefault("actions", [])
    r.setdefault("say", "")
    r.setdefault("tone", "calm")
    if not isinstance(r["actions"], list):
        r["actions"] = []
    return r


def local_say(text: str):
    """Fallback voice so the user is never left without an answer."""
    for cmd in (["say", text], ["espeak", text]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, check=False)
            return


class Assistant:
    """Runs requests one at a time on a worker thread. `context()` is supplied by run.py and returns
    {"jpeg": bytes, "objects": {id: obj}, "focus": id|None, "selector": Selector}."""

    def __init__(self, cfg, music, context, player=None, publish=None, log=print, client=None):
        o = cfg["omni"]
        self.cfg, self.music, self.context, self.player = o, music, context, player
        self.publish = publish or (lambda m: None)
        self.log = log
        key = os.environ.get("OMNI_API_KEY", "")
        self.client = client or OmniClient(os.environ.get("OMNI_BASE_URL", o["base_url"]), key,
                                           os.environ.get("OMNI_MODEL", o["model"]),
                                           os.environ.get("OMNI_VOICE", o["voice"]))
        if client is None and not key:
            self.log("[omni] OMNI_API_KEY is not set; requests will fail and fall back to local speech")
        self.history = []                   # [(heard, say)] for multi-turn follow-ups
        self.status, self.caption = "idle", ""
        self.last_metrics = {}
        self._q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, wav: bytes, focus=None):
        self._q.put((wav, focus))

    def _worker(self):
        while True:
            wav, focus = self._q.get()
            try:
                self.handle(wav, focus)
            except Exception as e:           # never let one bad request kill the assistant
                self.log(f"[omni] error: {e}")
                self.caption = "Sorry, I couldn't reach Maestro."
                self.publish({"type": "assistant", "error": str(e)[:200]})
                self._speak_local(self.caption)
            finally:
                self.status = "idle"

    # ------------------------------------------------------------------ one request
    def handle(self, wav: bytes, focus=None):
        t0 = time.monotonic()
        self.status, self.caption = "thinking", "…"
        ctx = self.context()
        focus = focus if focus is not None else ctx.get("focus")
        objects = ctx["objects"]
        scene = {
            "objects": [{k: v for k, v in o.items() if k in ("id", "color", "shape", "note", "instrument", "distance_cm")}
                        for o in objects.values()],
            "focus": focus,
            "available_instruments": self.music.available,
            "lesson": self.music.state()["lesson"],
        }
        user = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(ctx["jpeg"]).decode()}},
            {"type": "input_audio", "input_audio": {"data": "data:;base64," + base64.b64encode(wav).decode(), "format": "wav"}},
            {"type": "text", "text": "SCENE " + json.dumps(scene)},
        ]
        messages = [{"role": "system", "content": UNDERSTAND_PROMPT.replace(
            "AVAILABLE_INSTRUMENTS", "/".join(self.music.available))}]
        for heard, said in self.history[-self.cfg["history_turns"]:]:
            messages += [{"role": "user", "content": heard}, {"role": "assistant", "content": said}]
        messages.append({"role": "user", "content": user})
        text = "".join(d for k, d in self.client.stream(messages) if k == "text")
        t_understood = time.monotonic()
        reply = parse_reply(text)

        results = [self.music.apply(a, objects, ctx.get("selector")) for a in reply["actions"] if isinstance(a, dict)]
        self.history.append((reply.get("heard", ""), reply["say"]))
        self.caption = reply["say"]
        self.log(f"[omni] heard: {reply.get('heard')!r} | say: {reply['say']!r} | actions: {results}")
        self.publish({"type": "assistant", "heard": reply.get("heard"), "say": reply["say"],
                      "actions": reply["actions"], "results": results, "focus": focus})
        self.publish({"type": "music", **self.music.state()})

        t_first_audio = self._speak(reply["say"], reply["tone"])
        self.last_metrics = {
            "understand_ms": round((t_understood - t0) * 1000),
            "first_audio_ms": None if t_first_audio is None else round((t_first_audio - t0) * 1000),
        }
        self.log(f"[omni] latency {self.last_metrics}")

    def _speak(self, text: str, tone: str):
        if not text or self.cfg["speak_with"] == "none":
            return None
        if self.cfg["speak_with"] == "local" or self.player is None:
            return self._speak_local(text)
        self.status = "speaking"
        first = None
        try:
            msgs = [{"role": "system", "content": SPEAK_PROMPT.format(tone=tone)}, {"role": "user", "content": text}]
            for kind, data in self.client.stream(msgs, audio_out=True):
                if kind == "audio":
                    first = first or time.monotonic()
                    self.player.feed(data)
        except OmniError as e:
            self.log(f"[omni] voice failed ({e}); using local speech")
        if first is None:
            return self._speak_local(text)
        while self.player.playing:
            time.sleep(0.05)
        return first

    def _speak_local(self, text):
        self.status = "speaking"
        t = time.monotonic()
        local_say(text)
        return t
