import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/ui/api': 'http://127.0.0.1:18863', '/annotate': 'http://127.0.0.1:18863' } },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
