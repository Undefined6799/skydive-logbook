import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vitest config lives in vitest.config.js (TEST-8 — audit
// 2026-04-29). Vitest auto-prefers vitest.config.js when present, so
// keeping the test config separate avoids polluting this dev-server
// config with test-only options.

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    // Forward /api/* from the Vite dev server to the FastAPI backend.
    // This lets the frontend use relative URLs (`/api/v1/jumps`) in
    // both dev and packaged modes — same code, same code path, no CORS.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
