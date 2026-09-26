import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` serves the UI on :5173 and proxies /api to a locally running
// uvicorn, so the frontend can be worked on without rebuilding the container.
export default defineConfig({
  plugins: [react()],
  // Relative asset URLs: Home Assistant's ingress serves the UI under
  // /api/hassio_ingress/<token>/, so nothing may assume it lives at /.
  base: './',
  server: {
    port: 5173,
    proxy: {
      '/api': process.env.DAPPLE_API || 'http://localhost:8080',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
