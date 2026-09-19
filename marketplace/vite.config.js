import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import basicSsl from '@vitejs/plugin-basic-ssl';

// HTTPS=1 npm run web -> https on the LAN (self-signed) so a phone's Chrome allows Web Bluetooth.
const api = `http://localhost:${process.env.PORT || 8787}`;
const rig = `http://localhost:${process.env.WALLET_PORT || 8765}`;
export default defineConfig({
  plugins: [react(), ...(process.env.HTTPS ? [basicSsl()] : [])],
  server: {
    port: 5173,
    host: !!process.env.HTTPS,
    proxy: {
      '/api': api,
      '/samples': api,
      '/rig': { target: rig, rewrite: (p) => p.replace(/^\/rig/, '') },   // the headset's HTTP phone link
    },
  },
});
