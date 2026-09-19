// Outer Vision song marketplace API. Run: npm run server (see marketplace/README.md).
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import nacl from 'tweetnacl';
import bs58 from 'bs58';
import { LAMPORTS_PER_SOL, PublicKey, VersionedTransaction } from '@solana/web3.js';
import { claimMessage, delistMessage, pairMessage } from './song.js';
import { Store } from './store.js';
import { Chain } from './solana.js';
import { layoutSvg } from './art.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const env = process.env;
const PORT = Number(env.PORT || 8787);
const PUBLIC_URL = env.PUBLIC_URL || `http://localhost:${PORT}`;   // metadata URIs point here
const store = new Store(path.resolve(root, env.SONGS_DIR || '../songs'), path.resolve(root, 'data/registry.json'));
const chain = new Chain(env.SOLANA_RPC || 'https://api.devnet.solana.com', path.resolve(root, env.MARKET_KEYPAIR || '.keys/marketplace.json'));

const app = express();
app.use(express.json({ limit: '256kb' }));
app.use('/samples', express.static(path.resolve(root, '../assets/samples')));

const wrap = (fn) => (req, res) => Promise.resolve(fn(req, res)).catch((e) => {
  console.error('[api]', req.path, e.message);
  res.status(e.status || 500).json({ error: e.message });
});
const fail = (status, message) => Object.assign(new Error(message), { status });

function checkAddr(a) {
  try { return new PublicKey(a).toBase58(); } catch { throw fail(400, 'bad wallet address'); }
}
function checkSig(message, signatureB64, wallet) {
  const ok = nacl.sign.detached.verify(new TextEncoder().encode(message), Buffer.from(signatureB64 || '', 'base64'),
    bs58.decode(wallet));
  if (!ok) throw fail(401, 'wallet signature does not match');
}

// ---------------------------------------------------------------- one-time nonces for signed requests
const nonces = new Map();
function takeNonce(n) {
  const exp = nonces.get(n);
  nonces.delete(n);
  if (!exp || exp < Date.now()) throw fail(401, 'expired nonce, try again');
}
app.get('/api/nonce', (req, res) => {
  const n = crypto.randomBytes(12).toString('hex');
  nonces.set(n, Date.now() + 5 * 60_000);
  res.json({ nonce: n });
});

// ---------------------------------------------------------------- reads
function summary(e, all) {
  const { song } = e;
  const minted = store.reg.songs[song.song_id];
  return {
    song_id: song.song_id, fingerprint: song.fingerprint, layout_hash: song.layout_hash,
    captured_at: song.captured_at, captured_at_ms: song.captured_at_ms, duration_s: song.duration_s,
    notes: song.notes.length, objects: song.layout.length, valid: e.valid,
    minted: minted || null, mint_check: minted ? null : store.mintable(e, all),
  };
}

app.get('/api/state', wrap(async (req, res) => {
  const all = store.captured();
  const minted = Object.values(store.reg.songs).map((m) => ({
    ...m, asset_url: chain.explorer('address', m.asset),
    ...(all[m.song_id] ? {} : { missing_file: true }),
  }));
  res.json({
    cluster: chain.cluster, escrow: chain.escrow.toBase58(), rig: store.reg.rig,
    captured: Object.values(all).filter((e) => !store.reg.songs[e.song.song_id]).map((e) => summary(e, all))
      .sort((a, b) => b.captured_at_ms - a.captured_at_ms),
    minted: minted.sort((a, b) => b.captured_at_ms - a.captured_at_ms),
    sales: store.reg.sales.slice(-20).reverse(),
  });
}));

