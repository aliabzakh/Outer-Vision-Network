// The phone's side of the headset link: Web Bluetooth (GATT, chunked JSON) or HTTP on the same network.
import { chunk, Reassembler } from './framing.js';

export const SERVICE = '7b3e0001-5f2a-4c1e-9d6a-0e7e5f0a11ce';
const RX = '7b3e0002-5f2a-4c1e-9d6a-0e7e5f0a11ce';
const TX = '7b3e0003-5f2a-4c1e-9d6a-0e7e5f0a11ce';
const CHUNK = 180;
const TIMEOUT_MS = 30_000;          // revoke waits for the refund transaction

class Base {
  constructor() {
    this.next = 1;
    this.waiting = new Map();
    this.onEvent = () => {};
    this.onClose = () => {};
  }

  request(op, body = {}) {
    const id = this.next++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.waiting.delete(id); reject(new Error('the headset did not answer')); }, TIMEOUT_MS);
      this.waiting.set(id, { resolve, reject, timer });
      this.send({ id, op, ...body }).catch((e) => { clearTimeout(timer); this.waiting.delete(id); reject(e); });
    });
  }

  receive(msg) {
    if (msg.event) return this.onEvent(msg.event);
    const w = this.waiting.get(msg.id);
    if (!w) return;
    clearTimeout(w.timer);
    this.waiting.delete(msg.id);
    if (msg.ok) w.resolve(msg.result); else w.reject(new Error(msg.error));
  }
}

export class BleLink extends Base {
  static supported() { return typeof navigator !== 'undefined' && !!navigator.bluetooth; }

  async connect() {
    this.device = await navigator.bluetooth.requestDevice({ filters: [{ services: [SERVICE] }] });
    this.device.addEventListener('gattserverdisconnected', () => this.onClose());
    const server = await this.device.gatt.connect();
    const svc = await server.getPrimaryService(SERVICE);
    this.rx = await svc.getCharacteristic(RX);
    const tx = await svc.getCharacteristic(TX);
    const re = new Reassembler();
    tx.addEventListener('characteristicvaluechanged', (e) => {
      const v = e.target.value;
      const msg = re.feed(new Uint8Array(v.buffer, v.byteOffset, v.byteLength));
      if (msg) this.receive(JSON.parse(new TextDecoder().decode(msg)));
    });
    await tx.startNotifications();
    this.msgId = 0;
    this.label = this.device.name || 'headset';
    return this;
  }

  async send(obj) {
    const parts = chunk(new TextEncoder().encode(JSON.stringify(obj)), (this.msgId = (this.msgId + 1) & 0xff), CHUNK);
    for (const p of parts) await this.rx.writeValueWithResponse(p);
  }

  close() { this.device?.gatt?.disconnect(); }
}

export class HttpLink extends Base {
  constructor(base) {
    super();
    this.base = base.replace(/\/$/, '');
    this.label = base;
    this.since = Date.now() - 60_000;
  }

  async connect() {
    await this.request('ping');
    this.poll = setInterval(async () => {
      try {
        const evs = await (await fetch(`${this.base}/wallet/events?since=${this.since}`)).json();
        for (const e of evs) { this.since = Math.max(this.since, e.t_ms); this.onEvent(e); }
      } catch { /* headset asleep: keep polling */ }
    }, 2000);
    return this;
  }

  async send(obj) {
    let r;
    try {
      r = await fetch(`${this.base}/wallet`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(obj) });
    } catch {
      throw new Error(`can't reach the headset at ${this.base}`);
    }
    this.receive(await r.json());
  }

  close() { clearInterval(this.poll); }
}
