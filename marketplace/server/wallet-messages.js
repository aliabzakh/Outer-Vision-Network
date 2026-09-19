// Eye-wallet texts, identical to outer_vision/wallet/delegation.py (checked by tests on both sides).
// Shared by the phone page (signs them) and the server (verifies delegated mints).
export const LAMPORTS_PER_SOL = 1_000_000_000;

/** Exact decimal SOL from integer lamports, no floats. */
export function sol(lamports) {
  const l = Math.trunc(Number(lamports));
  const whole = Math.floor(l / LAMPORTS_PER_SOL);
  const frac = String(l % LAMPORTS_PER_SOL).padStart(9, '0').replace(/0+$/, '');
  return frac ? `${whole}.${frac}` : String(whole);
}

export const iso = (ms) => new Date(Math.floor(ms / 1000) * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');

export function delegationMessage(d) {
  const contacts = d.contacts.map((c) => `${c.label} (${c.address})`).join(', ') || 'none';
  return 'Outer Vision: let my headset act for me.\n'
    + `owner: ${d.owner}\n`
    + `headset key: ${d.session}\n`
    + `rig: ${d.rig}\n`
    + `allowed: ${d.actions.join(', ') || 'nothing'}\n`
    + `tip size: ${sol(d.tip_lamports)} SOL\n`
    + `daily limit: ${sol(d.daily_lamports)} SOL\n`
    + `contacts: ${contacts}\n`
    + `expires: ${iso(d.expires_ms)} (${d.expires_ms})\n`
    + `nonce: ${d.nonce}`;
}

export const rigRevokeMessage = (owner, rig, nonce) =>
  `Outer Vision: stop my headset acting for me and return its SOL.\nowner: ${owner}\nrig: ${rig}\nnonce: ${nonce}`;

export const marketRevokeMessage = (owner, session, nonce) =>
  `Outer Vision: the marketplace must stop accepting headset key ${session}.\nowner: ${owner}\nnonce: ${nonce}`;