function songFile(id) {
  const e = store.captured()[id];
  if (!e) throw fail(404, 'no such song on this rig');
  return e;
}
app.get('/api/song/:id', wrap(async (req, res) => res.json(songFile(req.params.id).song)));
app.get('/api/art/:id.svg', wrap(async (req, res) => { const svg = layoutSvg(songFile(req.params.id).song); res.type('image/svg+xml').send(svg); }));
app.get('/api/metadata/:id.json', wrap(async (req, res) => {
  const { song } = songFile(req.params.id);
  const m = store.reg.songs[song.song_id];
  res.json({
    name: `Eye Song ${song.song_id.slice(0, 6)}`,
    description: 'A song played with the eyes on Outer Vision: each note is an object on the table, chosen by gaze. '
      + 'Its fingerprint is the table layout and the played path, measured from the player\'s head.',
    image: `${PUBLIC_URL}/api/art/${song.song_id}.svg`,
    external_url: `${PUBLIC_URL}/api/song/${song.song_id}`,
    attributes: [
      { trait_type: 'notes', value: song.notes.length },
      { trait_type: 'objects on table', value: song.layout.length },
      { trait_type: 'duration_s', value: song.duration_s },
      { trait_type: 'instruments', value: [...new Set(song.notes.map((n) => n.instrument))].join(', ') },
      { trait_type: 'captured_at', value: song.captured_at },
    ],
    properties: { fingerprint: song.fingerprint, creator: m?.creator ?? null, category: 'audio' },
  });
}));
app.get('/api/wallet/:addr', wrap(async (req, res) => res.json({ sol: await chain.balance(checkAddr(req.params.addr)) })));

// ---------------------------------------------------------------- pairing: the rig's songs belong to one wallet
app.post('/api/pair', wrap(async (req, res) => {
  const wallet = checkAddr(req.body.wallet);
  takeNonce(req.body.nonce);
  checkSig(pairMessage(wallet, req.body.nonce), req.body.signature, wallet);
  store.reg.rig = { wallet, paired_at: new Date().toISOString() };
  store.save();
  res.json({ rig: store.reg.rig });
}));

// ---------------------------------------------------------------- claim = creator signs the fingerprint, we mint
const minting = new Set();
app.post('/api/claim', wrap(async (req, res) => {
  const wallet = checkAddr(req.body.wallet);
  if (store.reg.rig?.wallet !== wallet) throw fail(403, 'only the wallet paired with this rig can claim its songs');
  const all = store.captured();
  const e = all[req.body.song_id];
  if (!e) throw fail(404, 'no such song on this rig');
  checkSig(claimMessage(e.song), req.body.signature, wallet);
  const check = store.mintable(e, all);
  if (!check.ok) throw fail(409, check.reason);
  if (minting.has(e.song.fingerprint)) throw fail(409, 'already minting');
  minting.add(e.song.fingerprint);
  try {
    const out = await chain.mintSong(e.song, wallet, `${PUBLIC_URL}/api/metadata/${e.song.song_id}.json`);
    store.record(e.song, { creator: wallet, owner: wallet, claim_signature: req.body.signature, ...out });
    console.log(`[mint] ${e.song.song_id.slice(0, 10)} -> ${out.asset} for ${wallet}`);
    res.json({ ...store.reg.songs[e.song.song_id], asset_url: chain.explorer('address', out.asset) });
  } finally {
    minting.delete(e.song.fingerprint);
  }
}));

// ---------------------------------------------------------------- list / buy: prepared here, co-signed by the user
const pending = new Map();
async function prepare(res, entry) {
  const { tx, lastValidBlockHeight } = entry.built;
  const id = crypto.randomBytes(8).toString('hex');
  pending.set(id, { ...entry, built: undefined, lastValidBlockHeight, message: Buffer.from(tx.message.serialize()) });
  setTimeout(() => pending.delete(id), 3 * 60_000);
  res.json({ pending_id: id, tx: Buffer.from(tx.serialize()).toString('base64') });
}
function owned(songId) {
  const m = store.reg.songs[songId];
  if (!m) throw fail(404, 'song is not minted');
  return m;
}

app.post('/api/list/prepare', wrap(async (req, res) => {
  const seller = checkAddr(req.body.seller);
  const m = owned(req.body.song_id);
  if (m.owner !== seller || m.listing) throw fail(403, 'you do not hold this song');
  const sol = Number(req.body.price_sol);
  if (!(sol > 0 && sol <= 1000)) throw fail(400, 'price must be between 0 and 1000 SOL');
  await prepare(res, { kind: 'list', song_id: m.song_id, seller, lamports: Math.round(sol * LAMPORTS_PER_SOL),
    built: await chain.listTx(m.asset, seller) });
}));

