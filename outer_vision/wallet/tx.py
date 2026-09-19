"""Just enough of Solana's wire format for the eye wallet: base58 and a signed SystemProgram transfer.

Legacy transaction = compact-u16 signature count, signatures, message:
  header [num_required_sigs, num_readonly_signed, num_readonly_unsigned], account keys, recent blockhash,
  instructions [program index, account indexes, data]. Transfer data = u32 LE 2 + u64 LE lamports.
Checked byte-for-byte against @solana/web3.js in tests/test_wallet.py.
"""
from __future__ import annotations

import struct

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SYSTEM_PROGRAM = bytes(32)
LAMPORTS_PER_SOL = 1_000_000_000


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = ALPHABET.find(ch)
        if i < 0:
            raise ValueError(f"bad base58 character {ch!r}")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def pubkey(s: str) -> bytes:
    """base58 address -> 32 bytes, or ValueError."""
    b = b58decode(s)
    if len(b) != 32:
        raise ValueError(f"not a 32-byte address: {s!r}")
    return b


def compact_u16(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def transfer_message(sender: bytes, recipient: bytes, lamports: int, blockhash: bytes) -> bytes:
    if sender == recipient:
        raise ValueError("sender and recipient are the same account")
    if not 0 < lamports < 2 ** 64:
        raise ValueError("lamports out of range")
    keys = [sender, recipient, SYSTEM_PROGRAM]
    data = struct.pack("<IQ", 2, lamports)
    return (bytes([1, 0, 1]) + compact_u16(len(keys)) + b"".join(keys) + blockhash
            + compact_u16(1) + bytes([2]) + compact_u16(2) + bytes([0, 1]) + compact_u16(len(data)) + data)


def signed_transaction(message: bytes, signature: bytes) -> bytes:
    return compact_u16(1) + signature + message
