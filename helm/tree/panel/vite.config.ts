import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Production — статика за Caddy, node-процесса нет (ТЗ §10.5.2).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    // Без sourcemap в проде: карта исходников на публичном домене отдаёт
    // структуру приложения любому, кто откроет devtools.
    sourcemap: false,
    target: 'es2022',
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/auth': 'http://127.0.0.1:8080',
    },
  },
})
