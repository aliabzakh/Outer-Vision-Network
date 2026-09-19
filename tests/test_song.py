import json
import tempfile
import unittest

from outer_vision import config
from outer_vision.song import SongRecorder, verify

CFG = config.load(None)
TABLE = [
    {"color": "red", "shape": "round", "x": 0.30, "y": 0.60, "distance_cm": None, "note": "C4", "midi": 60, "instrument": "marimba"},
    {"color": "blue", "shape": "square", "x": 0.55, "y": 0.62, "distance_cm": None, "note": "A4", "midi": 69, "instrument": "piano"},
    {"color": "green", "shape": "cylinder", "x": 0.75, "y": 0.58, "distance_cm": None, "note": "F4", "midi": 65, "instrument": "flute"},
]


def play(rec, order, table=TABLE, t0=0.0):
    for i, k in enumerate(order):
        rec.on_lock(table[k], table, t0 + i)
    return rec.finish()


class SongTest(unittest.TestCase):
    def rec(self, now=1_700_000_000.0):
        return SongRecorder(CFG, save_dir=tempfile.mkdtemp(), clock=lambda: now)

    def test_bearings(self):
        r = self.rec().relative({"x": 0.5, "y": 0.5})
        self.assertEqual((r["azimuth_deg"], r["elevation_deg"]), (0.0, 0.0))
        r = self.rec().relative({"x": 1.0, "y": 0.0})
        self.assertAlmostEqual(r["azimuth_deg"], 33.0, places=1)      # half the 66 deg FOV, right
        self.assertAlmostEqual(r["elevation_deg"], 20.5, places=1)    # half the 41 deg FOV, up

    def test_same_layout_and_path_same_fingerprint_different_id(self):
        a = play(self.rec(1_700_000_000.0), [0, 1, 2, 1])
        b = play(self.rec(1_700_000_050.0), [0, 1, 2, 1], t0=100.0)
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertNotEqual(a["song_id"], b["song_id"])               # the timestamp keeps them apart
        self.assertTrue(verify(a) and verify(b))

    def test_jitter_ignored_but_moved_object_is_a_new_song(self):
        a = play(self.rec(), [0, 1, 2, 1])
        jitter = [{**o, "x": o["x"] + 0.004} for o in TABLE]
        self.assertEqual(a["fingerprint"], play(self.rec(), [0, 1, 2, 1], jitter)["fingerprint"])
        moved = [dict(TABLE[0], x=0.10), TABLE[1], TABLE[2]]
        b = play(self.rec(), [0, 1, 2, 1], moved)
        self.assertNotEqual(a["layout_hash"], b["layout_hash"])
        self.assertEqual(play(self.rec(), [0, 1, 2, 1])["path_hash"], a["path_hash"])
        self.assertNotEqual(a["path_hash"], play(self.rec(), [0, 2, 1, 1])["path_hash"])

    def test_short_phrase_dropped_and_idle_ends_song(self):
        self.assertIsNone(play(self.rec(), [0, 1]))
        r = self.rec()
        for i, k in enumerate([0, 1, 2, 0]):
            r.on_lock(TABLE[k], TABLE, float(i))
        self.assertIsNone(r.tick(5.0))
        song = r.tick(3.0 + CFG["song"]["idle_s"])
        self.assertEqual([n["note"] for n in song["notes"]], ["C4", "A4", "F4", "C4"])
        self.assertIsNone(r.tick(100.0))

    def test_tamper_detected_and_saved(self):
        r = self.rec()
        song = play(r, [0, 1, 2, 1])
        path = r.save(song)
        self.assertEqual(json.loads(path.read_text()), song)
        song["notes"][0]["azimuth_deg"] += 10
        self.assertFalse(verify(song))


if __name__ == "__main__":
    unittest.main()
