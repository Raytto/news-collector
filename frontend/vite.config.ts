import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const frontendPort = Number(process.env.FRONTEND_PORT || process.env.PORT) || 5180
const backendPort = process.env.BACKEND_PORT || '8000'
const backendTarget = `http://127.0.0.1:${backendPort}`

export default defineConfig({
  plugins: [react()],
  envPrefix: ['VITE_', 'FRONTEND_'],
  server: {
    port: frontendPort,
    strictPort: true,
    host: true,
    // Allow external access via this hostname
    allowedHosts: ['us.pangruitao.com', 'localhost', '127.0.0.1'],
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        rewrite: (p: string) => p.replace(/^\/api/, '')
      }
    }
  }
})
