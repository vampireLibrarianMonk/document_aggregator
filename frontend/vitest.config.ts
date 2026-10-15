import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    // Unit tests live under src/. The e2e/ directory is Playwright's; keep it
    // out of Vitest's collection so the two runners don't fight.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
