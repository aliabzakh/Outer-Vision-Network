import { useCallback, useEffect, useRef, useState } from 'react';
import { usePrivy } from '@privy-io/react-auth';
import { useSignMessage, useSignTransaction, useWallets } from '@privy-io/react-auth/solana';
import { claimMessage, delistMessage, pairMessage } from '../server/messages.js';
import { play } from './player.js';

const CHAIN = 'solana:devnet';
const short = (a) => (a ? `${a.slice(0, 4)}…${a.slice(-4)}` : '');
const sol = (lamports) => +(lamports / 1e9).toFixed(4);
const b64 = (bytes) => btoa(String.fromCharCode(...bytes));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

async function api(path, body) {
  const r = await fetch(`/api/${path}`, body ? {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  } : undefined);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `${r.status}`);
  return j;
}

export default function App() {
  const { ready, authenticated, login, logout, user } = usePrivy();
  const { wallets } = useWallets();
  const { signMessage } = useSignMessage();
  const { signTransaction } = useSignTransaction();
  const wallet = wallets.find((w) => w.standardWallet?.name === 'Privy') || wallets[0];
  const me = wallet?.address;

  const [state, setState] = useState(null);
  const [balance, setBalance] = useState(null);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);           // {text, url?, error?}
  const [playing, setPlaying] = useState(null);      // {id, index}
  const stopRef = useRef(null);

  const refresh = useCallback(async () => {
    setState(await api('state').catch((e) => ({ error: e.message })));
    if (me) setBalance((await api(`wallet/${me}`).catch(() => ({ sol: null }))).sol);
  }, [me]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);           // new songs appear as the rig saves them
    return () => clearInterval(t);
  }, [refresh]);

  const sign = async (text) => {
    const { signature } = await signMessage({ message: new TextEncoder().encode(text), wallet });
    return b64(signature);
  };
  const cosign = async (prepared) => {
    const { signedTransaction } = await signTransaction({ transaction: unb64(prepared.tx), wallet, chain: CHAIN });
    return api('submit', { pending_id: prepared.pending_id, tx: b64(signedTransaction) });
  };
  const run = (key, label, fn) => async () => {
    setBusy(key);
    setNote(null);
    try {
      const out = await fn();
      setNote({ text: label, url: out?.tx_url || out?.asset_url });
    } catch (e) {
      setNote({ text: e.message, error: true });
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const listen = async (id) => {
    stopRef.current?.();
    if (playing?.id === id) return setPlaying(null);
    const song = await api(`song/${id}`);
    stopRef.current = await play(song, (i) => setPlaying(i < 0 ? null : { id, index: i }));
  };

  const pair = run('pair', 'Rig paired: new songs from the headset are yours to claim.', async () => {
    const { nonce } = await api('nonce');
    return api('pair', { wallet: me, nonce, signature: await sign(pairMessage(me, nonce)) });
  });
  const claim = (s) => run(s.song_id, 'Minted to your wallet.', async () =>
    api('claim', { song_id: s.song_id, wallet: me, signature: await sign(claimMessage(s)) }));
  const list = (s, price) => run(s.song_id, `Listed for ${price} SOL.`, async () =>
    cosign(await api('list/prepare', { song_id: s.song_id, seller: me, price_sol: price })));
  const delist = (s) => run(s.song_id, 'Back in your wallet.', async () => {
    const { nonce } = await api('nonce');
    return api('delist', { song_id: s.song_id, seller: me, nonce, signature: await sign(delistMessage(s.song_id, nonce)) });
  });
  const buy = (s) => run(s.song_id, 'Bought: the song and the SOL swapped in one transaction.', async () =>
    cosign(await api('buy/prepare', { song_id: s.song_id, buyer: me })));
  const faucet = run('faucet', 'Sent you 1 devnet SOL.', () => api('faucet', { wallet: me }));

  if (!ready) return <main className="wrap"><p className="muted">Loading…</p></main>;

  const rigMine = state?.rig?.wallet === me;
  const market = state?.minted?.filter((m) => m.listing) ?? [];
  const mine = state?.minted?.filter((m) => m.owner === me || m.listing?.seller === me) ?? [];

  return (
    <main className="wrap">
      <header>
        <div>
          <h1>Eye Songs</h1>
          <p className="muted">Songs played with the eyes on Outer Vision, owned by the player's wallet.</p>
        </div>
        {authenticated ? (
          <div className="who">
            <span className="mono" title={me}>{me ? short(me) : 'creating wallet…'}</span>
            <span className="muted">{user?.email?.address} · {balance ?? '–'} SOL</span>
            <div className="row">
              {state?.cluster === 'devnet' && balance !== null && balance < 0.5 &&
                <button onClick={faucet} disabled={busy}>Get devnet SOL</button>}
              <button className="ghost" onClick={logout}>Log out</button>
            </div>
          </div>
        ) : <button onClick={login}>Log in with email</button>}
      </header>

      {note && <p className={`note ${note.error ? 'err' : ''}`}>{note.text} {note.url && <a href={note.url} target="_blank" rel="noreferrer">explorer ↗</a>}</p>}
      {state?.error && <p className="note err">Marketplace server unreachable ({state.error}). Is <code>npm run server</code> running?</p>}

      {authenticated && me && (
        <section>
          <h2>This rig</h2>
          <div className="card row between">
            {state?.rig ? (
              <span>Paired with <span className="mono">{short(state.rig.wallet)}</span>{rigMine ? ' (you)' : ''}</span>
            ) : <span className="muted">Not paired. The paired wallet is the creator of every song the headset records.</span>}
            {!rigMine && <button onClick={pair} disabled={busy}>{state?.rig ? 'Pair with my wallet instead' : 'Pair with my wallet'}</button>}
          </div>
        </section>
      )}

      <section>
        <h2>Recorded on the headset</h2>
        {!state?.captured?.length && <p className="muted">No songs yet. Play a phrase with the headset; it saves after {8} s of silence.</p>}
        <div className="grid">
          {state?.captured?.map((s) => (
            <SongCard key={s.song_id} s={s} playing={playing} onPlay={listen}>
              {s.mint_check?.ok
                ? <button onClick={claim(s)} disabled={!rigMine || busy} title={rigMine ? '' : 'pair this rig with your wallet first'}>
                    {busy === s.song_id ? 'Minting…' : 'Sign & mint'}</button>
                : <span className="tag warn" title={s.mint_check?.reason}>{s.mint_check?.reason}</span>}
            </SongCard>
          ))}
        </div>
      </section>

      <section>
        <h2>For sale</h2>
        {!market.length && <p className="muted">Nothing listed.</p>}
        <div className="grid">
          {market.map((m) => (
            <SongCard key={m.song_id} s={m} playing={playing} onPlay={listen} minted>
              <span className="price">{sol(m.listing.lamports)} SOL</span>
              {m.listing.seller === me
                ? <button className="ghost" onClick={delist(m)} disabled={busy}>Delist</button>
                : <button onClick={buy(m)} disabled={!me || busy}>{busy === m.song_id ? 'Buying…' : 'Buy'}</button>}
            </SongCard>
          ))}
        </div>
      </section>

      {me && (
        <section>
          <h2>Your songs</h2>
          {!mine.length && <p className="muted">None yet.</p>}
          <div className="grid">
            {mine.filter((m) => !m.listing).map((m) => (
              <SongCard key={m.song_id} s={m} playing={playing} onPlay={listen} minted>
                <ListForm onList={(p) => list(m, p)()} busy={busy === m.song_id} disabled={!!busy} />
              </SongCard>
            ))}
          </div>
        </section>
      )}

      {!!state?.sales?.length && (
        <section>
          <h2>Recent sales</h2>
          <ul className="sales">
            {state.sales.map((x) => (
              <li key={x.sig}><span className="mono">{x.song_id.slice(0, 8)}</span> {short(x.from)} → {short(x.to)} · {sol(x.lamports)} SOL{' '}
                <a href={`https://explorer.solana.com/tx/${x.sig}?cluster=${state.cluster}`} target="_blank" rel="noreferrer">tx ↗</a></li>
            ))}
          </ul>
        </section>
      )}
      <footer className="muted">Escrow {short(state?.escrow)} · {state?.cluster}</footer>
    </main>
  );
}

function SongCard({ s, playing, onPlay, minted, children }) {
  const on = playing?.id === s.song_id;
  return (
    <article className="card song">
      <img src={`/api/art/${s.song_id}.svg`} alt="table layout and played path" onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }} />
      <div className="row between">
        <strong className="mono">{s.song_id.slice(0, 10)}</strong>
        <button className="ghost" onClick={() => onPlay(s.song_id)} disabled={s.missing_file}>{on ? `■ ${playing.index + 1}/${s.notes}` : '▶ Play'}</button>
      </div>
      <dl>
        <dt>fingerprint</dt><dd className="mono" title={s.fingerprint}>{s.fingerprint.slice(0, 16)}</dd>
        <dt>captured</dt><dd>{new Date(s.captured_at_ms).toLocaleString()}</dd>
        <dt>notes</dt><dd>{s.notes}{s.objects ? ` on ${s.objects} objects` : ''}</dd>
        {minted && <><dt>creator</dt><dd className="mono">{short(s.creator)}</dd></>}
        {minted && s.block_time && <><dt>on-chain</dt><dd>{new Date(s.block_time * 1000).toLocaleString()}</dd></>}
        {minted && <><dt>asset</dt><dd><a href={s.asset_url} target="_blank" rel="noreferrer" className="mono">{short(s.asset)} ↗</a></dd></>}
      </dl>
      <div className="row between">{children}</div>
    </article>
  );
}

function ListForm({ onList, busy, disabled }) {
  const [price, setPrice] = useState('0.1');
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); onList(Number(price)); }}>
      <input type="number" min="0.001" step="0.001" value={price} onChange={(e) => setPrice(e.target.value)} aria-label="price in SOL" />
      <span className="muted">SOL</span>
      <button disabled={disabled}>{busy ? 'Listing…' : 'List'}</button>
    </form>
  );
}
