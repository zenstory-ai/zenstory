import { defineConfig, devices } from '@playwright/test'

const LOCALHOST_NO_PROXY_ENTRIES = ['127.0.0.1', 'localhost']

function ensureLocalNoProxy() {
  const merged = Array.from(
    new Set(
      [process.env.NO_PROXY, process.env.no_proxy]
        .filter(Boolean)
        .flatMap((value) => value!.split(','))
        .map((value) => value.trim())
        .filter(Boolean)
        .concat(LOCALHOST_NO_PROXY_ENTRIES)
    )
  ).join(',')

  process.env.NO_PROXY = merged
  process.env.no_proxy = merged
}

ensureLocalNoProxy()

const useExternalBackend = process.env.PLAYWRIGHT_EXTERNAL_BACKEND === '1'
const reuseExistingServer = process.env.E2E_REUSE_EXISTING_SERVER !== 'false'
const prodPreviewUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:4173'
const backendHealthUrl = process.env.E2E_API_HEALTH_URL || 'http://127.0.0.1:8000/health'

export default defineConfig({
  testDir: './e2e',
  testMatch: /.*prod-bundle-smoke\.spec\.ts/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html']] : 'html',
  timeout: 30000,
  expect: {
    timeout: 10000,
  },
  use: {
    baseURL: prodPreviewUrl,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    actionTimeout: 10000,
    navigationTimeout: 15000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: useExternalBackend
    ? [
      {
        command: 'pnpm preview --host 127.0.0.1 --port 4173 --strictPort',
        url: prodPreviewUrl,
        reuseExistingServer,
        timeout: 60000,
      },
    ]
    : [
      {
        command:
          'cd ../server && if [ -f .env.test ]; then cp .env.test .env; fi && if [ -d .venv312 ]; then . .venv312/bin/activate; elif [ -d venv ]; then . venv/bin/activate; elif [ -d .venv ]; then . .venv/bin/activate; fi && AUTH_RATE_LIMIT_ENABLED=false python main.py',
        url: backendHealthUrl,
        reuseExistingServer,
        timeout: 60000,
        stdout: 'pipe',
        stderr: 'pipe',
      },
      {
        command: 'pnpm preview --host 127.0.0.1 --port 4173 --strictPort',
        url: prodPreviewUrl,
        reuseExistingServer,
        timeout: 60000,
      },
    ],
})
