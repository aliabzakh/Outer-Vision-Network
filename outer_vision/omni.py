"""Maestro: the brain and voice of the instrument, on an OMNI (vision + language + speech) model.

The user PLAYS with their eyes and RECONFIGURES with blinks (outer_vision/menu.py); there is no microphone.
A blink command says WHAT kind of change ("new instrument for this one", "faster", "teach me a song");
OMNI decides the details from what it sees and what the user has been playing, and explains it aloud.
Each request:
  1. DECIDE (one call): command + camera view (objects outlined and labelled, gaze circle) + scene JSON
     (notes, instruments, dwell time, recent notes played) -> JSON {say, tone, actions}.
  2. ACT: only actions that fit the command are applied, and Music.apply() validates them. If OMNI is
     unreachable or replies with nothing usable, Music.default_action() runs instead, so a command
     always does something.
  3. SPEAK (streaming): OMNI voices `say` in the requested tone; audio plays as it arrives. If that
     fails, Voice.say() (ElevenLabs, then local TTS) says it instead.
Privacy: only a downscaled frame and the scene JSON leave the device, and only when the user blinks a command.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request

DECIDE_PROMPT = """You are Maestro, the brain and voice of an eye-played musical instrument worn by someone
who cannot use their hands or voice (for example, ALS). They play notes by LOOKING at coloured objects on
a table (colour = pitch, shape = instrument, farther away = quieter), and they give you commands by
BLINKING. A command tells you what kind of change they want; you choose the details.

You get: COMMAND (below); the current camera view with each object outlined and labelled
"#id colour shape" and the gaze point as a white circle; and SCENE JSON with each object's current note
and instrument, the look-to-play time (dwell_s), the lesson in progress, and the notes they played recently
(newest last, with how long ago and whether the look was a confident hit).

Commands and the ONE action each allows:
- change_instrument (target id): {"type":"set_instrument","target":<id>,"instrument":<one of AVAILABLE_INSTRUMENTS>}
  Pick something different from its current instrument that suits the object's look and what else is on the table.
- change_note (target id): {"type":"set_note","target":<id>,"note":"<scientific pitch like C4 or F#4>"}
  Pick a different note that fits with the other notes on the table (fill a gap in the scale, complete a chord, or
  add a note a song they tried needs).
- faster / slower: {"type":"set_dwell","seconds":<0.2-1.5>}
  Must be shorter (faster) or longer (slower) than dwell_s. Change it by 15-40%: more if recent notes came
  quickly and confidently (faster) or they hit neighbouring objects by mistake (slower).
- teach: {"type":"start_lesson","title":"<song>","notes":["C4","C4","G4",...]}
  A well-known song using ONLY notes currently on the table; simplify it if needed (and say so). 6-16 notes.

Reply with ONLY one JSON object, no prose, no code fences:
{"say": "<spoken reply: warm, at most 2 short sentences, say what you changed and why; never mention ids or JSON>",
 "tone": "cheerful|calm|encouraging|playful", "actions": [<exactly one action from above>]}
Never invent objects."""

SPEAK_PROMPT = "You are Maestro's voice. Read the user's text aloud exactly as written, in a {tone} tone. Add nothing."

ALLOWED = {"change_instrument": "set_instrument", "change_note": "set_note", "faster": "set_dwell",
           "slower": "set_dwell", "teach": "start_lesson"}


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
    try:
        r = json.loads(m.group(0))
    except ValueError as e:
        raise OmniError(f"bad JSON in reply: {e}") from None
    r.setdefault("actions", [])
    r.setdefault("say", "")
    r.setdefault("tone", "calm")
    if not isinstance(r["actions"], list):
        r["actions"] = []
    return r


def fits(command: dict, action: dict, objects: dict, music, dwell_s: float) -> bool:
    """Does OMNI's action do what the blink asked, and actually change something?"""
    kind = command.get("command")
    if not isinstance(action, dict) or action.get("type") != ALLOWED.get(kind):
        return False
    if kind in ("change_instrument", "change_note"):
        try:
            if int(action.get("target")) != command.get("target"):
                return False
        except (TypeError, ValueError):
            return False
        o = objects.get(command["target"])
        if o is None:
            return False
        cur = music.voice_of(o["color"], o["shape"])
        field = "instrument" if kind == "change_instrument" else "note"
        return str(action.get(field, "")).lower() != str(cur[field]).lower()
    if kind in ("faster", "slower"):
        try:
            s = float(action.get("seconds"))
        except (TypeError, ValueError):
            return False
        return s < dwell_s if kind == "faster" else s > dwell_s
    return True


