// Creates the marketplace keypair (escrow + fee payer) and asks devnet for SOL. Never commit .keys/.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Connection, Keypair, LAMPORTS_PER_SOL } from '@solana/web3.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const file = path.resolve(root, process.env.MARKET_KEYPAIR || '.keys/marketplace.json');
let kp;
if (fs.existsSync(file)) {
  kp = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(file, 'utf8'))));
  console.log(`keypair exists: ${file}`);
} else {
  kp = Keypair.generate();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(Array.from(kp.secretKey)), { mode: 0o600 });
  console.log(`new keypair: ${file}`);
}
console.log(`address: ${kp.publicKey.toBase58()}`);
const conn = new Connection(process.env.SOLANA_RPC || 'https://api.devnet.solana.com', 'confirmed');
try {
  const sig = await conn.requestAirdrop(kp.publicKey, 2 * LAMPORTS_PER_SOL);
  await conn.confirmTransaction(sig, 'confirmed');
  console.log('airdropped 2 devnet SOL');
} catch (e) {
  console.log(`airdrop refused (${e.message.split('\n')[0]}); fund it at https://faucet.solana.com (devnet)`);
}
console.log(`balance: ${(await conn.getBalance(kp.publicKey)) / LAMPORTS_PER_SOL} SOL`);
