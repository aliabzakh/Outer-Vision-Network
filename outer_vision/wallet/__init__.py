"""Eye wallet: the headset spends and signs for its owner within limits the owner's phone set.

  phone (Seed Vault / Privy wallet)  --BLE or HTTP-->  rig: link.py -> Wallet (policy.py) -> Solana RPC
       signs a Delegation once                            blink menu asks, Wallet checks + signs

delegation.py  the owner's signed grant: session key, actions, tip size, daily limit, contacts, expiry
policy.py      Wallet: session key, limits ledger, tip/mint/revoke
tx.py          minimal Solana legacy transaction (SystemProgram transfer) + base58
link.py        phone <-> rig protocol (JSON ops, BLE chunk framing); ble.py / http_link.py carry it
"""