class Assistant:
    """Runs blink commands one at a time on a worker thread. `context()` is supplied by run.py and returns
    {"jpeg": bytes, "objects": {id: obj}, "selector": Selector, "recent": [...]}."""

    def __init__(self, cfg, music, context, player=None, publish=None, log=print, client=None, voice=None):
        o = cfg["omni"]
        self.cfg, self.music, self.context, self.player, self.voice = o, music, context, player, voice
        self.publish = publish or (lambda m: None)
        self.log = log
        key = os.environ.get("OMNI_API_KEY", "")
        self.client = client or OmniClient(os.environ.get("OMNI_BASE_URL", o["base_url"]), key,
                                           os.environ.get("OMNI_MODEL", o["model"]),
                                           os.environ.get("OMNI_VOICE", o["voice"]), timeout=o["timeout_s"])
        if client is None and not key:
            self.log("[omni] OMNI_API_KEY is not set; commands will use the offline defaults")
        self.history = []                   # [(command, say)] so Maestro doesn't repeat itself
        self.status, self.caption = "idle", ""
        self.last_metrics = {}
        self._q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def busy(self):
        return self.status != "idle" or not self._q.empty()

    def submit(self, command: dict):
        self._q.put(command)

    def _worker(self):
        while True:
            command = self._q.get()
            try:
                self.handle(command)
            except Exception as e:           # never let one bad request kill the assistant
                self.log(f"[omni] error: {e}")
                self.caption = "Sorry, something went wrong."
                self.publish({"type": "assistant", "command": command, "error": str(e)[:200]})
            finally:
                self.status = "idle"

    # ------------------------------------------------------------------ one command
    def handle(self, command: dict):
        t0 = time.monotonic()
        self.status, self.caption = "thinking", "…"
        ctx = self.context()
        objects, sel = ctx["objects"], ctx.get("selector")
        dwell_s = sel.p["dwell_s"] if sel is not None else 0.5
        scene = {
            "objects": [{k: v for k, v in o.items() if k in ("id", "color", "shape", "note", "instrument", "distance_cm")}
                        for o in objects.values()],
            "dwell_s": dwell_s,
            "available_instruments": self.music.available,
            "lesson": self.music.state()["lesson"],
            "recent": ctx.get("recent", []),
        }
        messages = [{"role": "system", "content": DECIDE_PROMPT.replace(
            "AVAILABLE_INSTRUMENTS", "/".join(self.music.available))}]
        for cmd, said in self.history[-self.cfg["history_turns"]:]:
            messages += [{"role": "user", "content": "COMMAND " + json.dumps(cmd)}, {"role": "assistant", "content": said}]
        messages.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(ctx["jpeg"]).decode()}},
            {"type": "text", "text": "COMMAND " + json.dumps(command)},
            {"type": "text", "text": "SCENE " + json.dumps(scene)},
        ]})

        source, reply, actions = "omni", None, []
        try:
            if not self.client.key:
                raise OmniError("no OMNI_API_KEY")
            reply = parse_reply("".join(d for k, d in self.client.stream(messages) if k == "text"))
            actions = [a for a in reply["actions"] if fits(command, a, objects, self.music, dwell_s)][:1]
        except OmniError as e:
            self.log(f"[omni] {e}")
        t_decided = time.monotonic()
        if not actions:                       # unreachable, or nothing usable: do the obvious thing
            source = "fallback"
            action, say = self.music.default_action(command, objects, dwell_s)
            actions, reply = ([action] if action else []), {"say": say, "tone": "calm"}

        results = [self.music.apply(a, objects, sel) for a in actions]
        self.history.append((command, reply["say"]))
        self.caption = reply["say"]
        self.log(f"[omni] {command} -> {results} ({source}) | say: {reply['say']!r}")
        self.publish({"type": "assistant", "command": command, "say": reply["say"], "actions": actions,
                      "results": results, "source": source})
        self.publish({"type": "music", **self.music.state(), "dwell_s": sel.p["dwell_s"] if sel is not None else None})

        t_first_audio = self._speak(reply["say"], reply.get("tone", "calm"), use_omni=source == "omni")
        self.last_metrics = {
            "decide_ms": round((t_decided - t0) * 1000),
            "first_audio_ms": None if t_first_audio is None else round((t_first_audio - t0) * 1000),
            "source": source,
        }
        self.log(f"[omni] latency {self.last_metrics}")

    def _speak(self, text: str, tone: str, use_omni=True):
        if not text or self.cfg["speak_with"] == "none":
            return None
        self.status = "speaking"
        if use_omni and self.cfg["speak_with"] == "omni" and self.player is not None:
            first = None
            try:
                msgs = [{"role": "system", "content": SPEAK_PROMPT.format(tone=tone)}, {"role": "user", "content": text}]
                for kind, data in self.client.stream(msgs, audio_out=True):
                    if kind == "audio":
                        if first is None:
                            first = time.monotonic()
                            self.player.stop()          # cut "one moment" if it's still playing
                        self.player.feed(data)
            except OmniError as e:
                self.log(f"[omni] voice failed ({e}); using fallback speech")
            if first is not None:
                while self.player.playing:
                    time.sleep(0.05)
                return first
        t = time.monotonic()
        if self.voice is not None:
            self.voice.say(text)
        else:
            from .voice import local_say
            local_say(text)
        return t
