"""Songs: every phrase the user plays, saved with where each object sat relative to the user.

The world camera is rigid to the head, so an object's image position is its bearing from the user's
face. A song file records, for the first note, the whole table layout (bearing + distance of every
visible object) and, for every note, the bearing of the object that played it. That is the song's
preliminary uniqueness factor:

  layout_hash  = sha256(the table layout, bearings binned by quant_deg / distances by quant_cm)
  path_hash    = sha256(the note sequence with each note's binned bearing)
  fingerprint  = sha256(layout_hash + path_hash)    same arrangement + same played path = same song

The capture timestamp is the secondary protection: song_id = sha256(fingerprint + captured_at_ms), so
two captures of the same fingerprint stay distinct, and the marketplace mints only the earliest one of
each fingerprint (the mint's on-chain block time then anchors it).

Pure logic apart from save(); run.py feeds it lock events.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

VERSION = 1


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _bin(v, step):
    """Round half up (not Python's half-to-even) so the marketplace's JS Math.round gives the same bins."""
    return None if v is None else math.floor(v / step + 0.5)


class SongRecorder:
    def __init__(self, cfg: dict, save_dir=None, clock=time.time):
        self.p = cfg["song"]
        self.dir = Path(save_dir or self.p["dir"])
        self.clock = clock                     # wall clock for captured_at (tests pass a fake one)
        self.reset()

    def reset(self):
        self.notes, self.layout, self.t0, self.last_t, self.captured_at = [], [], None, None, None

    # ------------------------------------------------------------------ geometry
    def relative(self, o: dict) -> dict:
        """Object dict (INTERFACE.md, normalized image coords) -> position relative to the user's head."""
        hf, vf = (math.radians(a) / 2 for a in self.p["fov_deg"])
        az = math.degrees(math.atan((o["x"] - 0.5) * 2 * math.tan(hf)))     # + = right of gaze-ahead
        el = math.degrees(math.atan((0.5 - o["y"]) * 2 * math.tan(vf)))     # + = above
        return {"azimuth_deg": round(az, 2), "elevation_deg": round(el, 2), "distance_cm": o.get("distance_cm")}

    def _key(self, rel: dict) -> list:
        q = self.p["quant_deg"]
        return [_bin(rel["azimuth_deg"], q), _bin(rel["elevation_deg"], q), _bin(rel["distance_cm"], self.p["quant_cm"])]

    # ------------------------------------------------------------------ recording
    def on_lock(self, obj: dict, objects: list, t: float):
        """obj: the played object (with note/instrument); objects: every visible object; t: source time."""
        if obj.get("note") is None or "x" not in obj:
            return
        if self.t0 is None:
            self.t0, self.captured_at = t, self.clock()
            self.layout = sorted(({"color": o["color"], "shape": o["shape"], **self.relative(o)} for o in objects
                                  if "x" in o), key=lambda o: (o["color"], o["shape"], o["azimuth_deg"]))
        self.last_t = t
        self.notes.append({"t": round(t - self.t0, 3), "note": obj["note"], "midi": obj.get("midi"),
                           "instrument": obj.get("instrument"), "volume": obj.get("volume"),
                           "color": obj.get("color"), "shape": obj.get("shape"), **self.relative(obj)})

    def tick(self, t: float):
        """Returns a finished song once the user has been silent for idle_s, else None."""
        if self.last_t is not None and t - self.last_t >= self.p["idle_s"]:
            return self.finish()
        return None

    def finish(self):
        """Close the current song. Returns it, or None if it was too short to keep."""
        song = self.build() if len(self.notes) >= self.p["min_notes"] else None
        self.reset()
        return song

    def build(self) -> dict:
        layout_hash = _sha([[o["color"], o["shape"], *self._key(o)] for o in self.layout])
        path_hash = _sha([[n["note"], n["instrument"], *self._key(n)] for n in self.notes])
        fingerprint = _sha([layout_hash, path_hash])
        ms = int(round(self.captured_at * 1000))
        return {
            "version": VERSION,
            "song_id": _sha([fingerprint, ms]),
            "fingerprint": fingerprint,          # primary: layout + played path relative to the user
            "layout_hash": layout_hash,
            "path_hash": path_hash,
            "captured_at_ms": ms,                # secondary: capture time (earliest wins a fingerprint)
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.captured_at)),
            "duration_s": self.notes[-1]["t"],
            "quant": {"deg": self.p["quant_deg"], "cm": self.p["quant_cm"], "fov_deg": self.p["fov_deg"]},
            "layout": self.layout,
            "notes": self.notes,
        }

    def save(self, song: dict) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{song['captured_at_ms']}_{song['song_id'][:10]}.json"
        path.write_text(json.dumps(song, indent=1) + "\n")
        return path


def verify(song: dict) -> bool:
    """Recompute every hash from the song's own layout/notes (the marketplace runs the same check)."""
    q = song["quant"]
    key = lambda r: [_bin(r["azimuth_deg"], q["deg"]), _bin(r["elevation_deg"], q["deg"]), _bin(r["distance_cm"], q["cm"])]  # noqa: E731
    layout_hash = _sha([[o["color"], o["shape"], *key(o)] for o in song["layout"]])
    path_hash = _sha([[n["note"], n["instrument"], *key(n)] for n in song["notes"]])
    fp = _sha([layout_hash, path_hash])
    return (layout_hash, path_hash, fp, _sha([fp, song["captured_at_ms"]])) == \
        (song["layout_hash"], song["path_hash"], song["fingerprint"], song["song_id"])
