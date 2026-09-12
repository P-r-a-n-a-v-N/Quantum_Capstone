import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api and /ws to FastAPI so the browser sees a single
// origin. That keeps CORS out of the picture in development and means the
// production build (served by nginx behind the same origin) behaves identically.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
})
