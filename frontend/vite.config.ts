import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式下把 API 请求代理到本地后端；生产构建产物由 FastAPI 静态托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
