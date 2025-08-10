import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ts-userapi': {
        target: 'https://userapi.topstepx.com',
        changeOrigin: true,
        secure: true,
        rewrite: (p) => p.replace(/^\/ts-userapi/, ''),
      },
    },
  },
})
