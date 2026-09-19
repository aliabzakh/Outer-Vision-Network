import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { PrivyProvider } from '@privy-io/react-auth';
import { createSolanaRpc, createSolanaRpcSubscriptions } from '@solana/kit';
import App from './App.jsx';
import './styles.css';

const appId = import.meta.env.VITE_PRIVY_APP_ID;
const rpcUrl = import.meta.env.VITE_SOLANA_RPC || 'https://api.devnet.solana.com';

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
          loginMethods: ['email'],
          appearance: { theme: 'dark', walletChainType: 'solana-only' },
          // every player gets their own Solana wallet on first login: that address is their identity as a creator
          embeddedWallets: { solana: { createOnLogin: 'all-users' }, ethereum: { createOnLogin: 'off' } },
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
        <App />
      </PrivyProvider>
    ) : <Missing />}
  </StrictMode>,
);
