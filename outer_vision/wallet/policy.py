"""The headset's wallet. It holds a session key that can only do what the owner's delegation allows:

  - tip: send exactly `tip_lamports` to one of the owner's named contacts, within `daily_lamports` per
         rolling 24 h. The session wallet only ever holds what the owner funded it with, so that is the
         hard ceiling even if this code were bypassed.
  - mint: sign a song claim so the marketplace mints the song to the OWNER (never to the session key).
  - revoke (owner-signed, from the phone): stops everything at once, then sweeps the SOL back to the owner.

Every phone request that changes authority must carry the rig's current one-time challenge, so a captured
delegation or revoke can't be replayed. While a delegation is active, only its owner can replace it.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .delegation import Delegation, DelegationError, revoke_message, sol
from .tx import LAMPORTS_PER_SOL, b58decode, b58encode, pubkey, signed_transaction, transfer_message

FEE_LAMPORTS = 5000                  # one signature, legacy transfer
DAY_MS = 24 * 3600 * 1000
MINTS_PER_DAY = 20


class WalletError(Exception):
    pass


def claim_message(song: dict) -> str:
    """Same text as marketplace/server/messages.js claimMessage."""
    return (f"Outer Vision: I composed this song with my eyes.\n"
            f"song: {song['song_id']}\nfingerprint: {song['fingerprint']}\n"
            f"captured: {song['captured_at']} ({song['captured_at_ms']})")


# ---------------------------------------------------------------- network (both replaceable in tests)
class Rpc:
    def __init__(self, url: str, timeout=10.0):
        self.url, self.timeout = url, timeout

    def call(self, method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, body, {"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.load(r)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise WalletError(f"Solana RPC unreachable ({e})") from None
        if "error" in out:
            raise WalletError(f"Solana RPC: {out['error'].get('message', out['error'])}")
        return out["result"]

    def blockhash(self) -> bytes:
        return b58decode(self.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"])

    def balance(self, addr: str) -> int:
        return int(self.call("getBalance", [addr, {"commitment": "confirmed"}])["value"])

    def send(self, tx: bytes) -> str:
        return self.call("sendTransaction", [base64.b64encode(tx).decode(), {"encoding": "base64",
                                                                            "preflightCommitment": "confirmed"}])

    def confirm(self, sig: str, timeout=30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            st = self.call("getSignatureStatuses", [[sig]])["value"][0]
            if st:
                if st.get("err"):
                    raise WalletError(f"transaction failed: {st['err']}")
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return True
            time.sleep(0.5)
        return False


def post_json(url: str, body: dict, timeout=60.0) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", str(e))
        except ValueError:
            msg = str(e)
        raise WalletError(msg) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WalletError(f"marketplace unreachable ({e})") from None


def default_rig_id() -> str:
    return "".join(ch for ch in socket.gethostname().split(".")[0] if ch.isalnum() or ch in "-_")[:32] or "rig"


# ---------------------------------------------------------------- the wallet
class Wallet:
    def __init__(self, state_dir="wallet", rpc=None, market_url=None, rig_id=None, clock=time.time, log=print,
                 explorer_cluster="devnet"):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rpc, self.market_url, self.log, self._clock = rpc, market_url, log, clock
        self.cluster = explorer_cluster
        self.rig = rig_id or default_rig_id()
        self.lock = threading.RLock()
        self.listeners = []                 # fn(event) for every activity event (phone feed, logs)
        self.events = []
        self.key = self._load_key()
        st = self._read("state.json", {})
        self.delegation = Delegation.from_dict(st["delegation"]) if st.get("delegation") else None
        self.delegation_sig = st.get("signature")
        self.last_owner = st.get("last_owner")
        self.sweep_pending = bool(st.get("sweep_pending"))
        self.ledger = self._read("ledger.json", [])
        self.challenge = self._new_challenge()

    # ---------------------------------------------------------------- persistence
    def now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _read(self, name, default):
        p = self.dir / name
        try:
            return json.loads(p.read_text()) if p.exists() else default
        except ValueError:
            self.log(f"[wallet] {p} unreadable; starting it fresh")
            return default

    def _write(self, name, obj, private=False):
        p = self.dir / name
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, p)

    def _load_key(self) -> SigningKey:
        k = self._read("session_key.json", None)
        if k:
            return SigningKey(bytes.fromhex(k["seed"]))
        return self._rotate_key()

    def _rotate_key(self) -> SigningKey:
        key = SigningKey.generate()
        self._write("session_key.json", {"seed": key.encode().hex(),
                                         "address": b58encode(bytes(key.verify_key))}, private=True)
        self.key = key
        return key

    def _save_state(self):
        self._write("state.json", {"delegation": self.delegation.to_dict() if self.delegation else None,
                                   "signature": self.delegation_sig, "last_owner": self.last_owner,
                                   "sweep_pending": self.sweep_pending})

    def _new_challenge(self) -> str:
        self.challenge = secrets.token_hex(12)
        return self.challenge

    # ---------------------------------------------------------------- views
    @property
    def address(self) -> str:
        return b58encode(bytes(self.key.verify_key))

    def active(self) -> bool:
        return self.delegation is not None and self.delegation.expires_ms > self.now_ms()

    def allows(self, action: str) -> bool:
        return self.active() and action in self.delegation.actions

    def spent_today(self) -> int:
        since = self.now_ms() - DAY_MS
        return sum(e["lamports"] for e in self.ledger
                   if e["kind"] == "tip" and e["status"] in ("pending", "sent") and e["t_ms"] > since)

    def mints_today(self) -> int:
        since = self.now_ms() - DAY_MS
        return sum(1 for e in self.ledger if e["kind"] == "mint" and e["status"] in ("pending", "sent") and e["t_ms"] > since)

    def status(self, with_balance=False) -> dict:
        d = self.delegation
        out = {"rig": self.rig, "session": self.address, "challenge": self.challenge, "active": self.active(),
               "delegation": d.to_dict() if d else None, "last_owner": self.last_owner,
               "sweep_pending": self.sweep_pending, "spent_today_lamports": self.spent_today(),
               "remaining_today_lamports": max(0, d.daily_lamports - self.spent_today()) if d else 0,
               "events": self.events[-20:]}
        if with_balance and self.rpc:
            try:
                out["balance_lamports"] = self.rpc.balance(self.address)
            except WalletError as e:
                out["balance_error"] = str(e)
        return out

    def _event(self, kind, text, ok=True, **extra):
        e = {"t_ms": self.now_ms(), "kind": kind, "text": text, "ok": ok, **extra}
        self.events = (self.events + [e])[-50:]
        self.log(f"[wallet] {'' if ok else 'REFUSED '}{kind}: {text}")
        for fn in list(self.listeners):
            try:
                fn(e)
            except Exception as ex:                 # a dead phone link must never break the wallet
                self.log(f"[wallet] listener failed: {ex}")
        return e

    def explorer(self, kind, sig):
        return f"https://explorer.solana.com/{kind}/{sig}?cluster={self.cluster}"

    # ---------------------------------------------------------------- authority (from the phone)
    def delegate(self, fields: dict, signature: str) -> dict:
        with self.lock:
            try:
                d = Delegation.from_dict(fields)
                if d.session != self.address:
                    raise DelegationError("delegation is for a different headset key")
                if d.rig != self.rig:
                    raise DelegationError("delegation is for a different rig")
                if d.nonce != self.challenge:
                    raise DelegationError("stale challenge: ask the headset for a fresh one")
                if self.active() and d.owner != self.delegation.owner:
                    raise DelegationError("the headset already works for another wallet; that owner must revoke first")
                if self.sweep_pending and d.owner != self.last_owner:
                    raise DelegationError("the previous owner's SOL hasn't been returned yet")
                d.verify(signature, self.now_ms())
            except DelegationError as e:
                self._new_challenge()
                self._event("delegate", str(e), ok=False)
                raise
            self.delegation, self.delegation_sig, self.last_owner = d, signature, d.owner
            self._new_challenge()
            self._save_state()
            contacts = ", ".join(c["label"] for c in d.contacts) or "no contacts"
            self._event("delegate", f"working for {d.owner[:6]}: {', '.join(d.actions)}; tip {sol(d.tip_lamports)} SOL, "
                                    f"{sol(d.daily_lamports)} SOL/day; {contacts}")
            return self.status()

    def revoke(self, owner: str, signature: str, nonce: str) -> dict:
        with self.lock:
            try:
                if nonce != self.challenge:
                    raise WalletError("stale challenge: ask the headset for a fresh one")
                expected = self.delegation.owner if self.delegation else self.last_owner
                if not expected or owner != expected:
                    raise WalletError("only the wallet that authorised this headset can revoke it")
                try:
                    VerifyKey(pubkey(owner)).verify(revoke_message(owner, self.rig, nonce).encode(),
                                                    base64.b64decode(signature, validate=True))
                except (BadSignatureError, ValueError, TypeError):
                    raise WalletError("owner signature does not match") from None
            except WalletError as e:
                self._new_challenge()
                self._event("revoke", str(e), ok=False)
                raise
            # stop first, then try to give the money back
            self.delegation, self.delegation_sig = None, None
            self.last_owner, self.sweep_pending = owner, True
            self._new_challenge()
            self._save_state()
            self._event("revoke", "headset stopped acting for its owner")
        return self.sweep()

    def sweep(self) -> dict:
        """Return everything but the fee to the last owner, then retire the key. Safe to retry."""
        with self.lock:
            if not self.sweep_pending or not self.last_owner:
                return self.status()
            if self.rpc is None:
                raise WalletError("no Solana RPC configured")
            bal = self.rpc.balance(self.address)
            sig = None
            if bal > FEE_LAMPORTS:
                msg = transfer_message(pubkey(self.address), pubkey(self.last_owner), bal - FEE_LAMPORTS, self.rpc.blockhash())
                sig = self.rpc.send(signed_transaction(msg, self.key.sign(msg).signature))
                if not self.rpc.confirm(sig):
                    self._event("sweep", "refund sent but not confirmed yet; will retry", ok=False, sig=sig)
                    return self.status()
            self.sweep_pending = False
            self._rotate_key()
            self._save_state()
            self._event("sweep", f"returned {sol(max(0, bal - FEE_LAMPORTS))} SOL to {self.last_owner[:6]}; new headset key",
                        sig=sig, url=self.explorer("tx", sig) if sig else None)
            return self.status()

    # ---------------------------------------------------------------- spending (from the blink menu)
    def check_tip(self, label: str):
        """-> (contact, lamports) or raise WalletError with a reason the menu can speak."""
        if not self.active():
            raise WalletError("the wallet isn't set up" if not self.delegation else "the wallet permission expired")
        d = self.delegation
        if "tip" not in d.actions:
            raise WalletError("tipping isn't allowed")
        c = d.contact(label)
        if c is None:
            raise WalletError(f"{label} isn't a contact")
        if self.spent_today() + d.tip_lamports > d.daily_lamports:
            raise WalletError("today's limit is reached")
        return c, d.tip_lamports

    def tip(self, label: str) -> dict:
        with self.lock:
            try:
                c, lamports = self.check_tip(label)
                if self.rpc is None:
                    raise WalletError("no Solana RPC configured")
                if self.rpc.balance(self.address) < lamports + FEE_LAMPORTS:
                    raise WalletError("the headset wallet is out of SOL")
            except WalletError as e:
                self._event("tip", str(e), ok=False, label=label)
                raise
            entry = {"t_ms": self.now_ms(), "kind": "tip", "label": label, "to": c["address"], "lamports": lamports,
                     "status": "pending", "sig": None}
            self.ledger.append(entry)             # reserved before sending: a crash can't double-spend the limit
            self._write("ledger.json", self.ledger)
        try:
            msg = transfer_message(pubkey(self.address), pubkey(c["address"]), lamports, self.rpc.blockhash())
            entry["sig"] = self.rpc.send(signed_transaction(msg, self.key.sign(msg).signature))
            confirmed = self.rpc.confirm(entry["sig"])
            entry["status"] = "sent"                   # sent even if unconfirmed: never free the limit early
        except WalletError as e:
            entry["status"] = "failed"
            with self.lock:
                self._write("ledger.json", self.ledger)
            self._event("tip", f"to {label} failed: {e}", ok=False, label=label)
            raise
        with self.lock:
            self._write("ledger.json", self.ledger)
        return self._event("tip", f"sent {sol(lamports)} SOL to {label}{'' if confirmed else ' (confirming)'}",
                           label=label, lamports=lamports, sig=entry["sig"], url=self.explorer("tx", entry["sig"]))

    def mint(self, song: dict) -> dict:
        with self.lock:
            try:
                if not self.allows("mint"):
                    raise WalletError("minting isn't allowed" if self.active() else "the wallet isn't set up")
                if self.mints_today() >= MINTS_PER_DAY:
                    raise WalletError("too many mints today")
                if not self.market_url:
                    raise WalletError("no marketplace configured")
            except WalletError as e:
                self._event("mint", str(e), ok=False)
                raise
            d, dsig = self.delegation, self.delegation_sig
            entry = {"t_ms": self.now_ms(), "kind": "mint", "song_id": song["song_id"], "lamports": 0,
                     "status": "pending", "sig": None}
            self.ledger.append(entry)
            self._write("ledger.json", self.ledger)
        body = {"song_id": song["song_id"], "wallet": d.owner, "session": self.address,
                "signature": base64.b64encode(self.key.sign(claim_message(song).encode()).signature).decode(),
                "delegation": d.to_dict(), "delegation_signature": dsig}
        try:
            out = post_json(self.market_url.rstrip("/") + "/api/claim", body)
        except WalletError as e:
            entry["status"] = "failed"
            with self.lock:
                self._write("ledger.json", self.ledger)
            self._event("mint", f"song {song['song_id'][:8]} not minted: {e}", ok=False)
            raise
        entry.update(status="sent", sig=out.get("mint_sig"), asset=out.get("asset"))
        with self.lock:
            self._write("ledger.json", self.ledger)
        return self._event("mint", f"song {song['song_id'][:8]} minted to {d.owner[:6]}", asset=out.get("asset"),
                           sig=out.get("mint_sig"), url=out.get("asset_url"))


__all__ = ["Wallet", "WalletError", "Rpc", "claim_message", "LAMPORTS_PER_SOL", "FEE_LAMPORTS"]
