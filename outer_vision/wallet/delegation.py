"""The owner's grant to the headset, signed once on their phone (Seed Vault or Privy wallet).

The phone signs a human-readable message (so the wallet's signing screen shows exactly what is granted);
the rig rebuilds that text from the fields and checks the signature, so fields and text can't disagree.
marketplace/server/wallet-messages.js builds the identical text (checked in tests).
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .tx import LAMPORTS_PER_SOL, pubkey

ACTIONS = ("mint", "tip")
MAX_CONTACTS = 2                                   # the blink menu has a left and a right eye
MAX_TIP_LAMPORTS = 1 * LAMPORTS_PER_SOL            # sanity caps, whatever the phone asks for
MAX_DAILY_LAMPORTS = 5 * LAMPORTS_PER_SOL
MAX_LIFETIME_MS = 7 * 24 * 3600 * 1000
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,15}$")   # spoken aloud by the menu
NONCE_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")
RIG_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class DelegationError(ValueError):
    pass


def sol(lamports: int) -> str:
    """Exact decimal SOL from integer lamports (no floats, so Python and JS agree)."""
    whole, frac = divmod(int(lamports), LAMPORTS_PER_SOL)
    f = str(frac).rjust(9, "0").rstrip("0")
    return f"{whole}.{f}" if f else str(whole)


def iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms // 1000))


@dataclass
class Delegation:
    owner: str
    session: str
    rig: str
    actions: list
    tip_lamports: int
    daily_lamports: int
    contacts: list = field(default_factory=list)        # [{"label", "address"}]
    expires_ms: int = 0
    nonce: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Delegation":
        try:
            return cls(owner=str(d["owner"]), session=str(d["session"]), rig=str(d["rig"]),
                       actions=[str(a) for a in d["actions"]], tip_lamports=int(d["tip_lamports"]),
                       daily_lamports=int(d["daily_lamports"]),
                       contacts=[{"label": str(c["label"]), "address": str(c["address"])} for c in d.get("contacts", [])],
                       expires_ms=int(d["expires_ms"]), nonce=str(d["nonce"]))
        except (KeyError, TypeError, ValueError) as e:
            raise DelegationError(f"malformed delegation ({e})") from None

    def to_dict(self) -> dict:
        return {"owner": self.owner, "session": self.session, "rig": self.rig, "actions": list(self.actions),
                "tip_lamports": self.tip_lamports, "daily_lamports": self.daily_lamports,
                "contacts": [dict(c) for c in self.contacts], "expires_ms": self.expires_ms, "nonce": self.nonce}

    def message(self) -> str:
        contacts = ", ".join(f"{c['label']} ({c['address']})" for c in self.contacts) or "none"
        return ("Outer Vision: let my headset act for me.\n"
                f"owner: {self.owner}\n"
                f"headset key: {self.session}\n"
                f"rig: {self.rig}\n"
                f"allowed: {', '.join(self.actions) or 'nothing'}\n"
                f"tip size: {sol(self.tip_lamports)} SOL\n"
                f"daily limit: {sol(self.daily_lamports)} SOL\n"
                f"contacts: {contacts}\n"
                f"expires: {iso(self.expires_ms)} ({self.expires_ms})\n"
                f"nonce: {self.nonce}")

    def contact(self, label: str):
        return next((c for c in self.contacts if c["label"] == label), None)

    def validate(self, now_ms: int):
        """Shape and limits only (no signature). Raises DelegationError."""
        for name in ("owner", "session"):
            try:
                pubkey(getattr(self, name))
            except ValueError:
                raise DelegationError(f"{name} is not a Solana address") from None
        if self.owner == self.session:
            raise DelegationError("owner and headset key must differ")
        if not RIG_RE.match(self.rig):
            raise DelegationError("bad rig id")
        if not NONCE_RE.match(self.nonce):
            raise DelegationError("bad nonce")
        if not self.actions or len(set(self.actions)) != len(self.actions) or any(a not in ACTIONS for a in self.actions):
            raise DelegationError(f"actions must be a non-empty subset of {ACTIONS}")
        if list(self.actions) != sorted(self.actions):
            raise DelegationError("actions must be sorted")
        if self.tip_lamports < 0 or self.daily_lamports < 0:
            raise DelegationError("amounts can't be negative")
        if self.tip_lamports > MAX_TIP_LAMPORTS or self.daily_lamports > MAX_DAILY_LAMPORTS:
            raise DelegationError("limit above the headset's hard cap")
        if len(self.contacts) > MAX_CONTACTS:
            raise DelegationError(f"at most {MAX_CONTACTS} contacts")
        labels = [c["label"] for c in self.contacts]
        if len(set(labels)) != len(labels):
            raise DelegationError("contact names must differ")
        for c in self.contacts:
            if not LABEL_RE.match(c["label"]):
                raise DelegationError(f"contact name {c['label']!r}: letters, digits, spaces, up to 16")
            try:
                pubkey(c["address"])
            except ValueError:
                raise DelegationError(f"contact {c['label']} has a bad address") from None
            if c["address"] == self.session:
                raise DelegationError("a contact can't be the headset itself")
        if "tip" in self.actions:
            if not self.contacts:
                raise DelegationError("tipping needs at least one contact")
            if self.tip_lamports <= 0 or self.daily_lamports < self.tip_lamports:
                raise DelegationError("tip size must be > 0 and within the daily limit")
        if self.expires_ms <= now_ms:
            raise DelegationError("already expired")
        if self.expires_ms > now_ms + MAX_LIFETIME_MS:
            raise DelegationError("expiry more than 7 days away")

    def verify(self, signature_b64: str, now_ms: int):
        """Validate, then check the owner's signature over message(). Raises DelegationError."""
        self.validate(now_ms)
        try:
            sig = base64.b64decode(signature_b64, validate=True)
            VerifyKey(pubkey(self.owner)).verify(self.message().encode(), sig)
        except (BadSignatureError, ValueError, TypeError):
            raise DelegationError("owner signature does not match") from None


def revoke_message(owner: str, rig: str, nonce: str) -> str:
    return f"Outer Vision: stop my headset acting for me and return its SOL.\nowner: {owner}\nrig: {rig}\nnonce: {nonce}"