app.post('/api/buy/prepare', wrap(async (req, res) => {
  const buyer = checkAddr(req.body.buyer);
  const m = owned(req.body.song_id);
  if (!m.listing) throw fail(409, 'not for sale');
  if (m.listing.seller === buyer) throw fail(400, 'that is your own listing');
  const need = m.listing.lamports / LAMPORTS_PER_SOL;
  if ((await chain.balance(buyer)) < need) throw fail(402, `not enough SOL: need ${need}`);
  await prepare(res, { kind: 'buy', song_id: m.song_id, buyer, seller: m.listing.seller, lamports: m.listing.lamports,
    built: await chain.buyTx(m.asset, buyer, m.listing.seller, m.listing.lamports) });
}));

app.post('/api/submit', wrap(async (req, res) => {
  const p = pending.get(req.body.pending_id);
  if (!p) throw fail(410, 'this sale expired, try again');
  const tx = VersionedTransaction.deserialize(Buffer.from(req.body.tx || '', 'base64'));
  // the marketplace already signed this exact message, so any change breaks our signature; check anyway
  if (!Buffer.from(tx.message.serialize()).equals(p.message)) throw fail(400, 'transaction was modified');
  pending.delete(req.body.pending_id);
  const sig = await chain.send(tx, p.lastValidBlockHeight);
  const m = store.reg.songs[p.song_id];
  if (p.kind === 'list') {
    store.update(p.song_id, { owner: chain.escrow.toBase58(), listing: { seller: p.seller, lamports: p.lamports, sig } });
  } else {
    store.update(p.song_id, { owner: p.buyer, listing: null });
    store.reg.sales.push({ song_id: p.song_id, from: p.seller, to: p.buyer, lamports: p.lamports, sig, at: Date.now() });
    store.save();
  }
  console.log(`[${p.kind}] ${p.song_id.slice(0, 10)} ${sig}`);
  res.json({ sig, tx_url: chain.explorer('tx', sig), song: m });
}));

app.post('/api/delist', wrap(async (req, res) => {
  const seller = checkAddr(req.body.seller);
  const m = owned(req.body.song_id);
  if (m.listing?.seller !== seller) throw fail(403, 'not your listing');
  takeNonce(req.body.nonce);
  checkSig(delistMessage(m.song_id, req.body.nonce), req.body.signature, seller);
  const sig = await chain.delist(m.asset, seller);
  store.update(m.song_id, { owner: seller, listing: null });
  res.json({ sig, tx_url: chain.explorer('tx', sig) });
}));

// ---------------------------------------------------------------- devnet top-up so a fresh email login can buy
const toppedUp = new Set();
app.post('/api/faucet', wrap(async (req, res) => {
  if (chain.cluster !== 'devnet') throw fail(403, 'faucet is devnet only');
  const wallet = checkAddr(req.body.wallet);
  if (toppedUp.has(wallet)) throw fail(429, 'already topped up this session');
  if ((await chain.balance(wallet)) >= 0.5) throw fail(400, 'you already have enough devnet SOL');
  toppedUp.add(wallet);
  const sig = await chain.topUp(wallet, 1);
  res.json({ sig, tx_url: chain.explorer('tx', sig) });
}));

const dist = path.resolve(root, 'dist');
if (fs.existsSync(dist)) {
  app.use(express.static(dist));
  app.get(/^\/(?!api\/).*/, (req, res) => res.sendFile(path.join(dist, 'index.html')));
}

app.listen(PORT, async () => {
  console.log(`[market] http://localhost:${PORT}  ${chain.cluster}  escrow ${chain.escrow.toBase58()}`
    + `  (${(await chain.balance(chain.escrow).catch(() => NaN)).toFixed(3)} SOL)  songs from ${store.songsDir}`);
});
