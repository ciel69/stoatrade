import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// Сервер нового дашборда (`./tb dash2`, порт 8767) раздаёт собранный dist/ и держит WebSocket /ws.
// В режиме разработки (`npm run dev`, порт 5173) сокет проксируется на него.
export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/ws': { target: 'ws://127.0.0.1:8767', ws: true } },
  },
  build: { outDir: 'dist', sourcemap: false, target: 'es2022' },
  test: { environment: 'jsdom', include: ['src/**/*.test.ts'] },
})
