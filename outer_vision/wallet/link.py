"""Phone <-> headset protocol, independent of the transport (BLE in ble.py, HTTP in http_link.py).

Request  {"id": 7, "op": "status" | "delegate" | "revoke" | "sweep" | "ping", ...}
Response {"id": 7, "ok": true, "result": {...}}  or  {"id": 7, "ok": false, "error": "why"}
Push     {"event": {...}}   every wallet event (tips, mints, refusals) for the phone's live feed

BLE writes are small, so JSON is split into chunks: [msg_id, index, count] + payload bytes.
marketplace/src/phone/link.js speaks the same framing.
"""
from __future__ import annotations

import json
import time

from .delegation import DelegationError
from .policy import WalletError

MAX_MESSAGE = 16 * 1024
MAX_CHUNKS = 128
HEADER = 3


class Link:
    def __init__(self, wallet):
        self.wallet = wallet

    def handle(self, req) -> dict:
        rid = req.get("id") if isinstance(req, dict) else None
        try:
            if not isinstance(req, dict) or not isinstance(req.get("op"), str):
                raise WalletError("request must be an object with an op")
            op = req["op"]
            w = self.wallet
            if op == "ping":
                result = {"pong": True, "rig": w.rig}
            elif op == "status":
                result = w.status(with_balance=True)
            elif op == "delegate":
                if not isinstance(req.get("delegation"), dict) or not isinstance(req.get("signature"), str):
                    raise WalletError("delegate needs delegation and signature")
                result = w.delegate(req["delegation"], req["signature"])
            elif op == "revoke":
                if not all(isinstance(req.get(k), str) for k in ("owner", "signature", "nonce")):
                    raise WalletError("revoke needs owner, signature and nonce")
                result = w.revoke(req["owner"], req["signature"], req["nonce"])
            elif op == "sweep":
                result = w.sweep()
            else:
                raise WalletError(f"unknown op {op!r}")
            return {"id": rid, "ok": True, "result": result}
        except (WalletError, DelegationError) as e:
            return {"id": rid, "ok": False, "error": str(e)}
        except Exception as e:                      # never let a bad request kill the link
            return {"id": rid, "ok": False, "error": f"internal error: {type(e).__name__}"}

    def handle_bytes(self, raw: bytes) -> bytes:
        if len(raw) > MAX_MESSAGE:
            return encode({"id": None, "ok": False, "error": "message too large"})
        try:
            req = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return encode({"id": None, "ok": False, "error": "not JSON"})
        return encode(self.handle(req))


def encode(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


# ---------------------------------------------------------------- BLE chunking
def chunk(data: bytes, msg_id: int, size: int) -> list:
    body = max(1, size - HEADER)
    parts = [data[i:i + body] for i in range(0, len(data), body)] or [b""]
    if len(parts) > MAX_CHUNKS:
        raise ValueError("message too large for BLE framing")
    return [bytes([msg_id & 0xFF, i, len(parts)]) + p for i, p in enumerate(parts)]


class Reassembler:
    """Collects chunks per msg_id; returns the full message when the last piece arrives. Tolerates
    duplicates and out-of-order delivery; drops half-received messages after `timeout_s`."""

    def __init__(self, timeout_s=10.0, clock=time.monotonic):
        self.timeout_s, self.clock = timeout_s, clock
        self.partial = {}                     # msg_id -> {"count", "parts", "t"}

    def feed(self, raw: bytes):
        now = self.clock()
        for k in [k for k, v in self.partial.items() if now - v["t"] > self.timeout_s]:
            del self.partial[k]
        if len(raw) < HEADER:
            return None
        mid, idx, count = raw[0], raw[1], raw[2]
        if count == 0 or count > MAX_CHUNKS or idx >= count:
            return None
        p = self.partial.get(mid)
        if p is None or p["count"] != count:          # new message (or a reused id with a new shape)
            p = self.partial[mid] = {"count": count, "parts": {}, "t": now}
        p["parts"][idx] = raw[HEADER:]
        p["t"] = now
        if len(p["parts"]) < count:
            return None
        del self.partial[mid]
        return b"".join(p["parts"][i] for i in range(count))
