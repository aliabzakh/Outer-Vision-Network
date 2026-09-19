import assert from 'node:assert/strict';
import test from 'node:test';
import { chunk, MAX_CHUNKS, Reassembler, toLamports } from '../src/phone/framing.js';

test('chunk round trip, out of order, duplicates', () => {
  for (const n of [0, 1, 177, 178, 3000]) {
    const data = Uint8Array.from({ length: n }, (_, i) => (i * 7) % 256);
    const parts = chunk(data, 5, 180);
    assert.ok(parts.every((p) => p.length <= 180));
    const r = new Reassembler();
    const order = parts.map((_, i) => i).reverse();
    let got = null;
    for (const i of [...order, order[0]]) got = r.feed(parts[i]) || got;
    assert.deepEqual(got, data);
  }
});

test('bad chunks ignored, stale partials dropped', () => {
  let now = 0;
  const r = new Reassembler(5000, () => now);
  for (const raw of [[], [1, 0], [1, 0, 0], [1, 4, 2], [1, 0, MAX_CHUNKS + 1]]) assert.equal(r.feed(Uint8Array.from(raw)), null);
  const p = chunk(new Uint8Array(400).fill(9), 2, 100);
  r.feed(p[0]);
  now = 10_000;
  for (const q of p.slice(1)) assert.equal(r.feed(q), null);
  assert.equal(r.feed(p[0]).length, 400);
  assert.throws(() => chunk(new Uint8Array(MAX_CHUNKS * 10 + 1), 1, 13));
});

test('SOL text to lamports is exact', () => {
  assert.equal(toLamports('0.01'), 10_000_000);
  assert.equal(toLamports('1'), 1_000_000_000);
  assert.equal(toLamports(' 0.123456789 '), 123_456_789);
  assert.equal(toLamports('2.'), 2_000_000_000);
  for (const bad of ['', '-1', '0.0000000001', 'abc', '1e3', '1,5']) assert.throws(() => toLamports(bad), bad);
});
