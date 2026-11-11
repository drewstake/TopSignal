/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    setupFiles: ['./src/test/setupTests.ts'],
    // Upper bound for slow shared CI runners; a genuine hang still fails.
    testTimeout: 15_000,
  },
})
