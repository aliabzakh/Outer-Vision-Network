# Eye Songs: Solana marketplace for songs played on Outer Vision

```
run.py ──lock events──► outer_vision/song.py ──songs/<ms>_<id>.json──► server (Express) ──► Solana devnet
                         layout + path fingerprint                      verify, mint, escrow      Metaplex Core assets
                                                                           ▲
                                     web app (Vite + React + Privy) ───────┘  email login → embedded Solana wallet
```

## Uniqueness

1. **Primary: where things were, relative to the player.** The world camera is fixed to the head, so each
   object's image position is a bearing from the player's face. A song stores the whole table layout at its
   first note and the bearing of every note played. `fingerprint = sha256(layout_hash, path_hash)`, binned to
   3° and 10 cm so tracker jitter doesn't change it.
2. **Secondary: time.** `song_id = sha256(fingerprint, captured_at_ms)`. Among captures that share a
   fingerprint, only the **earliest** can be minted. Once one is minted, that fingerprint is closed. The mint's
   on-chain block time then anchors the claim. The fingerprint, the layout/path hashes and the capture time
   are stored on-chain as Core `Attributes`.

The server recomputes every hash from the song's own data (`server/song.js`, the same maths as
`outer_vision/song.py`, checked by `npm test`). An edited song file is refused.

## Identity: Privy embedded wallets

Players log in with email; Privy creates a Solana wallet for them, and its address is their identity.
- **Pair:** the player signs a pairing message. From then on the rig's songs belong to that wallet.
- **Claim:** the player signs the song's fingerprint and timestamp. The server checks the signature and mints
  the Core asset to them, with them as the 100% royalty creator (5%).
- **List:** the seller signs a transfer into the marketplace escrow. The marketplace pays the fee, so a new
  wallet needs no SOL to mint or list.
- **Buy:** one transaction does both *SOL buyer → seller* and *song escrow → buyer*, so both happen or neither
  does.

## Run

```bash
cd marketplace
npm install
cp .env.example .env          # then fill VITE_PRIVY_APP_ID
npm run keygen                # marketplace keypair in .keys/ (gitignored); tries a devnet airdrop
npm run server                # API on :8787, reads ../songs
npm run web                   # http://localhost:5173
```

If the airdrop is rate-limited, send devnet SOL to the printed address from https://faucet.solana.com.
Each mint costs about 0.004 SOL of rent plus fees, paid by the marketplace.

**Privy setup (dashboard.privy.io):** create an app, enable **Email** login and **Solana** embedded wallets,
and add `http://localhost:5173` to allowed origins. Copy the App ID into `VITE_PRIVY_APP_ID`.

Then play a phrase on the headset (`python run.py ...`). After 8 s of silence it appears under
"Recorded on the headset".

Shortcuts are listed in `../COMPROMISES.md` (#30–33).
