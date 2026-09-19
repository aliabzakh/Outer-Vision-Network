// /phone: the owner's control panel for the eye wallet. Open it on the Solana phone (Seeker/Saga: the
// Seed Vault wallet signs through Mobile Wallet Adapter) or any phone with a Privy email wallet.
import { useEffect, useRef, useState } from 'react';
import { usePrivy } from '@privy-io/react-auth';
import { useSignAndSendTransaction, useSignMessage, useWallets } from '@privy-io/react-auth/solana';
import { Connection, PublicKey, SystemProgram, Transaction } from '@solana/web3.js';
import bs58 from 'bs58';
import { pairMessage } from '../../server/messages.js';
import { delegationMessage, marketRevokeMessage, rigRevokeMessage, sol } from '../../server/wallet-messages.js';
import { toLamports } from './framing.js';
import { BleLink, HttpLink } from './link.js';

const RPC = import.meta.env.VITE_SOLANA_RPC || 'https://api.devnet.solana.com';
const CHAIN = 'solana:devnet';
const short = (a) => (a ? `${a.slice(0, 4)}…${a.slice(-4)}` : '');
const b64 = (bytes) => btoa(String.fromCharCode(...bytes));
const HOURS = [1, 8, 24, 72, 168];

async function api(path, body) {
  const r = await fetch(`/api/${path}`, body ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `${r.status}`);
  return j;
}

function isAddress(a) {
  try { return new PublicKey(a.trim()).toBytes().length === 32; } catch { return false; }
}

