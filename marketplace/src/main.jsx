import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { PrivyProvider } from '@privy-io/react-auth';
import { toSolanaWalletConnectors } from '@privy-io/react-auth/solana';
import { createSolanaRpc, createSolanaRpcSubscriptions } from '@solana/kit';
import {
  createDefaultAuthorizationCache, createDefaultChainSelector, createDefaultWalletNotFoundHandler, registerMwa,
} from '@solana-mobile/wallet-standard-mobile';
import App from './App.jsx';
import Phone from './phone/Phone.jsx';
import './styles.css';

const appId = import.meta.env.VITE_PRIVY_APP_ID;
const rpcUrl = import.meta.env.VITE_SOLANA_RPC || 'https://api.devnet.solana.com';
const isPhone = location.pathname.replace(/\/$/, '') === '/phone';

// On a Solana phone (Seeker/Saga) this exposes the Seed Vault wallet to the page as a standard wallet,
// so Privy lists it and it signs the delegation. Harmless elsewhere (it only registers on Android).
registerMwa({
  appIdentity: { name: 'Outer Vision', uri: location.origin },
  authorizationCache: createDefaultAuthorizationCache(),
  chains: ['solana:devnet'],
  chainSelector: createDefaultChainSelector(),
  onWalletNotFound: createDefaultWalletNotFoundHandler(),
});

function Missing() {
  return (
    <main className="wrap">
      <h1>Eye Songs</h1>
      <p className="card">Set <code>VITE_PRIVY_APP_ID</code> in <code>marketplace/.env</code> (see <code>.env.example</code>) and restart <code>npm run web</code>.</p>
    </main>
  );
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {appId ? (
      <PrivyProvider
        appId={appId}
        clientId={import.meta.env.VITE_PRIVY_CLIENT_ID || undefined}
        config={{
          loginMethods: isPhone ? ['wallet', 'email'] : ['email'],
          externalWallets: { solana: { connectors: toSolanaWalletConnectors() } },
          appearance: { theme: 'dark', walletChainType: 'solana-only' },
          // every player gets their own Solana wallet on first login: that address is their identity as a creator
          embeddedWallets: { solana: { createOnLogin: 'users-without-wallets' }, ethereum: { createOnLogin: 'off' } },
          solana: {
            rpcs: {
              'solana:devnet': {
                rpc: createSolanaRpc(rpcUrl),
                rpcSubscriptions: createSolanaRpcSubscriptions(rpcUrl.replace(/^http/, 'ws')),
                blockExplorerUrl: 'https://explorer.solana.com/?cluster=devnet',
              },
            },
          },
        }}
      >
        {isPhone ? <Phone /> : <App />}
      </PrivyProvider>
    ) : <Missing />}
  </StrictMode>,
);
