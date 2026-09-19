// BLE chunk framing, identical to outer_vision/wallet/link.py: [msg_id, index, count] + payload.
export const HEADER = 3;
export const MAX_CHUNKS = 128;

export function chunk(bytes, msgId, size) {
  const body = Math.max(1, size - HEADER);
  const parts = [];
  for (let i = 0; i < bytes.length; i += body) parts.push(bytes.subarray(i, i + body));
  if (!parts.length) parts.push(new Uint8Array(0));
  if (parts.length > MAX_CHUNKS) throw new Error('message too large for BLE framing');
  return parts.map((p, i) => {
    const out = new Uint8Array(HEADER + p.length);
    out.set([msgId & 0xff, i, parts.length]);
    out.set(p, HEADER);
    return out;
  });
}

export class Reassembler {
  constructor(timeoutMs = 10_000, clock = () => Date.now()) {
    this.timeoutMs = timeoutMs;
    this.clock = clock;
    this.partial = new Map();
  }

  /** Returns the whole message (Uint8Array) when its last chunk arrives, else null. */
  feed(raw) {
    const now = this.clock();
    for (const [k, v] of this.partial) if (now - v.t > this.timeoutMs) this.partial.delete(k);
    if (raw.length < HEADER) return null;
    const [mid, idx, count] = raw;
    if (count === 0 || count > MAX_CHUNKS || idx >= count) return null;
    let p = this.partial.get(mid);
    if (!p || p.count !== count) {
      p = { count, parts: new Map(), t: now };
      this.partial.set(mid, p);
    }
    p.parts.set(idx, raw.slice(HEADER));
    p.t = now;
    if (p.parts.size < count) return null;
    this.partial.delete(mid);
    const pieces = [...Array(count).keys()].map((i) => p.parts.get(i));
    const out = new Uint8Array(pieces.reduce((n, x) => n + x.length, 0));
    let o = 0;
    for (const x of pieces) { out.set(x, o); o += x.length; }
    return out;
  }
}

/** "0.015" -> 15000000 lamports, exactly (no float maths). Throws on bad input. */
export function toLamports(text) {
  const m = /^\s*(\d{1,6})(?:\.(\d{0,9}))?\s*$/.exec(String(text));
  if (!m) throw new Error(`not an amount: ${text}`);
  return Number(m[1]) * 1_000_000_000 + Number((m[2] || '').padEnd(9, '0'));
}
