import assert from 'node:assert/strict';
import test from 'node:test';
import nacl from 'tweetnacl';
import bs58 from 'bs58';
import { claimSigner } from './delegated.js';
import { claimMessage } from './messages.js';
import { delegationMessage } from './wallet-messages.js';

const kp = () => { const k = nacl.sign.keyPair(); return { k, a: bs58.encode(k.publicKey) }; };
const sign = (who, text) => Buffer.from(nacl.sign.detached(new TextEncoder().encode(text), who.k.secretKey)).toString('base64');
const song = { song_id: 'a'.repeat(64), fingerprint: 'b'.repeat(64), captured_at: '2026-09-19T18:00:43Z', captured_at_ms: 1789840843974 };
const NOW = 1_790_000_000_000;

function setup(over = {}) {
  const owner = kp(); const session = kp();
  const d = { owner: owner.a, session: session.a, rig: 'rig-1', actions: ['mint', 'tip'], tip_lamports: 1e7, daily_lamports: 5e7,
    contacts: [{ label: 'Mom', address: kp().a }], expires_ms: NOW + 3600_000, nonce: 'abcdef0123456789', ...over };
  const body = { session: session.a, signature: sign(session, claimMessage(song)), delegation: d, delegation_signature: sign(owner, delegationMessage(d)) };
  return { owner, session, d, body };
}
const code = (fn) => { try { fn(); return 'ok'; } catch (e) { return `${e.status} ${e.message}`; } };

test('owner-signed claim (web page) still works', () => {
  const owner = kp();
  assert.equal(claimSigner({ signature: sign(owner, claimMessage(song)) }, owner.a, song), owner.a);
  assert.match(code(() => claimSigner({ signature: sign(kp(), claimMessage(song)) }, owner.a, song)), /^401/);
});

test('valid delegated claim returns the headset key', () => {
  const { owner, session, body } = setup();
  assert.equal(claimSigner(body, owner.a, song, {}, NOW), session.a);
});

test('delegated claim refusals', () => {
  const cases = {
    'different owner': (s) => [s.body, kp().a],
    'session mismatch': (s) => [{ ...s.body, session: kp().a }, s.owner.a],
    'bad session address': (s) => [{ ...s.body, session: 'nope' }, s.owner.a],
    'tampered delegation': (s) => [{ ...s.body, delegation: { ...s.d, daily_lamports: 9e9 } }, s.owner.a],
    'delegation signed by headset': (s) => [{ ...s.body, delegation_signature: sign(s.session, delegationMessage(s.d)) }, s.owner.a],
    'claim signed by owner not headset': (s) => [{ ...s.body, signature: sign(s.owner, claimMessage(song)) }, s.owner.a],
    'claim for another song': (s) => [{ ...s.body, signature: sign(s.session, claimMessage({ ...song, song_id: 'c'.repeat(64) })) }, s.owner.a],
    'garbage signature': (s) => [{ ...s.body, signature: '!!' }, s.owner.a],
    'null delegation': (s) => [{ ...s.body, delegation: null }, s.owner.a],
    'contacts missing': (s) => [{ ...s.body, delegation: { ...s.d, contacts: undefined } }, s.owner.a],
  };
  for (const [name, make] of Object.entries(cases)) {
    const s = setup();
    const [body, owner] = make(s);
    assert.notEqual(code(() => claimSigner(body, owner, song, {}, NOW)), 'ok', name);
  }
  const tipOnly = setup({ actions: ['tip'] });
  assert.match(code(() => claimSigner(tipOnly.body, tipOnly.owner.a, song, {}, NOW)), /did not allow minting/);
  const old = setup({ expires_ms: NOW - 1 });
  assert.match(code(() => claimSigner(old.body, old.owner.a, song, {}, NOW)), /expired/);
});

test('revocation is scoped to the owner who revoked', () => {
  const s = setup();
  assert.match(code(() => claimSigner(s.body, s.owner.a, song, { [s.session.a]: { owner: s.owner.a } }, NOW)), /revoked/);
  assert.equal(claimSigner(s.body, s.owner.a, song, { [s.session.a]: { owner: kp().a } }, NOW), s.session.a);
});
