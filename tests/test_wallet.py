"""Eye wallet: delegation, limits, revocation, replay, wire format, phone link, blink menu.
Cross-language checks (JS message builders, web3.js transaction bytes) run when marketplace/node_modules exists."""
import base64
import json
import random
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from nacl.signing import SigningKey, VerifyKey

from outer_vision import config
from outer_vision.menu import Menu
from outer_vision.wallet import http_link, policy
from outer_vision.wallet.bridge import MenuBridge
from outer_vision.wallet.delegation import Delegation, DelegationError, revoke_message, sol
from outer_vision.wallet.link import HEADER, MAX_CHUNKS, Link, Reassembler, chunk
from outer_vision.wallet.policy import FEE_LAMPORTS, Wallet, WalletError, claim_message
from outer_vision.wallet.tx import (b58decode, b58encode, compact_u16, pubkey, signed_transaction,
                                    transfer_message)

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "marketplace"
HAVE_NODE = shutil.which("node") is not None and (MARKET / "node_modules").exists()
CFG = config.load(None)
SOL = 1_000_000_000
T0 = 1_790_000_000.0                      # fake wall clock (seconds)


def addr(key: SigningKey) -> str:
    return b58encode(bytes(key.verify_key))


def sign_b64(key: SigningKey, text: str) -> str:
    return base64.b64encode(key.sign(text.encode()).signature).decode()


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class FakeRpc:
    """Ledger of balances; checks every transaction's signature and applies transfers."""

    def __init__(self):
        self.balances, self.sent = {}, []
        self.fail_send = self.fail_balance = False
        self.confirm_ok = True
        self.send_delay = 0.0
        self.lock = threading.Lock()

    def blockhash(self):
        return bytes(range(32))

    def balance(self, a):
        if self.fail_balance:
            raise WalletError("Solana RPC unreachable (test)")
        return self.balances.get(a, 0)

    def send(self, tx):
        if self.fail_send:
            raise WalletError("Solana RPC unreachable (test)")
        time.sleep(self.send_delay)
        assert tx[0] == 1
        sig, msg = tx[1:65], tx[65:]
        sender, recipient = msg[4:36], msg[36:68]
        VerifyKey(sender).verify(msg, sig)                 # raises if the headset signed wrong
        lamports = struct.unpack("<IQ", msg[-12:])[1]
        s, r = b58encode(sender), b58encode(recipient)
        with self.lock:
            assert self.balances.get(s, 0) >= lamports + FEE_LAMPORTS, "overdraft"
            self.balances[s] -= lamports + FEE_LAMPORTS
            self.balances[r] = self.balances.get(r, 0) + lamports
            self.sent.append({"from": s, "to": r, "lamports": lamports})
        return b58encode(sig)

    def confirm(self, sig, timeout=30.0):
        return self.confirm_ok


class Env:
    """A wallet in a temp dir, an owner, two contacts, a fake chain."""

    def __init__(self, **kw):
        self.dir = tempfile.mkdtemp()
        self.clock, self.rpc = Clock(), FakeRpc()
        self.owner, self.other = SigningKey.generate(), SigningKey.generate()
        self.mom, self.clinic = addr(SigningKey.generate()), addr(SigningKey.generate())
        self.logs = []
        self.w = self.make()

    def make(self):
        return Wallet(self.dir, rpc=self.rpc, market_url="http://market.test", rig_id="rig-1", clock=self.clock,
                      log=self.logs.append)

    def fields(self, owner=None, **over):
        owner = owner or self.owner
        f = {"owner": addr(owner), "session": self.w.address, "rig": "rig-1", "actions": ["mint", "tip"],
             "tip_lamports": SOL // 100, "daily_lamports": SOL // 20,
             "contacts": [{"label": "Mom", "address": self.mom}, {"label": "Clinic", "address": self.clinic}],
             "expires_ms": int((self.clock.t + 3600) * 1000), "nonce": self.w.challenge}
        f.update(over)
        return f

    def signed(self, owner=None, signer=None, **over):
        f = self.fields(owner, **over)
        return f, sign_b64(signer or owner or self.owner, Delegation.from_dict(f).message())

    def delegate(self, owner=None, **over):
        f, s = self.signed(owner, **over)
        return self.w.delegate(f, s)

    def revoke(self, owner=None, signer=None, nonce=None):
        owner = owner or self.owner
        nonce = nonce or self.w.challenge
        return self.w.revoke(addr(owner), sign_b64(signer or owner, revoke_message(addr(owner), "rig-1", nonce)), nonce)

    def fund(self, lamports):
        self.rpc.balances[self.w.address] = lamports


