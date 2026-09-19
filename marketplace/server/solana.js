// Everything on-chain. The marketplace keypair (MARKET_KEYPAIR) is the escrow for listed songs and the
// fee payer for every transaction, so players' embedded wallets never need SOL except to buy.
//
//   mint:  marketplace mints a Metaplex Core asset straight to the creator's wallet; the fingerprint,
//          layout hash and capture time go on-chain as Attributes, the creator as 100% royalty creator.
//   list:  seller signs a Core transfer seller -> escrow (marketplace pays the fee).
//   buy:   ONE transaction: SOL buyer -> seller AND Core transfer escrow -> buyer. Both happen or neither,
//          so neither side can be short-changed. The buyer signs it; the marketplace co-signs as escrow.
import fs from 'node:fs';
import bs58 from 'bs58';
import {
  Connection, Keypair, LAMPORTS_PER_SOL, PublicKey, SystemProgram, TransactionMessage, VersionedTransaction,
} from '@solana/web3.js';
import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { createNoopSigner, generateSigner, keypairIdentity, publicKey } from '@metaplex-foundation/umi';
import { create, mplCore, ruleSet, transferV1 } from '@metaplex-foundation/mpl-core';
import { toWeb3JsInstruction } from '@metaplex-foundation/umi-web3js-adapters';

export class Chain {
  constructor(rpcUrl, keypairPath) {
    if (!fs.existsSync(keypairPath)) {
      throw new Error(`no marketplace keypair at ${keypairPath}: run \`npm run keygen\` first`);
    }
    const secret = Uint8Array.from(JSON.parse(fs.readFileSync(keypairPath, 'utf8')));
    this.kp = Keypair.fromSecretKey(secret);
    this.escrow = this.kp.publicKey;
    this.conn = new Connection(rpcUrl, 'confirmed');
    this.umi = createUmi(rpcUrl).use(mplCore());
    this.umi.use(keypairIdentity(this.umi.eddsa.createKeypairFromSecretKey(secret)));
    this.cluster = rpcUrl.includes('devnet') ? 'devnet' : rpcUrl.includes('mainnet') ? 'mainnet-beta' : 'custom';
  }

  explorer(kind, id) {
    return `https://explorer.solana.com/${kind}/${id}${this.cluster === 'mainnet-beta' ? '' : `?cluster=${this.cluster}`}`;
  }

  async balance(addr) {
    return (await this.conn.getBalance(new PublicKey(addr))) / LAMPORTS_PER_SOL;
  }

  async mintSong(song, creator, uri) {
    const asset = generateSigner(this.umi);
    const owner = publicKey(creator);
    const res = await create(this.umi, {
      asset,
      owner,
      name: `Eye Song ${song.song_id.slice(0, 6)}`,
      uri,
      plugins: [
        { type: 'Attributes', attributeList: [
          { key: 'fingerprint', value: song.fingerprint },
          { key: 'layout_hash', value: song.layout_hash },
          { key: 'path_hash', value: song.path_hash },
          { key: 'captured_at_ms', value: String(song.captured_at_ms) },
          { key: 'notes', value: String(song.notes.length) },
        ] },
        { type: 'Royalties', basisPoints: 500, creators: [{ address: owner, percentage: 100 }], ruleSet: ruleSet('None') },
      ],
    }).sendAndConfirm(this.umi, { confirm: { commitment: 'confirmed' } });
    const sig = bs58.encode(res.signature);
    const tx = await this.conn.getTransaction(sig, { commitment: 'confirmed', maxSupportedTransactionVersion: 0 });
    return { asset: asset.publicKey.toString(), mint_sig: sig, block_time: tx?.blockTime ?? null };
  }

  coreTransfer(asset, from, to) {
    // authority = current owner (a no-op signer here: they sign the finished transaction themselves)
    const authority = from === this.escrow.toBase58() ? this.umi.identity : createNoopSigner(publicKey(from));
    return transferV1(this.umi, { asset: publicKey(asset), authority, payer: this.umi.identity, newOwner: publicKey(to) })
      .getInstructions().map(toWeb3JsInstruction);
  }

  /** Build a v0 transaction paid by the marketplace, signed by it; returns the tx for the user to co-sign. */
  async build(instructions) {
    const { blockhash, lastValidBlockHeight } = await this.conn.getLatestBlockhash('confirmed');
    const msg = new TransactionMessage({ payerKey: this.escrow, recentBlockhash: blockhash, instructions }).compileToV0Message();
    const tx = new VersionedTransaction(msg);
    tx.sign([this.kp]);
    return { tx, lastValidBlockHeight };
  }

  listTx(asset, seller) {
    return this.build(this.coreTransfer(asset, seller, this.escrow.toBase58()));
  }

  buyTx(asset, buyer, seller, lamports) {
    return this.build([
      SystemProgram.transfer({ fromPubkey: new PublicKey(buyer), toPubkey: new PublicKey(seller), lamports }),
      ...this.coreTransfer(asset, this.escrow.toBase58(), buyer),
    ]);
  }

  async delist(asset, seller) {
    const { tx, lastValidBlockHeight } = await this.build(this.coreTransfer(asset, this.escrow.toBase58(), seller));
    return this.send(tx, lastValidBlockHeight);
  }

  async send(tx, lastValidBlockHeight) {
    const sig = await this.conn.sendRawTransaction(tx.serialize());
    const blockhash = tx.message.recentBlockhash;
    const res = await this.conn.confirmTransaction({ signature: sig, blockhash, lastValidBlockHeight }, 'confirmed');
    if (res.value.err) throw new Error(`transaction failed: ${JSON.stringify(res.value.err)}`);
    return sig;
  }

  /** Devnet only: top a wallet up from the marketplace's own balance so a new player can buy. */
  async topUp(addr, sol) {
    const { tx, lastValidBlockHeight } = await this.build([
      SystemProgram.transfer({ fromPubkey: this.escrow, toPubkey: new PublicKey(addr), lamports: Math.round(sol * LAMPORTS_PER_SOL) }),
    ]);
    return this.send(tx, lastValidBlockHeight);
  }
}
