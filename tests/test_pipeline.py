"""Run: .venv/bin/python -m unittest discover tests -v"""
import math
import random
import unittest

import cv2
import numpy as np

from outer_vision import config, synthetic
from outer_vision.detector import Detector
from outer_vision.selector import Selector
from outer_vision.tracker import Tracker, estimate_depth, reference_sizes

CFG = config.load(None)


def random_scene(seed):
    rnd = random.Random(seed)
    objs = []
    while len(objs) < 5:
        o = (rnd.choice(list(synthetic.BGR)), rnd.choice(["round", "square", "cylinder"]),
             rnd.randint(80, 560), rnd.randint(90, 400), rnd.randint(24, 60))
        if all(math.hypot(o[2] - p[2], o[3] - p[3]) > 1.6 * (o[4] + p[4]) + 30 for p in objs):
            objs.append(o)
    return objs


class TestDetector(unittest.TestCase):
    def test_random_scenes(self):
        det = Detector(CFG)
        total = correct = missed = 0
        for seed in range(60):
            objs = random_scene(seed)
            img = synthetic.render(seed, objs, seed=seed, head_motion=False)
            dets = det.detect(img)
            for color, shape, x, y, s in objs:
                total += 1
                cands = [d for d in dets if d.color == color and math.hypot(d.cx - x, d.cy - y) < s * 1.2]
                if not cands:
                    missed += 1
                    continue
                correct += min(cands, key=lambda d: math.hypot(d.cx - x, d.cy - y)).shape == shape
        print(f"\n  synthetic shape accuracy {correct}/{total}, missed {missed}")
        self.assertEqual(missed, 0)
        self.assertGreaterEqual(correct / total, 0.95)

    def test_grey_table_is_empty(self):
        img = synthetic.render(0, objects=[])
        self.assertEqual(Detector(CFG).detect(img), [])


def track_at(tid, x, y, r=20):
    """A fake confirmed track with a circular outline."""
    c = cv2.ellipse2Poly((x, y), (r, r), 0, 0, 360, 10).reshape(-1, 1, 2).astype(np.int32)

    class D:
        contour, area, partial, bbox = c, math.pi * r * r, False, (x - r, y - r, 2 * r, 2 * r)

    class T:
        pass

    t = T()
    t.id, t.cx, t.cy, t.det, t.color, t.shape = tid, x, y, D, "red", "round"
    return t


class TestSelector(unittest.TestCase):
    W = 640

    def run_seq(self, seq, tracks, dt=1 / 30):
        """seq: list of gaze points (or None) per frame. Returns [(frame, id)] of locks."""
        s, out = Selector(CFG), []
        for i, g in enumerate(seq):
            for e in s.update(tracks, g, i * dt, self.W):
                out.append((i, e["id"]))
        return out

    def test_locks_once_after_dwell(self):
        a = track_at(1, 100, 100)
        locks = self.run_seq([(100, 100)] * 60, [a])
        self.assertEqual(locks, [(15, 1)])   # 0.5 s at 30 fps, exactly once

    def test_blink_does_not_reset(self):
        a = track_at(1, 100, 100)
        seq = [(100, 100)] * 8 + [None] * 3 + [(100, 100)] * 20   # 100 ms blink
        self.assertEqual(self.run_seq(seq, [a]), [(15, 1)])

    def test_look_away_rearms(self):
        a = track_at(1, 100, 100)
        seq = [(100, 100)] * 20 + [(400, 400)] * 10 + [(100, 100)] * 20
        self.assertEqual([i for _, i in self.run_seq(seq, [a])], [1, 1])

    def test_switch_latency_not_padded_by_grace(self):
        a, b = track_at(1, 100, 100), track_at(2, 300, 100)
        seq = [(100, 100)] * 20 + [(300, 100)] * 30
        locks = self.run_seq(seq, [a, b])
        self.assertEqual(locks[1], (35, 2))   # 15 frames after arriving at b

    def test_best_guess_and_nothing(self):
        a = track_at(1, 100, 100, r=20)
        near = self.run_seq([(100, 150)] * 20, [a])        # 30 px off the edge -> best guess
        far = self.run_seq([(500, 400)] * 20, [a])
        self.assertEqual(near, [(15, 1)])
        self.assertEqual(far, [])

    def test_edge_flicker_is_sticky(self):
        a, b = track_at(1, 100, 100), track_at(2, 150, 100)   # outlines 10 px apart
        seq = [(125, 100), (126, 100)] * 15                     # gaze wobbling in the gap
        self.assertEqual(len(self.run_seq(seq, [a, b])), 1)


class TestTrackerDepth(unittest.TestCase):
    def test_ids_stable_under_head_motion_and_depth(self):
        det, trk = Detector(CFG), Tracker(CFG)
        cfg = config.load(None)
        ids = None
        for i in range(60):
            tracks = trk.update(det.detect(synthetic.render(i)), i / 30, 640)
            if i == 10:
                ids = sorted(t.id for t in tracks)
                cfg["depth"]["ref_distance_cm"] = 60.0
                cfg["depth"]["ref_size"] = reference_sizes(tracks, 640)
        self.assertEqual(sorted(t.id for t in tracks), ids)
        for t in tracks:
            dist, vol = estimate_depth(t, cfg, 640)
            self.assertAlmostEqual(dist, 60.0, delta=6.0)


if __name__ == "__main__":
    unittest.main()
