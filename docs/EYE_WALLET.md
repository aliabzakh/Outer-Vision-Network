# Eye wallet

A player who can't use their hands can't tap "Approve" in a wallet. The owner (the player, or a carer)
authorises the headset **once** from their phone. After that the player mints songs and sends tips with
blinks, and only within the limits the phone signed.

```
phone /phone page ──── BLE (Web Bluetooth) or Wi-Fi ────► headset (run.py --wallet)
  Seed Vault via MWA (Seeker/Saga)                          outer_vision/wallet
  or a Privy wallet                                           Wallet: session key + limits ledger
  1. sign delegation  ──────────────────────────────────►      checks signature, challenge, rig, key
  2. send SOL to the headset key (the hard ceiling)            blink menu: song → mint | tip → contact → confirm
  3. watch the live feed ◄──────────────────────────────       every action pushed as an event
  4. revoke ────────────────────────────────────────────►      stops at once, refunds the owner, new key
          └──► marketplace: stop accepting that key (works even if the headset is lost)
```

## What the owner signs

The wallet shows this text in its signing prompt (`delegation.py`, `wallet-messages.js`):

```
Outer Vision: let my headset act for me.
owner: <owner wallet>
headset key: <session key>
rig: outer-vision
allowed: mint, tip
tip size: 0.01 SOL
daily limit: 0.05 SOL
contacts: Mom (<address>), Clinic (<address>)
expires: 2026-09-20T02:00:00Z (1789869600000)
nonce: <the headset's one-time challenge>
```

## Blinks

| Menu | Left eye | Right eye | Both eyes |
|---|---|---|---|
| Song saved (opens by itself) | mint it | send a tip | keep playing |
| Who gets the tip? | contact 1 | contact 2 | cancel |
| "Send 0.01 SOL to Mom?" | not sent | not sent | **send** |

A double blink cancels any menu. Silence also cancels, and in the confirm step silence means "not sent".
Refusals are spoken before the confirm step (for example "today's limit is reached"). Every outcome is
spoken too.

## Guarantees (each one is covered by `tests/test_wallet.py`)

- **Ceiling:** the headset can never spend more than the owner sent it.
- **Tips:**
  - only the fixed tip size, only to the signed contacts, within the daily limit over a rolling 24 h;
  - the amount is reserved before sending, so neither concurrent blinks nor a crash can go over the limit;
  - a failed send frees the reservation; an unconfirmed send still counts.
- **Replay:**
  - the delegation and revoke messages carry the headset's one-time challenge, so neither can be replayed;
  - a failed attempt uses up the challenge too;
  - after a revoke the headset key changes, so an old delegation can't bring the headset back.
- **Hijacking:** while a delegation is active, only its owner can replace it. A new owner has to wait
  until the previous owner's SOL has been returned.
- **Revoke:**
  - spending stops first, then the refund is sent;
  - if the chain is unreachable, the refund stays pending and retries;
  - the marketplace also stops accepting the headset key, and only the owner who revoked it can do that.
- **Mints:** the song goes to the owner, never to the headset key. The marketplace checks both the owner's
  delegation and the headset's signature (`marketplace/server/delegated.js`).
- **Safe crashes:** the Bluetooth helper runs as its own process, so a crash in the Bluetooth stack can't
  stop the instrument.

## Run

```bash
python run.py --source picam --gaze udp --wallet            # add --rig-id, --rpc, --market as needed
cd marketplace && npm run server                            # mints songs (see marketplace/README.md)
cd marketplace && npm run web:https                         # phone opens https://<laptop-ip>:5173/phone
```

- Web Bluetooth and Mobile Wallet Adapter need a secure context, so the phone page is served over HTTPS
  with a self-signed certificate. Accept the warning once.
- On a Solana phone, log in with **wallet**, then pick the Seed Vault wallet. Anywhere else, use email.
- **macOS:** the Bluetooth helper exits until your terminal app is allowed under System Settings →
  Privacy & Security → Bluetooth. Until then the phone connects over Wi-Fi (`https://<laptop>:5173/rig`).
