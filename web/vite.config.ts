import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The client module is named `client.ts` on purpose: a proxy prefix `/api` must never collide with a
// client module path (it did once in another project).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8791', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
