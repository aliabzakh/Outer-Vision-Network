// Claims signed by a headset under its owner's delegation (the eye wallet). Pure, so it's unit-tested.
import nacl from 'tweetnacl';
import bs58 from 'bs58';
import { PublicKey } from '@solana/web3.js';
import { claimMessage } from './messages.js';
import { delegationMessage } from './wallet-messages.js';

const fail = (status, message) => Object.assign(new Error(message), { status });

function verifySig(message, signatureB64, wallet) {
  try {
    return nacl.sign.detached.verify(new TextEncoder().encode(message), Buffer.from(signatureB64 || '', 'base64'),
      bs58.decode(wallet));
  } catch {
    return false;
  }
}

function address(a) {
  try { return new PublicKey(a).toBase58(); } catch { throw fail(400, 'bad wallet address'); }
}

/**
 * Who must have signed this claim? The owner (signed on the web page) or, with a delegation, the headset's
 * session key. Throws {status, message} when anything doesn't hold. `revoked`: session -> {owner}.
 */
export function claimSigner(body, owner, song, revoked = {}, now = Date.now()) {
  let signer = owner;
  if (body.delegation !== undefined) {
    const d = body.delegation;
    if (!d || typeof d !== 'object' || !Array.isArray(d.actions) || !Array.isArray(d.contacts)) throw fail(400, 'malformed delegation');
    const session = address(body.session);
    if (d.owner !== owner || d.session !== session) throw fail(403, 'delegation does not match this owner and headset key');
    if (!d.actions.includes('mint')) throw fail(403, 'the owner did not allow minting');
    if (!(Number(d.expires_ms) > now)) throw fail(403, 'the delegation has expired');
    if (revoked[session]?.owner === owner) throw fail(403, 'the owner revoked this headset key');
    if (!verifySig(delegationMessage(d), body.delegation_signature, owner)) throw fail(401, 'owner signature on the delegation does not match');
    signer = session;
  }
  if (!verifySig(claimMessage(song), body.signature, signer)) throw fail(401, 'wallet signature does not match');
  return signer;
}