# ====================================================================== wire format
class TestTx(unittest.TestCase):
    def test_base58_round_trip(self):
        rnd = random.Random(1)
        for n in range(200):
            b = bytes([0] * rnd.randint(0, 3)) + bytes(rnd.randrange(256) for _ in range(rnd.randint(0, 40)))
            self.assertEqual(b58decode(b58encode(b)), b)
        self.assertEqual(b58encode(bytes(32)), "1" * 32)                 # the System Program id
        self.assertEqual(b58encode(b"\0\0\x01"), "112")

    def test_bad_base58_and_addresses(self):
        for bad in ("0abc", "Il", "O", "abc!"):
            with self.assertRaises(ValueError):
                b58decode(bad)
        with self.assertRaises(ValueError):
            pubkey("abc")                                                   # decodes, but not 32 bytes
        self.assertEqual(len(pubkey(addr(SigningKey.generate()))), 32)

    def test_compact_u16(self):
        for n, want in ((0, b"\0"), (127, b"\x7f"), (128, b"\x80\x01"), (16383, b"\xff\x7f"), (16384, b"\x80\x80\x01")):
            self.assertEqual(compact_u16(n), want)

    def test_transfer_guards(self):
        a, b = bytes(range(32)), bytes(32 * [7])
        with self.assertRaises(ValueError):
            transfer_message(a, a, 5, bytes(32))
        with self.assertRaises(ValueError):
            transfer_message(a, b, 0, bytes(32))
        with self.assertRaises(ValueError):
            transfer_message(a, b, 2 ** 64, bytes(32))

    @unittest.skipUnless(HAVE_NODE, "needs node + marketplace/node_modules")
    def test_transfer_bytes_match_web3js(self):
        cases = []
        rnd = random.Random(7)
        for lamports in (1, 5000, SOL // 100, 123_456_789, SOL, 2 ** 40 + 3):
            s, r = SigningKey.generate(), SigningKey.generate()
            bh = bytes(rnd.randrange(256) for _ in range(32))
            msg = transfer_message(bytes(s.verify_key), bytes(r.verify_key), lamports, bh)
            tx = signed_transaction(msg, s.sign(msg).signature)
            cases.append({"from": addr(s), "to": addr(r), "lamports": lamports, "blockhash": b58encode(bh),
                          "msg": base64.b64encode(msg).decode(), "tx": base64.b64encode(tx).decode()})
        js = r"""
import { PublicKey, SystemProgram, Transaction, VersionedTransaction } from '@solana/web3.js';
const cases = JSON.parse(process.argv[1]);
const out = cases.map((c) => {
  const t = new Transaction({ feePayer: new PublicKey(c.from), recentBlockhash: c.blockhash });
  t.add(SystemProgram.transfer({ fromPubkey: new PublicKey(c.from), toPubkey: new PublicKey(c.to), lamports: c.lamports }));
  const same = Buffer.from(t.serializeMessage()).toString('base64') === c.msg;
  const parsed = VersionedTransaction.deserialize(Buffer.from(c.tx, 'base64'));
  const sigOk = Transaction.from(Buffer.from(c.tx, 'base64')).verifySignatures();
  return same && sigOk && parsed.signatures.length === 1;
});
console.log(JSON.stringify(out));
"""
        out = subprocess.run(["node", "--input-type=module", "-e", js, json.dumps(cases)], cwd=MARKET,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [True] * len(cases))


# ====================================================================== delegation
class TestDelegation(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def test_valid_delegation_activates_and_rotates_the_challenge(self):
        before = self.e.w.challenge
        st = self.e.delegate()
        self.assertTrue(st["active"])
        self.assertNotEqual(self.e.w.challenge, before)
        self.assertEqual(st["delegation"]["owner"], addr(self.e.owner))
        self.assertTrue(self.e.w.allows("tip") and self.e.w.allows("mint"))

    def test_sol_formatting_is_exact(self):
        for lam, want in ((0, "0"), (1, "0.000000001"), (SOL // 100, "0.01"), (SOL, "1"), (3 * SOL // 2, "1.5"),
                          (123_456_789, "0.123456789"), (5 * SOL, "5")):
            self.assertEqual(sol(lam), want)

    def test_signed_by_someone_else(self):
        f, s = self.e.signed(signer=self.e.other)
        with self.assertRaisesRegex(DelegationError, "signature"):
            self.e.w.delegate(f, s)
        self.assertFalse(self.e.w.active())

    def test_tampered_after_signing(self):
        for field, value in (("tip_lamports", SOL // 2), ("daily_lamports", 4 * SOL),
                             ("contacts", [{"label": "Mom", "address": addr(SigningKey.generate())}]),
                             ("actions", ["tip"]), ("expires_ms", int((T0 + 7200) * 1000))):
            e = Env()
            f, s = e.signed()
            f[field] = value
            with self.assertRaises(DelegationError, msg=field):
                e.w.delegate(f, s)
            self.assertFalse(e.w.active())

    def test_garbage_signatures(self):
        for sig in ("", "not base64!!", base64.b64encode(b"short").decode(), base64.b64encode(bytes(64)).decode()):
            f, _ = self.e.signed()
            with self.assertRaises(DelegationError):
                self.e.w.delegate(f, sig)

    def test_stale_or_replayed_nonce(self):
        f, s = self.e.signed()
        self.e.w.delegate(f, s)
        with self.assertRaisesRegex(DelegationError, "stale"):
            self.e.w.delegate(f, s)                         # exact replay
        f2, s2 = self.e.signed(nonce="deadbeefdeadbeef")
        with self.assertRaisesRegex(DelegationError, "stale"):
            self.e.w.delegate(f2, s2)

    def test_failed_attempt_burns_the_challenge(self):
        c = self.e.w.challenge
        f, s = self.e.signed(signer=self.e.other)
        with self.assertRaises(DelegationError):
            self.e.w.delegate(f, s)
        self.assertNotEqual(self.e.w.challenge, c)
        f, s = self.e.signed()                                # fresh challenge works
        f["nonce"] = c
        s = sign_b64(self.e.owner, Delegation.from_dict(f).message())
        with self.assertRaisesRegex(DelegationError, "stale"):
            self.e.w.delegate(f, s)

    def test_wrong_headset_or_rig(self):
        with self.assertRaisesRegex(DelegationError, "different headset"):
            self.e.delegate(session=addr(SigningKey.generate()))
        with self.assertRaisesRegex(DelegationError, "different rig"):
            self.e.delegate(rig="rig-2")

    def test_expiry_bounds(self):
        with self.assertRaisesRegex(DelegationError, "expired"):
            self.e.delegate(expires_ms=int(T0 * 1000))
        with self.assertRaisesRegex(DelegationError, "7 days"):
            self.e.delegate(expires_ms=int((T0 + 8 * 86400) * 1000))
        self.e.delegate(expires_ms=int((T0 + 7 * 86400) * 1000) - 1)
        self.assertTrue(self.e.w.active())

    def test_expires_on_its_own(self):
        self.e.delegate()
        self.e.clock.t += 3601
        self.assertFalse(self.e.w.active())
        with self.assertRaisesRegex(WalletError, "expired"):
            self.e.w.check_tip("Mom")

    def test_shape_validation(self):
        k = addr(SigningKey.generate())
        bad = {
            "unknown action": {"actions": ["mint", "steal"]},
            "unsorted": {"actions": ["tip", "mint"]},
            "duplicate": {"actions": ["mint", "mint"]},
            "empty": {"actions": []},
            "tip without contacts": {"contacts": []},
            "tip over daily": {"tip_lamports": SOL, "daily_lamports": SOL // 2},
            "zero tip": {"tip_lamports": 0},
            "over hard cap": {"tip_lamports": 2 * SOL, "daily_lamports": 5 * SOL},
            "daily over cap": {"daily_lamports": 6 * SOL},
            "negative": {"daily_lamports": -1},
            "three contacts": {"contacts": [{"label": f"C{i}", "address": k} for i in range(3)]},
            "duplicate names": {"contacts": [{"label": "Mom", "address": k}, {"label": "Mom", "address": self.e.mom}]},
            "emoji name": {"contacts": [{"label": "Mom ❤", "address": k}]},
            "long name": {"contacts": [{"label": "A" * 17, "address": k}]},
            "leading space": {"contacts": [{"label": " Mom", "address": k}]},
            "bad address": {"contacts": [{"label": "Mom", "address": "nope"}]},
            "contact is headset": {"contacts": [{"label": "Me", "address": self.e.w.address}]},
            "bad rig": {"rig": "rig 1!"},
        }
        for name, over in bad.items():
            f, s = self.e.signed(**over)
            with self.assertRaises(DelegationError, msg=name):
                self.e.w.delegate(f, s)
        f, s = self.e.signed()
        f["owner"] = f["session"]
        with self.assertRaises(DelegationError):
            self.e.w.delegate(f, s)

    def test_malformed_dicts(self):
        for f in ({}, {"owner": "x"}, dict(self.e.fields(), tip_lamports="lots"), dict(self.e.fields(), contacts=[{"label": "Mom"}])):
            with self.assertRaises(DelegationError):
                self.e.w.delegate(f, "AAAA")

    def test_mint_only_needs_no_contacts(self):
        self.e.delegate(actions=["mint"], contacts=[], tip_lamports=0, daily_lamports=0)
        self.assertTrue(self.e.w.allows("mint"))
        self.assertFalse(self.e.w.allows("tip"))

    def test_another_owner_cannot_hijack(self):
        self.e.delegate()
        with self.assertRaisesRegex(DelegationError, "another wallet"):
            self.e.delegate(owner=self.e.other, contacts=[{"label": "Thief", "address": addr(self.e.other)}])
        self.assertEqual(self.e.w.delegation.owner, addr(self.e.owner))

    def test_same_owner_can_update_limits(self):
        self.e.delegate()
        self.e.delegate(tip_lamports=SOL // 50, daily_lamports=SOL // 10)
        self.assertEqual(self.e.w.delegation.tip_lamports, SOL // 50)

    def test_after_expiry_a_new_owner_may_take_over(self):
        self.e.delegate()
        self.e.clock.t += 3601
        self.e.w.sweep_pending = False
        self.e.delegate(owner=self.e.other)
        self.assertEqual(self.e.w.delegation.owner, addr(self.e.other))

    def test_survives_restart_with_a_fresh_challenge(self):
        self.e.delegate()
        key, challenge = self.e.w.address, self.e.w.challenge
        w2 = self.e.make()
        self.assertEqual(w2.address, key)
        self.assertTrue(w2.active())
        self.assertNotEqual(w2.challenge, challenge)
        self.assertEqual(oct((Path(self.e.dir) / "session_key.json").stat().st_mode & 0o777), "0o600")

    def test_corrupt_state_files_start_fresh(self):
        for name in ("state.json", "ledger.json"):
            (Path(self.e.dir) / name).write_text("{not json")
        w2 = self.e.make()
        self.assertFalse(w2.active())
        self.assertEqual(w2.ledger, [])


# ====================================================================== tips
class TestTips(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.e.delegate()
        self.e.fund(SOL)

    def test_tip_sends_exact_amount_to_the_contact(self):
        ev = self.e.w.tip("Mom")
        self.assertEqual(self.e.rpc.sent, [{"from": self.e.w.address, "to": self.e.mom, "lamports": SOL // 100}])
        self.assertEqual(self.e.rpc.balances[self.e.mom], SOL // 100)
        self.assertTrue(ev["ok"] and ev["url"].endswith("?cluster=devnet"))
        self.assertEqual(self.e.w.ledger[-1]["status"], "sent")

    def test_daily_limit_and_rolling_window(self):
        self.e.delegate(expires_ms=int((T0 + 3 * 86400) * 1000))
        for _ in range(5):                                  # 5 x 0.01 = the 0.05 daily limit
            self.e.w.tip("Clinic")
        with self.assertRaisesRegex(WalletError, "limit"):
            self.e.w.tip("Mom")
        self.assertEqual(len(self.e.rpc.sent), 5)
        self.e.clock.t += 23 * 3600
        with self.assertRaisesRegex(WalletError, "limit"):
            self.e.w.tip("Mom")
        self.e.clock.t += 3601                               # first tips fall out of the 24 h window
        self.e.w.tip("Mom")

    def test_refusals(self):
        with self.assertRaisesRegex(WalletError, "isn't a contact"):
            self.e.w.tip("Stranger")
        e = Env()
        e.delegate(actions=["mint"])
        e.fund(SOL)
        with self.assertRaisesRegex(WalletError, "isn't allowed"):
            e.w.tip("Mom")
        e2 = Env()
        with self.assertRaisesRegex(WalletError, "set up"):
            e2.w.tip("Mom")
        self.assertEqual(self.e.rpc.sent, [])
        self.assertTrue(any(not ev["ok"] for ev in self.e.w.events))

    def test_out_of_sol_reserves_nothing(self):
        self.e.fund(SOL // 100)                              # not enough for tip + fee
        with self.assertRaisesRegex(WalletError, "out of SOL"):
            self.e.w.tip("Mom")
        self.assertEqual(self.e.w.spent_today(), 0)
        self.assertEqual(self.e.w.ledger, [])

    def test_rpc_failure_frees_the_limit(self):
        self.e.rpc.fail_send = True
        with self.assertRaisesRegex(WalletError, "unreachable"):
            self.e.w.tip("Mom")
        self.assertEqual(self.e.w.ledger[-1]["status"], "failed")
        self.assertEqual(self.e.w.spent_today(), 0)
        self.e.rpc.fail_balance = True
        with self.assertRaises(WalletError):
            self.e.w.tip("Mom")

    def test_unconfirmed_still_counts(self):
        self.e.rpc.confirm_ok = False
        ev = self.e.w.tip("Mom")
        self.assertIn("confirming", ev["text"])
        self.assertEqual(self.e.w.spent_today(), SOL // 100)

    def test_concurrent_tips_never_exceed_the_limit(self):
        self.e.rpc.send_delay = 0.02
        errors = []

        def go():
            try:
                self.e.w.tip("Mom")
            except WalletError as ex:
                errors.append(str(ex))
        ts = [threading.Thread(target=go) for _ in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.e.rpc.sent), 5)
        self.assertEqual(len(errors), 7)
        self.assertLessEqual(self.e.w.spent_today(), self.e.w.delegation.daily_lamports)

    def test_limit_survives_restart(self):
        for _ in range(5):
            self.e.w.tip("Mom")
        w2 = self.e.make()
        with self.assertRaisesRegex(WalletError, "limit"):
            w2.check_tip("Mom")

    def test_listener_failure_doesnt_break_the_tip(self):
        self.e.w.listeners.append(lambda ev: 1 / 0)
        got = []
        self.e.w.listeners.append(got.append)
        self.e.w.tip("Mom")
        self.assertEqual(got[-1]["kind"], "tip")


# ====================================================================== revoke
class TestRevoke(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.e.delegate()
        self.e.fund(SOL // 10)

    def test_revoke_stops_and_refunds_the_owner(self):
        old_key = self.e.w.address
        st = self.e.revoke()
        self.assertFalse(st["active"])
        self.assertFalse(st["sweep_pending"])
        self.assertEqual(self.e.rpc.balances[addr(self.e.owner)], SOL // 10 - FEE_LAMPORTS)
        self.assertNotEqual(self.e.w.address, old_key)       # retired key
        with self.assertRaises(WalletError):
            self.e.w.tip("Mom")

    def test_only_the_owner_with_a_fresh_challenge(self):
        with self.assertRaisesRegex(WalletError, "only the wallet"):
            self.e.revoke(owner=self.e.other)
        with self.assertRaisesRegex(WalletError, "signature"):
            self.e.revoke(signer=self.e.other)
        with self.assertRaisesRegex(WalletError, "stale"):
            self.e.revoke(nonce="0123456789abcdef")
        self.assertTrue(self.e.w.active())

    def test_replayed_revoke_is_refused(self):
        nonce = self.e.w.challenge
        sig = sign_b64(self.e.owner, revoke_message(addr(self.e.owner), "rig-1", nonce))
        self.e.w.revoke(addr(self.e.owner), sig, nonce)
        self.e.delegate()
        with self.assertRaisesRegex(WalletError, "stale"):
            self.e.w.revoke(addr(self.e.owner), sig, nonce)
        self.assertTrue(self.e.w.active())

    def test_old_delegation_cannot_be_replayed_after_revoke(self):
        f, s = self.e.signed()
        self.e.revoke()
        f["nonce"] = self.e.w.challenge                       # even with a fresh nonce, the key changed
        with self.assertRaises(DelegationError):
            self.e.w.delegate(f, s)

    def test_rpc_down_during_revoke_still_stops_spending(self):
        self.e.rpc.fail_balance = True
        with self.assertRaises(WalletError):
            self.e.revoke()
        self.assertFalse(self.e.w.active())
        self.assertTrue(self.e.w.sweep_pending)
        with self.assertRaisesRegex(DelegationError, "returned"):
            self.e.delegate(owner=self.e.other)
        self.e.rpc.fail_balance = False
        st = self.e.w.sweep()
        self.assertFalse(st["sweep_pending"])
        self.assertEqual(self.e.rpc.balances[addr(self.e.owner)], SOL // 10 - FEE_LAMPORTS)
        self.assertTrue(self.e.make().sweep_pending is False)    # persisted

    def test_unconfirmed_sweep_retries(self):
        self.e.rpc.confirm_ok = False
        st = self.e.revoke()
        self.assertTrue(st["sweep_pending"])
        self.e.rpc.confirm_ok = True
        self.assertFalse(self.e.w.sweep()["sweep_pending"])

    def test_empty_wallet_sweep_just_rotates(self):
        self.e.fund(0)
        st = self.e.revoke()
        self.assertFalse(st["sweep_pending"])
        self.assertEqual(self.e.rpc.sent, [])

    def test_revoke_after_expiry(self):
        self.e.clock.t += 7200
        self.e.revoke()
        self.assertEqual(self.e.rpc.balances[addr(self.e.owner)], SOL // 10 - FEE_LAMPORTS)

    def test_owner_can_reauthorise_the_new_key(self):
        self.e.revoke()
        self.e.delegate()
        self.assertTrue(self.e.w.active())


# ====================================================================== mint
class TestMint(unittest.TestCase):
    SONG = {"song_id": "a" * 64, "fingerprint": "b" * 64, "captured_at": "2026-09-19T18:00:43Z",
            "captured_at_ms": 1789840843974}

    def setUp(self):
        self.e = Env()
        self.posts = []
        self.reply = {"asset": "Asset1", "mint_sig": "Sig1", "asset_url": "https://x"}
        self.orig = policy.post_json

        def fake(url, body, timeout=60.0):
            self.posts.append((url, body))
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        policy.post_json = fake

    def tearDown(self):
        policy.post_json = self.orig

    def test_mint_is_signed_by_the_headset_for_the_owner(self):
        self.e.delegate()
        ev = self.e.w.mint(self.SONG)
        url, body = self.posts[0]
        self.assertEqual(url, "http://market.test/api/claim")
        self.assertEqual(body["wallet"], addr(self.e.owner))
        self.assertEqual(body["session"], self.e.w.address)
        VerifyKey(pubkey(body["session"])).verify(claim_message(self.SONG).encode(), base64.b64decode(body["signature"]))
        VerifyKey(pubkey(body["wallet"])).verify(Delegation.from_dict(body["delegation"]).message().encode(),
                                                 base64.b64decode(body["delegation_signature"]))
        self.assertEqual(ev["asset"], "Asset1")

    def test_mint_refusals(self):
        with self.assertRaisesRegex(WalletError, "set up"):
            self.e.w.mint(self.SONG)
        self.e.delegate(actions=["tip"])
        with self.assertRaisesRegex(WalletError, "isn't allowed"):
            self.e.w.mint(self.SONG)
        self.assertEqual(self.posts, [])

    def test_marketplace_error_is_reported(self):
        self.e.delegate()
        self.reply = WalletError("duplicate: same layout")
        with self.assertRaisesRegex(WalletError, "duplicate"):
            self.e.w.mint(self.SONG)
        self.assertEqual(self.e.w.ledger[-1]["status"], "failed")

    def test_daily_mint_cap(self):
        self.e.delegate()
        for _ in range(policy.MINTS_PER_DAY):
            self.e.w.mint(self.SONG)
        with self.assertRaisesRegex(WalletError, "too many"):
            self.e.w.mint(self.SONG)


# ====================================================================== phone link
class TestLink(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.link = Link(self.e.w)

    def req(self, **r):
        return json.loads(self.link.handle_bytes(json.dumps(r).encode()))

    def test_ops(self):
        self.assertEqual(self.req(id=1, op="ping")["result"]["rig"], "rig-1")
        st = self.req(id=2, op="status")
        self.assertTrue(st["ok"] and st["id"] == 2 and st["result"]["session"] == self.e.w.address)
        f, s = self.e.signed()
        self.assertTrue(self.req(id=3, op="delegate", delegation=f, signature=s)["result"]["active"])
        nonce = self.e.w.challenge
        r = self.req(id=4, op="revoke", owner=addr(self.e.owner), nonce=nonce,
                     signature=sign_b64(self.e.owner, revoke_message(addr(self.e.owner), "rig-1", nonce)))
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.req(id=5, op="sweep")["ok"])

    def test_bad_requests_answer_with_an_error(self):
        for raw, want in ((b"not json", "not JSON"), (b"\xff\xfe", "not JSON"), (b"[1,2]", "op"),
                          (b'{"op": 5}', "op"), (b'{"op": "format_disk"}', "unknown op"),
                          (b'{"op": "delegate"}', "needs"), (b'{"op": "revoke", "owner": "x"}', "needs"),
                          (b"x" * 20000, "too large")):
            r = json.loads(self.link.handle_bytes(raw))
            self.assertFalse(r["ok"])
            self.assertIn(want, r["error"])

    def test_internal_errors_are_contained(self):
        self.e.w.status = lambda **kw: 1 / 0
        r = self.req(op="status")
        self.assertEqual(r["error"], "internal error: ZeroDivisionError")

    def test_chunk_round_trips(self):
        for n in (0, 1, 176, 177, 178, 1000, 5000):
            data = bytes(random.Random(n).randrange(256) for _ in range(n))
            parts = chunk(data, 9, 180)
            self.assertTrue(all(len(p) <= 180 for p in parts))
            r = Reassembler()
            got = [r.feed(p) for p in parts]
            self.assertEqual(got[-1], data)
            self.assertTrue(all(g is None for g in got[:-1]))

    def test_out_of_order_duplicates_and_interleaving(self):
        a, b = b"A" * 900, b"B" * 700
        pa, pb = chunk(a, 1, 100), chunk(b, 2, 100)
        mixed = [pa[3], pb[0], pa[0], pa[0], pb[2]] + pa[1:3] + pb[1:] + pa[4:]
        r = Reassembler()
        done = [m for m in (r.feed(p) for p in mixed) if m is not None]
        self.assertEqual(sorted(done), sorted([a, b]))

    def test_stale_partials_are_dropped(self):
        clock = Clock(0)
        r = Reassembler(timeout_s=5, clock=clock)
        p = chunk(b"x" * 500, 3, 100)
        r.feed(p[0])
        clock.t = 10
        for q in p[1:]:
            self.assertIsNone(r.feed(q))                      # first chunk expired, message incomplete
        self.assertEqual(r.feed(p[0]), b"x" * 500)

    def test_invalid_chunks_are_ignored(self):
        r = Reassembler()
        for raw in (b"", b"\x01\x00", bytes([1, 0, 0]) + b"x", bytes([1, 5, 3]) + b"x", bytes([1, 0, MAX_CHUNKS + 1])):
            self.assertIsNone(r.feed(raw))
        with self.assertRaises(ValueError):
            chunk(b"x" * (MAX_CHUNKS * 10 + 1), 1, 10 + HEADER)

    def test_reused_id_with_new_shape_starts_over(self):
        r = Reassembler()
        r.feed(chunk(b"a" * 300, 4, 100)[0])
        self.assertEqual(r.feed(chunk(b"z", 4, 100)[0]), b"z")


class TestHttpLink(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.srv = http_link.serve(self.e.w, 0, host="127.0.0.1")
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def call(self, path, body=None, method=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data, {"content-type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_status_delegate_and_events(self):
        code, headers, body = self.call("/wallet")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        self.assertEqual(json.loads(body)["result"]["rig"], "rig-1")
        f, s = self.e.signed()
        code, _, body = self.call("/wallet", {"id": 1, "op": "delegate", "delegation": f, "signature": s})
        self.assertTrue(json.loads(body)["ok"])
        _, _, body = self.call("/wallet/events?since=0")
        self.assertEqual(json.loads(body)[-1]["kind"], "delegate")
        _, _, body = self.call(f"/wallet/events?since={int(T0 * 1000) + 1}")
        self.assertEqual(json.loads(body), [])

    def test_errors_and_cors_preflight(self):
        self.assertEqual(self.call("/nope")[0], 404)
        self.assertEqual(self.call("/nope", {"op": "ping"})[0], 404)
        self.assertEqual(self.call("/wallet", method="OPTIONS")[0], 204)
        self.assertEqual(self.call("/wallet", b"x" * 20000)[0], 413)
        self.assertFalse(json.loads(self.call("/wallet", b"garbage")[2])["ok"])
        self.assertEqual(json.loads(self.call("/wallet/events?since=abc")[2]), [])


# ====================================================================== blink menu + bridge
class FakeBridge:
    def __init__(self, mint=True, tip=True, contacts=("Mom", "Clinic"), refuse=None):
        self.i = {"mint": mint, "tip": tip, "contacts": list(contacts), "tip_sol": "0.01"}
        self.refuse, self.done = refuse, []

    def info(self):
        return self.i

    def check_tip(self, label):
        return self.refuse

    def do(self, action):
        self.done.append(action)


class TestWalletMenu(unittest.TestCase):
    def make(self, **kw):
        self.said = []
        self.b = FakeBridge(**kw)
        self.m = Menu(CFG, lambda key, text=None: self.said.append((key, text)), lambda c: None, lambda a: "ok",
                      wallet=self.b)
        return self.m

    def blink(self, side, t=1.0, focus=None):
        self.m.on_gesture({"kind": "long", "side": side}, focus, False, t)

    def test_song_menu_mint(self):
        m = self.make()
        self.assertTrue(m.offer_song({"song_id": "s1"}, 0.0))
        self.assertEqual(m.state, "song")
        self.blink("left")
        self.assertEqual(self.b.done, [{"type": "mint", "song": {"song_id": "s1"}}])
        self.assertFalse(m.active)

    def test_tip_needs_explicit_both_eyes_confirmation(self):
        m = self.make()
        m.offer_song({"song_id": "s1"}, 0.0)
        self.blink("right")
        self.assertEqual(m.state, "tip_pick")
        self.assertEqual(m.options(), {"left": "Mom", "right": "Clinic", "both": "cancel"})
        self.blink("right")
        self.assertEqual(m.state, "confirm")
        self.assertIn("Clinic", self.said[-1][1])
        self.blink("both")
        self.assertEqual(self.b.done, [{"type": "tip", "label": "Clinic"}])

    def test_wink_at_confirmation_sends_nothing(self):
        for side in ("left", "right"):
            m = self.make()
            m.offer_song({"song_id": "s1"}, 0.0)
            self.blink("right")
            self.blink("left")
            self.blink(side)
            self.assertEqual(self.b.done, [])
            self.assertEqual(self.said[-1][0], "not_sent")

    def test_silence_or_double_blink_sends_nothing(self):
        m = self.make()
        m.offer_song({"song_id": "s1"}, 0.0)
        self.blink("right", 1.0)
        self.blink("left", 2.0)
        m.tick(2.0 + CFG["menu"]["timeout_s"] + 0.1)
        self.assertFalse(m.active)
        self.assertEqual(self.said[-1][0], "not_sent")
        m.offer_song({"song_id": "s1"}, 20.0)
        self.blink("right", 21.0)
        self.blink("left", 22.0)
        m.on_gesture({"kind": "double"}, None, False, 23.0)
        self.assertFalse(m.active)
        self.assertEqual(self.b.done, [])

    def test_missing_contact_reprompts(self):
        m = self.make(contacts=("Mom",))
        m.offer_song({"song_id": "s1"}, 0.0)
        self.blink("right")
        self.assertEqual(m.options()["right"], "-")
        self.blink("right")
        self.assertEqual(m.state, "tip_pick")
        self.assertIn("Nobody", self.said[-1][1])

    def test_limit_refusal_is_spoken_before_confirming(self):
        m = self.make(refuse="today's limit is reached")
        m.offer_song({"song_id": "s1"}, 0.0)
        self.blink("right")
        self.blink("left")
        self.assertFalse(m.active)
        self.assertIn("limit", self.said[-1][1])
        self.assertEqual(self.b.done, [])

    def test_disallowed_option_and_keep_playing(self):
        m = self.make(tip=False)
        m.offer_song({"song_id": "s1"}, 0.0)
        self.assertIn("nothing", self.said[-1][1])
        self.blink("right")
        self.assertEqual(m.state, "song")
        self.assertEqual(self.said[-1][0], "wallet_refused")
        self.blink("both")
        self.assertFalse(m.active)
        self.assertEqual(self.b.done, [])

    def test_no_offer_when_inactive_busy_or_no_wallet(self):
        m = self.make(mint=False, tip=False)
        self.assertFalse(m.offer_song({"song_id": "s1"}, 0.0))
        m = self.make()
        m.on_gesture({"kind": "long", "side": "both"}, None, False, 0.0)     # space menu open
        self.assertFalse(m.offer_song({"song_id": "s1"}, 0.1))
        plain = Menu(CFG, lambda *a: None, lambda c: None, lambda a: "ok")
        self.assertFalse(plain.offer_song({"song_id": "s1"}, 0.0))


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.spoken = []
        self.b = MenuBridge(self.e.w, self.spoken.append, background=False)

    def test_info_follows_the_delegation(self):
        self.assertEqual(self.b.info()["tip"], False)
        self.e.delegate()
        self.assertEqual(self.b.info(), {"mint": True, "tip": True, "contacts": ["Mom", "Clinic"], "tip_sol": "0.01"})
        self.e.clock.t += 7200
        self.assertEqual(self.b.info()["mint"], False)

    def test_tip_outcomes_are_spoken(self):
        self.e.delegate()
        self.e.fund(SOL)
        self.b.do({"type": "tip", "label": "Mom"})
        self.assertEqual(self.spoken[-1], "Sent 0.01 SOL to Mom.")
        self.e.fund(0)
        self.b.do({"type": "tip", "label": "Mom"})
        self.assertIn("Not sent", self.spoken[-1])
        self.assertIsNone(self.b.check_tip("Mom"))
        self.assertIn("contact", self.b.check_tip("Bob"))

    def test_busy_and_crash(self):
        self.b.busy.acquire()
        self.b.do({"type": "tip", "label": "Mom"})
        self.assertIn("busy", self.spoken[-1])
        self.b.busy.release()
        self.e.w.tip = lambda label: 1 / 0
        self.e.delegate()
        self.b.do({"type": "tip", "label": "Mom"})
        self.assertIn("Nothing was sent", self.spoken[-1])


# ====================================================================== Python <-> JS agreement
@unittest.skipUnless(HAVE_NODE, "needs node + marketplace/node_modules")
class TestCrossLanguage(unittest.TestCase):
    def test_messages_and_signatures_agree(self):
        rnd = random.Random(3)
        cases = []
        for i in range(40):
            owner, session = SigningKey.generate(), SigningKey.generate()
            n = rnd.randint(0, 2)
            d = Delegation(owner=addr(owner), session=addr(session), rig=f"rig-{i}",
                           actions=sorted(rnd.sample(["mint", "tip"], rnd.randint(1, 2))),
                           tip_lamports=rnd.randint(1, SOL), daily_lamports=rnd.randint(SOL, 5 * SOL),
                           contacts=[{"label": f"Name {j}", "address": addr(SigningKey.generate())} for j in range(n)],
                           expires_ms=rnd.randint(1_700_000_000_000, 1_900_000_000_000), nonce=f"n{rnd.getrandbits(64):x}")
            song = {"song_id": f"{i:064x}", "fingerprint": "f" * 64, "captured_at": "2026-09-19T18:00:43Z",
                    "captured_at_ms": 1789840843974 + i}
            cases.append({"d": d.to_dict(), "py": d.message(), "sig": sign_b64(owner, d.message()),
                          "revoke_py": revoke_message(d.owner, d.rig, d.nonce), "song": song,
                          "claim_py": claim_message(song), "claim_sig": sign_b64(session, claim_message(song))})
        js = r"""
import nacl from 'tweetnacl';
import bs58 from 'bs58';
import { delegationMessage, rigRevokeMessage, sol } from './server/wallet-messages.js';
import { claimMessage } from './server/messages.js';
const cases = JSON.parse(process.argv[1]);
const ok = (msg, sig, who) => nacl.sign.detached.verify(new TextEncoder().encode(msg), Buffer.from(sig, 'base64'), bs58.decode(who));
console.log(JSON.stringify(cases.map((c) => [
  delegationMessage(c.d) === c.py, ok(delegationMessage(c.d), c.sig, c.d.owner),
  rigRevokeMessage(c.d.owner, c.d.rig, c.d.nonce) === c.revoke_py,
  claimMessage(c.song) === c.claim_py, ok(claimMessage(c.song), c.claim_sig, c.d.session),
]).concat([[1, 1e9, 1.5e9, 123456789, 0].map(sol)])));
"""
        out = subprocess.run(["node", "--input-type=module", "-e", js, json.dumps(cases)], cwd=MARKET,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout)
        self.assertEqual(res[:-1], [[True] * 5] * len(cases))
        self.assertEqual(res[-1], [sol(x) for x in (1, SOL, 3 * SOL // 2, 123456789, 0)])


if __name__ == "__main__":
    unittest.main()


# ====================================================================== end to end (in process)
class TestBlinkToChain(unittest.TestCase):
    """Real Menu + MenuBridge + Wallet, fake chain: the whole blink path moves exactly the promised SOL."""

    def setUp(self):
        self.e = Env()
        self.e.delegate()
        self.e.fund(SOL)
        self.spoken = []
        self.bridge = MenuBridge(self.e.w, self.spoken.append, background=False)
        self.m = Menu(CFG, lambda k, text=None: self.spoken.append(text or k), lambda c: None, lambda a: "ok",
                      wallet=self.bridge)

    def blink(self, side, t):
        self.m.on_gesture({"kind": "long", "side": side}, None, False, t)

    def test_song_then_tip_by_blinks(self):
        self.assertTrue(self.m.offer_song({"song_id": "s"}, 0))
        self.blink("right", 1)                      # send a tip
        self.blink("left", 2)                       # Mom
        self.assertEqual(self.e.rpc.sent, [])       # nothing moves before the confirmation
        self.blink("both", 3)                       # confirm
        self.assertEqual(self.e.rpc.sent, [{"from": self.e.w.address, "to": self.e.mom, "lamports": SOL // 100}])
        self.assertEqual(self.spoken[-1], "Sent 0.01 SOL to Mom.")

    def test_limit_reached_mid_session_is_spoken_and_nothing_moves(self):
        for i in range(5):
            self.m.offer_song({"song_id": "s"}, i * 10)
            self.blink("right", i * 10 + 1)
            self.blink("right", i * 10 + 2)
            self.blink("both", i * 10 + 3)
        self.assertEqual(len(self.e.rpc.sent), 5)
        self.m.offer_song({"song_id": "s"}, 100)
        self.blink("right", 101)
        self.blink("left", 102)
        self.assertFalse(self.m.active)
        self.assertIn("limit", self.spoken[-1])
        self.assertEqual(len(self.e.rpc.sent), 5)

    def test_revoked_between_menu_and_confirmation(self):
        self.m.offer_song({"song_id": "s"}, 0)
        self.blink("right", 1)
        self.blink("left", 2)
        self.e.revoke()                             # the phone pulls the plug while the question is open
        paid_back = self.e.rpc.balances[addr(self.e.owner)]
        self.blink("both", 3)
        self.assertIn("Not sent", self.spoken[-1])
        self.assertEqual(self.e.rpc.balances.get(self.e.mom, 0), 0)
        self.assertEqual(self.e.rpc.balances[addr(self.e.owner)], paid_back)


@unittest.skipUnless(HAVE_NODE, "needs node + marketplace/node_modules")
class TestFramingAcrossLanguages(unittest.TestCase):
    def test_python_chunks_reassemble_in_js_and_back(self):
        msg = json.dumps({"id": 1, "op": "delegate", "blob": "x" * 2500}).encode()
        parts = [base64.b64encode(p).decode() for p in reversed(chunk(msg, 42, 180))]
        js = r"""
import { chunk, Reassembler } from './src/phone/framing.js';
const parts = JSON.parse(process.argv[1]).map((p) => Uint8Array.from(Buffer.from(p, 'base64')));
const r = new Reassembler();
let got = null;
for (const p of parts) got = r.feed(p) || got;
const back = chunk(new TextEncoder().encode('{"ok":true,"from":"js"}' + ' '.repeat(600)), 7, 180);
console.log(JSON.stringify({ got: Buffer.from(got).toString('base64'), back: back.map((b) => Buffer.from(b).toString('base64')) }));
"""
        out = subprocess.run(["node", "--input-type=module", "-e", js, json.dumps(parts)], cwd=MARKET,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout)
        self.assertEqual(base64.b64decode(res["got"]), msg)
        r = Reassembler()
        whole = [r.feed(base64.b64decode(p)) for p in res["back"]][-1]
        self.assertEqual(json.loads(whole), {"ok": True, "from": "js"})
