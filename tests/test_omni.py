"""Voice assistant + music logic against a mock OpenAI-compatible OMNI server (no key, no network)."""
import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from outer_vision import config, omni
from outer_vision.audio import FRAME, MIC_SR, Endpointer, to_wav
from outer_vision.music import Music, midi
from outer_vision.selector import Selector

OBJECTS = {
    1: {"id": 1, "color": "red", "shape": "round"},
    4: {"id": 4, "color": "green", "shape": "cylinder"},
    5: {"id": 5, "color": "blue", "shape": "square"},
}


class MockOmni:
    """Streams SSE like Qwen-Omni: text deltas; audio deltas when modalities include audio."""

    def __init__(self, reply: dict, fail_audio=False):
        self.reply, self.fail_audio, self.requests = reply, fail_audio, []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append(body)
                audio = "audio" in body.get("modalities", [])
                if audio and mock.fail_audio:
                    self.send_response(500)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if audio:
                    pcm = (np.sin(np.arange(2400) / 10) * 8000).astype("<i2").tobytes()
                    chunks = [{"audio": {"data": base64.b64encode(pcm).decode()}}] * 3
                else:
                    txt = "```json\n" + json.dumps(mock.reply) + "\n```"
                    chunks = [{"content": txt[i:i + 20]} for i in range(0, len(txt), 20)]
                for c in chunks:
                    self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": c}]}).encode() + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


class FakePlayer:
    def __init__(self):
        self.data, self.playing = b"", False

    def feed(self, b):
        self.data += b


def make_assistant(reply, fail_audio=False, cfg=None):
    cfg = cfg or config.load(None)
    mock = MockOmni(reply, fail_audio)
    music, player, published = Music(cfg), FakePlayer(), []
    sel = Selector(cfg)
    ctx = lambda: {"jpeg": b"\xff\xd8fakejpeg", "objects": {k: {**v, **music.voice_of(v["color"], v["shape"])}
                                                            for k, v in OBJECTS.items()}, "focus": None, "selector": sel}
    client = omni.OmniClient(mock.url, "test-key", "qwen3.5-omni-flash", "Cherry")
    a = omni.Assistant(cfg, music, ctx, player, published.append, log=lambda m: None, client=client)
    return a, mock, music, player, published, sel


class TestAssistant(unittest.TestCase):
    def test_this_one_resolves_to_gaze_focus(self):
        reply = {"heard": "make this one a drum", "say": "Done, that's a drum now.", "tone": "cheerful",
                 "actions": [{"type": "set_instrument", "target": 4, "instrument": "drum"}]}
        a, mock, music, player, pub, _ = make_assistant(reply)
        a.handle(to_wav(np.zeros(16000, np.int16), MIC_SR), focus=4)
        understand = mock.requests[0]
        parts = {p["type"]: p for p in understand["messages"][-1]["content"]}
        self.assertIn("image_url", parts)
        self.assertIn("input_audio", parts)
        self.assertEqual(json.loads(parts["text"]["text"][len("SCENE "):])["focus"], 4)
        self.assertEqual(understand["modalities"], ["text"])
        self.assertEqual(music.voice_of("green", "cylinder")["instrument"], "drum")
        self.assertEqual(music.voice_of("red", "round")["instrument"], "marimba")     # untouched
        self.assertEqual(mock.requests[1]["modalities"], ["text", "audio"])           # spoken in OMNI's voice
        self.assertEqual(len(player.data), 3 * 4800)
        self.assertEqual(pub[0]["type"], "assistant")
        self.assertIsNotNone(a.last_metrics["first_audio_ms"])

    def test_voice_failure_falls_back_to_local_speech(self):
        said = []
        orig = omni.local_say
        omni.local_say = said.append
        try:
            a, *_ = make_assistant({"say": "Hello there", "actions": []}, fail_audio=True)
            a.handle(b"RIFF", focus=None)
        finally:
            omni.local_say = orig
        self.assertEqual(said, ["Hello there"])

    def test_bad_actions_are_ignored(self):
        cfg = config.load(None)
        music, sel = Music(cfg), Selector(cfg)
        objs = {k: {**v, **music.voice_of(v["color"], v["shape"])} for k, v in OBJECTS.items()}
        self.assertTrue(music.apply({"type": "set_instrument", "target": 4, "instrument": "kazoo"}, objs).startswith("ignored"))
        self.assertTrue(music.apply({"type": "set_instrument", "target": 99, "instrument": "drum"}, objs).startswith("ignored"))
        self.assertTrue(music.apply({"type": "rm -rf"}, objs).startswith("ignored"))
        music.apply({"type": "set_dwell", "seconds": 0.01}, objs, sel)
        self.assertEqual(sel.p["dwell_s"], 0.2)                                      # clamped
        self.assertEqual(music.overrides, {})

    def test_lesson_uses_only_notes_on_table(self):
        cfg = config.load(None)
        music = Music(cfg)
        objs = {k: {**v, **music.voice_of(v["color"], v["shape"])} for k, v in OBJECTS.items()}   # C4, F4, A4
        r = music.apply({"type": "start_lesson", "title": "test", "notes": ["C4", "C4", "G4", "F4", "A4"]}, objs)
        self.assertIn("skipped ['G4']", r)
        seq = [objs[1], objs[5], objs[1], objs[4], objs[5]]     # right, wrong, right, right, right
        fbs = [music.on_lock(o) for o in seq]
        self.assertEqual([f["correct"] for f in fbs], [True, False, True, True, True])
        self.assertTrue(fbs[-1]["done"])
        self.assertIsNone(music.lesson)

    def test_parse_reply_and_midi(self):
        r = omni.parse_reply('Sure!\n```json\n{"say": "hi", "actions": {"oops": 1}}\n```')
        self.assertEqual((r["say"], r["actions"], r["tone"]), ("hi", [], "calm"))
        self.assertEqual((midi("C4"), midi("A4"), midi("F#4"), midi("Bb3"), midi("H2")), (60, 69, 66, 58, None))


class TestEndpointer(unittest.TestCase):
    @staticmethod
    def frames(*segments):
        """segments: (seconds, amplitude) -> list of 30 ms int16 frames."""
        sig = np.concatenate([a * np.sin(np.arange(int(s * MIC_SR)) * 0.2) for s, a in segments]).astype(np.int16)
        return [sig[i:i + FRAME] for i in range(0, len(sig) - FRAME + 1, FRAME)]

    def run_ep(self, ep, frames):
        for f in frames:
            out = ep.feed(f)
            if out is not None:
                return out
        return None

    def test_armed_capture_ends_after_silence(self):
        ep = Endpointer()
        ep.arm()
        utt = self.run_ep(ep, self.frames((0.2, 0), (1.0, 6000), (1.2, 0)))
        self.assertIsNotNone(utt)
        self.assertAlmostEqual(len(utt) / MIC_SR, 0.2 + 1.0 + 0.8, delta=0.15)

    def test_vad_waits_for_speech(self):
        ep = Endpointer()
        ep.vad = True
        utt = self.run_ep(ep, self.frames((1.0, 30), (0.8, 6000), (1.0, 30)))
        self.assertIsNotNone(utt)
        self.assertLess(len(utt) / MIC_SR, 2.0)

    def test_armed_but_silent_gives_up(self):
        ep = Endpointer()
        ep.arm()
        self.assertIsNone(self.run_ep(ep, self.frames((5.0, 0))))
        self.assertFalse(ep.capturing)


if __name__ == "__main__":
    unittest.main()
