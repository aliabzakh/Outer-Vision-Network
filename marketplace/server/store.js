// Marketplace state: songs captured by the rig (../songs/*.json) plus the registry of claimed ones.
// A JSON file, not a database: one rig, one laptop, one demo (see COMPROMISES.md).
import fs from 'node:fs';
import path from 'node:path';
import { verify } from './song.js';

export class Store {
  constructor(songsDir, registryPath) {
    this.songsDir = songsDir;
    this.registryPath = registryPath;
    this.reg = fs.existsSync(registryPath)
      ? JSON.parse(fs.readFileSync(registryPath, 'utf8'))
      : { rig: null, songs: {}, fingerprints: {}, sales: [] };
  }

  save() {
    fs.mkdirSync(path.dirname(this.registryPath), { recursive: true });
    fs.writeFileSync(this.registryPath + '.tmp', JSON.stringify(this.reg, null, 1));
    fs.renameSync(this.registryPath + '.tmp', this.registryPath);
  }

  /** Every song file the rig has written, verified, keyed by song_id. */
  captured() {
    const out = {};
    if (!fs.existsSync(this.songsDir)) return out;
    for (const f of fs.readdirSync(this.songsDir).filter((f) => f.endsWith('.json'))) {
      try {
        const song = JSON.parse(fs.readFileSync(path.join(this.songsDir, f), 'utf8'));
        out[song.song_id] = { song, valid: verify(song), file: f };
      } catch {
        /* half-written or foreign file */
      }
    }
    return out;
  }

  /**
   * Can this capture be minted? Primary factor: its fingerprint (layout + played path relative to the user)
   * must not be minted already. Secondary: among captures sharing a fingerprint, only the earliest
   * timestamp may mint, so replaying someone's arrangement later never takes their song.
   */
  mintable(entry, all = this.captured()) {
    const { song, valid } = entry;
    if (!valid) return { ok: false, reason: 'song file fails its fingerprint check (edited?)' };
    if (this.reg.songs[song.song_id]) return { ok: false, reason: 'already minted' };
    if (song.captured_at_ms > Date.now() + 60_000) return { ok: false, reason: 'capture time is in the future' };
    const taken = this.reg.fingerprints[song.fingerprint];
    if (taken) return { ok: false, reason: `duplicate: same layout and path as minted song ${taken.slice(0, 10)}`, original: taken };
    const earlier = Object.values(all).find(
      (e) => e.valid && e.song.fingerprint === song.fingerprint && e.song.captured_at_ms < song.captured_at_ms,
    );
    if (earlier) return { ok: false, reason: `duplicate: an earlier capture (${earlier.song.captured_at}) has this fingerprint`, original: earlier.song.song_id };
    return { ok: true };
  }

  record(song, fields) {
    this.reg.songs[song.song_id] = {
      song_id: song.song_id, fingerprint: song.fingerprint, captured_at_ms: song.captured_at_ms,
      captured_at: song.captured_at, notes: song.notes.length, listing: null, ...fields,
    };
    this.reg.fingerprints[song.fingerprint] = song.song_id;
    this.save();
  }

  update(songId, fields) {
    Object.assign(this.reg.songs[songId], fields);
    this.save();
  }
}