export default function Phone() {
  const { ready, authenticated, login, logout } = usePrivy();
  const { wallets } = useWallets();
  const { signMessage } = useSignMessage();
  const { signAndSendTransaction } = useSignAndSendTransaction();
  const [pick, setPick] = useState(null);
  const wallet = wallets.find((w) => w.address === pick) || wallets.find((w) => w.standardWallet?.name !== 'Privy') || wallets[0];
  const me = wallet?.address;

  const linkRef = useRef(null);
  const [linkLabel, setLinkLabel] = useState(null);
  const [rig, setRig] = useState(null);
  const [feed, setFeed] = useState([]);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const [httpUrl, setHttpUrl] = useState(`${location.origin}/rig`);
  const [form, setForm] = useState({
    mint: true, tip: true, tipSol: '0.01', dailySol: '0.05', hours: 8,
    c1: '', a1: '', c2: '', a2: '',
  });
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  // the last headset key this phone authorised, so it can still be revoked if the headset is lost
  const [lastKey, setLastKey] = useState(() => { try { return localStorage.getItem('ov-headset-key'); } catch { return null; } });
  const remember = (k) => { setLastKey(k); try { if (k) localStorage.setItem('ov-headset-key', k); else localStorage.removeItem('ov-headset-key'); } catch { /* private mode */ } };

  const refresh = async () => {
    if (linkRef.current) setRig(await linkRef.current.request('status'));
  };
  useEffect(() => {
    const t = setInterval(() => refresh().catch(() => {}), 10_000);
    return () => clearInterval(t);
  }, []);

  const act = (key, fn) => async () => {
    setBusy(key);
    setNote(null);
    try {
      const msg = await fn();
      if (msg) setNote({ text: msg.text || msg, url: msg.url });
    } catch (e) {
      setNote({ text: e.message, error: true });
    } finally {
      setBusy(null);
      refresh().catch(() => {});
    }
  };

  const attach = async (link) => {
    link.onEvent = (e) => { setFeed((f) => [e, ...f].slice(0, 30)); refresh().catch(() => {}); };
    link.onClose = () => { setLinkLabel(null); linkRef.current = null; };
    await link.connect();
    linkRef.current?.close();
    linkRef.current = link;
    setLinkLabel(link.label);
    const st = await link.request('status');
    setRig(st);
    setFeed(st.events.slice().reverse());
    return `Connected to ${st.rig}.`;
  };

  const sign = async (text) => b64((await signMessage({ message: new TextEncoder().encode(text), wallet })).signature);

  const authorise = act('auth', async () => {
    const st = await linkRef.current.request('status');          // fresh one-time challenge
    const contacts = [[form.c1, form.a1], [form.c2, form.a2]]
      .filter(([n, a]) => n.trim() || a.trim())
      .map(([n, a]) => {
        if (!/^[A-Za-z0-9][A-Za-z0-9 ]{0,15}$/.test(n.trim())) throw new Error(`contact name "${n}": letters, digits, spaces, up to 16`);
        if (!isAddress(a)) throw new Error(`${n.trim()}'s address isn't a Solana address`);
        return { label: n.trim(), address: new PublicKey(a.trim()).toBase58() };
      });
    const actions = ['mint', 'tip'].filter((a) => form[a]);
    const d = {
      owner: me, session: st.session, rig: st.rig, actions,
      tip_lamports: form.tip ? toLamports(form.tipSol) : 0,
      daily_lamports: form.tip ? toLamports(form.dailySol) : 0,
      contacts: form.tip ? contacts : [],
      expires_ms: Date.now() + form.hours * 3600_000, nonce: st.challenge,
    };
    const signature = await sign(delegationMessage(d));
    await linkRef.current.request('delegate', { delegation: d, signature });
    remember(d.session);
    if (form.mint) {                                               // the marketplace mints the rig's songs to this wallet
      const { nonce } = await api('nonce');
      await api('pair', { wallet: me, nonce, signature: await sign(pairMessage(me, nonce)) }).catch((e) => {
        throw new Error(`Headset authorised, but pairing with the marketplace failed: ${e.message}`);
      });
    }
    return 'Headset authorised. Now fund it: it can never spend more than you send it.';
  });

  const fund = act('fund', async () => {
    const lamports = toLamports(form.dailySol);
    const conn = new Connection(RPC, 'confirmed');
    const { blockhash } = await conn.getLatestBlockhash('confirmed');
    const tx = new Transaction({ feePayer: new PublicKey(me), recentBlockhash: blockhash })
      .add(SystemProgram.transfer({ fromPubkey: new PublicKey(me), toPubkey: new PublicKey(rig.session), lamports }));
    const { signature } = await signAndSendTransaction({
      transaction: tx.serialize({ requireAllSignatures: false, verifySignatures: false }), wallet, chain: CHAIN,
    });
    const sig = bs58.encode(signature);
    return { text: `Sent ${sol(lamports)} SOL to the headset.`, url: `https://explorer.solana.com/tx/${sig}?cluster=devnet` };
  });

  // Works even if the headset is lost: the marketplace stops accepting its key first.
  const revoke = act('revoke', async () => {
    const session = rig?.delegation?.session || lastKey;
    const done = [];
    if (session) {
      const { nonce } = await api('nonce');
      await api('delegation/revoke', { owner: me, session, nonce, signature: await sign(marketRevokeMessage(me, session, nonce)) });
      done.push('marketplace no longer accepts the headset key');
      remember(null);
    }
    if (linkRef.current) {
      const st = await linkRef.current.request('status');
      const out = await linkRef.current.request('revoke', { owner: me, nonce: st.challenge, signature: await sign(rigRevokeMessage(me, st.rig, st.challenge)) });
      done.push(out.sweep_pending ? 'headset stopped (refund still pending, it will retry)' : 'headset stopped and SOL returned');
    }
    return done.length ? `Revoked: ${done.join('; ')}.` : 'Connect the headset first.';
  });

  if (!ready) return <main className="wrap"><p className="muted">Loading…</p></main>;
  const d = rig?.delegation;
  const mine = d && d.owner === me;
  const lostHeadsetKey = !linkLabel && lastKey;

  return (
    <main className="wrap phone">
      <header>
        <div>
          <h1>Eye Wallet</h1>
          <p className="muted">Let the headset mint and tip for you, by blink, within limits you sign here.</p>
        </div>
        {authenticated
          ? <div className="who"><span className="mono">{short(me)}</span><button className="ghost" onClick={logout}>Log out</button></div>
          : <button onClick={login}>Log in</button>}
      </header>

      {note && <p className={`note ${note.error ? 'err' : ''}`}>{note.text} {note.url && <a href={note.url} target="_blank" rel="noreferrer">explorer ↗</a>}</p>}

      {authenticated && wallets.length > 1 && (
        <section>
          <h2>Signing wallet</h2>
          <div className="card row">
            <select value={me} onChange={(e) => setPick(e.target.value)} aria-label="signing wallet">
              {wallets.map((w) => <option key={w.address} value={w.address}>{w.standardWallet?.name} · {short(w.address)}</option>)}
            </select>
            <span className="muted">Pick the Seed Vault wallet on a Solana phone.</span>
          </div>
        </section>
      )}

      <section>
        <h2>1 · Headset</h2>
        <div className="card">
          {linkLabel ? (
            <div className="row between">
              <span>Connected: <strong>{rig?.rig}</strong> <span className="muted">via {linkLabel}</span></span>
              <button className="ghost" onClick={() => { linkRef.current?.close(); linkRef.current = null; setLinkLabel(null); }}>Disconnect</button>
            </div>
          ) : (
            <div className="stack">
              {BleLink.supported()
                ? <button onClick={act('ble', () => attach(new BleLink()))} disabled={!!busy}>Connect by Bluetooth</button>
                : <p className="muted">This browser has no Web Bluetooth (use Chrome on Android over https), so connect over Wi-Fi:</p>}
              <div className="row">
                <input className="grow" value={httpUrl} onChange={(e) => setHttpUrl(e.target.value)} aria-label="headset address" />
                <button className="ghost" onClick={act('http', () => attach(new HttpLink(httpUrl)))} disabled={!!busy}>Connect over Wi-Fi</button>
              </div>
            </div>
          )}
          {rig && (
            <dl>
              <dt>headset key</dt><dd className="mono">{short(rig.session)}</dd>
              <dt>balance</dt><dd>{rig.balance_lamports != null ? `${sol(rig.balance_lamports)} SOL` : rig.balance_error || '–'}</dd>
              <dt>status</dt><dd>{rig.active ? `working for ${mine ? 'you' : short(d.owner)}` : d ? 'permission expired' : 'not authorised'}</dd>
              {d && <><dt>allowed</dt><dd>{d.actions.join(', ')}</dd></>}
              {d?.actions.includes('tip') && <><dt>tips</dt><dd>{sol(d.tip_lamports)} SOL each · {sol(rig.spent_today_lamports)} of {sol(d.daily_lamports)} today</dd></>}
              {d?.contacts?.length > 0 && <><dt>contacts</dt><dd>{d.contacts.map((c) => c.label).join(', ')}</dd></>}
              {d && <><dt>expires</dt><dd>{new Date(d.expires_ms).toLocaleString()}</dd></>}
              {rig.sweep_pending && <><dt>refund</dt><dd className="warn">pending, retrying</dd></>}
            </dl>
          )}
        </div>
      </section>

      {authenticated && linkLabel && (!rig?.active || mine) && (
        <section>
          <h2>2 · What it may do</h2>
          <form className="card stack" onSubmit={(e) => { e.preventDefault(); authorise(); }}>
            <label className="row"><input type="checkbox" checked={form.mint} onChange={set('mint')} /> Mint my songs (free, they go to this wallet)</label>
            <label className="row"><input type="checkbox" checked={form.tip} onChange={set('tip')} /> Send tips to my contacts</label>
            {form.tip && (
              <>
                <div className="row">
                  <label>Each tip <input value={form.tipSol} onChange={set('tipSol')} inputMode="decimal" /> SOL</label>
                  <label>Per day <input value={form.dailySol} onChange={set('dailySol')} inputMode="decimal" /> SOL</label>
                </div>
                <p className="muted small">Left eye picks the first contact, right eye the second.</p>
                {[['c1', 'a1', 'Left eye'], ['c2', 'a2', 'Right eye']].map(([c, a, eye]) => (
                  <div className="row" key={c}>
                    <input placeholder={`${eye}: name`} value={form[c]} onChange={set(c)} maxLength={16} />
                    <input className="grow mono" placeholder="Solana address" value={form[a]} onChange={set(a)} />
                  </div>
                ))}
              </>
            )}
            <label className="row">For
              <select value={form.hours} onChange={(e) => setForm({ ...form, hours: Number(e.target.value) })}>
                {HOURS.map((h) => <option key={h} value={h}>{h < 24 ? `${h} h` : `${h / 24} day${h > 24 ? 's' : ''}`}</option>)}
              </select>
            </label>
            <button disabled={!!busy || (!form.mint && !form.tip)}>{busy === 'auth' ? 'Waiting for your wallet…' : 'Sign & authorise headset'}</button>
          </form>
        </section>
      )}

      {authenticated && linkLabel && mine && d.actions.includes('tip') && (
        <section>
          <h2>3 · Fund it</h2>
          <div className="card row between">
            <span>The headset can only spend what you send it.</span>
            <button onClick={fund} disabled={!!busy}>{busy === 'fund' ? 'Sending…' : `Send ${form.dailySol} SOL`}</button>
          </div>
        </section>
      )}

      {authenticated && (mine || lostHeadsetKey || rig?.sweep_pending) && (
        <section>
          <h2>Stop</h2>
          <div className="card row between danger">
            <span>Revoke immediately and return the headset's SOL to you.</span>
            <button className="danger" onClick={revoke} disabled={!!busy}>{busy === 'revoke' ? 'Revoking…' : 'Revoke'}</button>
          </div>
        </section>
      )}

      {feed.length > 0 && (
        <section>
          <h2>What the headset did</h2>
          <ul className="sales">
            {feed.map((e) => (
              <li key={`${e.t_ms}-${e.kind}-${e.text}`} className={e.ok ? '' : 'warn'}>
                <span className="muted">{new Date(e.t_ms).toLocaleTimeString()}</span> {e.kind}: {e.text}
                {e.url && <> <a href={e.url} target="_blank" rel="noreferrer">↗</a></>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
