// node --test server/  — the JS fingerprint must match what outer_vision/song.py wrote.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { hashes, verify } from './song.js';
import { Store } from './store.js';

const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/song.json', import.meta.url), 'utf8'));
const clone = () => structuredClone(fixture);

test('python song verifies in JS', () => {
  assert.ok(verify(fixture));
});

test('editing a bearing or the timestamp breaks it', () => {
  const a = clone();
  a.notes[0].azimuth_deg += 9;
  assert.equal(verify(a), false);
  const b = clone();
  b.captured_at_ms -= 1000;
  assert.equal(verify(b), false);
});

test('earliest capture of a fingerprint wins, minted fingerprints are closed', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ov-'));
  const later = clone();
  later.captured_at_ms += 60_000;
  Object.assign(later, hashes(later));
  fs.writeFileSync(path.join(dir, 'a.json'), JSON.stringify(fixture));
  fs.writeFileSync(path.join(dir, 'b.json'), JSON.stringify(later));
  const store = new Store(dir, path.join(dir, 'reg', 'registry.json'));
  const all = store.captured();
  assert.equal(store.mintable(all[fixture.song_id], all).ok, true);
  assert.match(store.mintable(all[later.song_id], all).reason, /earlier capture/);
  store.record(fixture, { creator: 'x', owner: 'x', asset: 'y' });
  assert.match(store.mintable(all[fixture.song_id], all).reason, /already minted/);
  assert.match(store.mintable(all[later.song_id], all).reason, /duplicate/);
});
