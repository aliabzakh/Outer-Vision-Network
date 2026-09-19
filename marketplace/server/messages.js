// Texts the player's embedded wallet signs. Shared by the server (verifies) and the web app (signs).

// Claim (mint) a song. Human-readable on purpose: Privy shows it in the signing prompt.
export const claimMessage = (song) =>
  `Outer Vision: I composed this song with my eyes.\n` +
  `song: ${song.song_id}\nfingerprint: ${song.fingerprint}\ncaptured: ${song.captured_at} (${song.captured_at_ms})`;

export const pairMessage = (wallet, nonce) =>
  `Outer Vision: pair this rig with my wallet.\nwallet: ${wallet}\nnonce: ${nonce}`;

export const delistMessage = (songId, nonce) => `Outer Vision: delist song ${songId}.\nnonce: ${nonce}`;
