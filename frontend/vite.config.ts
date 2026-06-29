import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// ESM 沒有 __dirname；從 import.meta.url 還原專案目錄給 rollup input 用。
const root = fileURLToPath(new URL('.', import.meta.url))

// VITE_API_TOKEN 由 backend lifespan 自動寫到專案根目錄的 .env.local
// （也可以手動在 frontend/.env.local 設定）。Proxy 把它注入到每個 /api
// request 與 ws upgrade 的 X-Local-Token header，前端業務 code 完全不用感知。
export default defineConfig(({ mode }) => {
  // Vite 預設只讀 frontend/ 內的 .env*；這裡再從專案根目錄補讀一份，
  // 讓 backend 自動寫的 ../.env.local 直接生效。
  const env = {
    ...loadEnv(mode, '../', ''),
    ...loadEnv(mode, '.', ''),
  }
  const token = env.VITE_API_TOKEN || ''

  return {
    plugins: [react(), tailwindcss()],
    // 多頁輸出：index.html = 完整操作者 app，chat.html = 純對話 kiosk。
    // 兩頁共用 hash 過的 bundle，FastAPI 之後從 /assets 服務。
    build: {
      rollupOptions: {
        input: {
          main: `${root}index.html`,
          chat: `${root}chat.html`,
        },
      },
    },
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          ws: true,
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (token) proxyReq.setHeader('X-Local-Token', token)
            })
            proxy.on('proxyReqWs', (proxyReq) => {
              if (token) proxyReq.setHeader('X-Local-Token', token)
            })
          },
        },
        // 產品圖由 backend serve（住 knowledge_base/product_images）。非 /api、
        // 不過 LocalTokenMiddleware，純公開靜態，故不需帶 token。
        '/product_images': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
