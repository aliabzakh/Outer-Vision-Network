// Song fingerprint check: the same hashes outer_vision/song.py computes, recomputed from the song's own
// layout and notes, so a hand-edited song file can't claim someone else's fingerprint or timestamp.
import { createHash } from 'node:crypto';

// Python: json.dumps(obj, sort_keys=True, separators=(",", ":")) on lists of str/int/null == JSON.stringify
const sha = (obj) => createHash('sha256').update(JSON.stringify(obj)).digest('hex');
const bin = (v, step) => (v === null || v === undefined ? null : Math.floor(v / step + 0.5));

export function hashes(song) {
  const q = song.quant;
  const key = (r) => [bin(r.azimuth_deg, q.deg), bin(r.elevation_deg, q.deg), bin(r.distance_cm, q.cm)];
  const layout_hash = sha(song.layout.map((o) => [o.color, o.shape, ...key(o)]));
  const path_hash = sha(song.notes.map((n) => [n.note, n.instrument, ...key(n)]));
  const fingerprint = sha([layout_hash, path_hash]);
  return { layout_hash, path_hash, fingerprint, song_id: sha([fingerprint, song.captured_at_ms]) };
}

export function verify(song) {
  try {
    const h = hashes(song);
    return Object.entries(h).every(([k, v]) => song[k] === v);
  } catch {
    return false;
  }
}

export { claimMessage, delistMessage, pairMessage } from './messages.js';
