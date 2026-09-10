import { defineConfig } from '@playwright/test'

// Runs against a live server: `python scripts/serve_daemon.py start --mode replay --port 8791`
export default defineConfig({
  testDir: './e2e',
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.STAMPEDE_URL ?? 'http://127.0.0.1:8791',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
  },
  reporter: [['list']],
})
