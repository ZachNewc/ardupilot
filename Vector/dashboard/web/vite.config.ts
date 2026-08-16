import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// The Python server hosts the built app and the WebSocket on one port. During
// `npm run dev`, Vite serves the UI instead and proxies both through to the server.
const SERVER = 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '~tests': fileURLToPath(new URL('../../tests', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/ws': { target: SERVER, ws: true },
      '/api': { target: SERVER },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // three.js is only needed by the 3D World page; keeping it in its own chunk
        // means every other page loads without paying for it.
        manualChunks: { three: ['three'] },
      },
    },
  },
})
