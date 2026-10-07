import { defineConfig, devices } from '@playwright/test'

// End-to-end suite: drives the BUILT app (served the same way the a11y audit
// serves it — static files + /api proxy to the live backend) through the real
// browser, asserting the on-screen values the project guides promise.
//
// Prerequisites (on-demand gate, NOT wired into pre-commit/CI):
//   1. the Docker stack is up with SAMPLES_ENABLED=true (docker compose up)
//   2. the app is built (npm run build)
//   3. chromium is installed (npx playwright install chromium)
// Run with: npm run e2e

const PORT = Number(process.env.E2E_PORT || 4600)
const API_TARGET = process.env.E2E_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  testDir: './e2e',
  // The suite mutates shared backend state (projects), so run serially.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: 'e2e/e2e-report.json' }]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 15_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // Serve the built app + proxy /api -> the live backend, via the shared server.
  webServer: {
    command: `node a11y/serve.mjs ${PORT}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    env: {
      E2E_PORT: String(PORT),
      E2E_DIST: 'dist',
      E2E_API_PREFIX: '/api',
      E2E_API_TARGET: API_TARGET,
    },
  },
})
