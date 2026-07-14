import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Le frontend appelle l'API en relatif (/api/...) ; le proxy renvoie vers le backend FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
    },
  },
})
