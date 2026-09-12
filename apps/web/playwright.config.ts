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
const authStateFile = process.env.PLAYWRIGHT_AUTH_FILE
const ciMaxFailures = Number.parseInt(process.env.E2E_MAX_FAILURES ?? '5', 10)

/**
 * Performance-optimized Playwright configuration
 * Target: Full test suite completion under 5 minutes
 *
 * Key optimizations:
 * - Reduced global timeout (30s default, override for slow tests)
 * - Granular timeouts for actions, navigation, assertions
 * - Parallel workers with CI-friendly defaults
 * - Fail-fast for faster feedback
 * - Optimized web server startup
 *
 * Flaky Test Detection:
 * - Retries enabled in CI (2 retries) to catch flaky tests
 * - Slow test reporting (tests > 10s flagged)
 * - Blob reporter for CI result caching across runs
 * - HTML reporter tracks test history and flakiness
 */
export default defineConfig({
  testDir: './e2e',
  testIgnore: ['**/geo-domain-smoke.spec.ts'], // Explicit release checks only (test:geo)
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // Retry failed tests in CI for flakiness mitigation and detection
  retries: process.env.CI ? 2 : 0,
  // Use optimal workers: 1 in CI for stability, 50% of cores locally
  workers: process.env.CI ? 1 : '50%',
  // Fail fast in CI for quicker feedback
  maxFailures: process.env.CI ? ciMaxFailures : 0,
  // Multiple reporters for better CI output and flaky test tracking
  // Blob reporter caches results for cross-run comparison
  reporter: process.env.CI
    ? [['github'], ['html'], ['blob']]
    : 'html',
  // Global test timeout (raised for CI stability under heavier load)
  timeout: 60000,
  // Report slow tests as potential flaky candidates (threshold: 10s)
  reportSlowTests: {
    max: 5,
    threshold: 10000,
  },
  expect: {
    // Assertion timeout
    timeout: 5000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
    locale: process.env.E2E_LOCALE || 'zh-CN',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Action timeout for click, fill, etc.
    actionTimeout: 10000,
    // Navigation timeout
    navigationTimeout: 30000,
  },
  projects: [
    // Setup project for authentication
    {
      name: 'setup',
      testMatch: /.*\.setup\.ts/,
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        ...(authStateFile ? { storageState: authStateFile } : {}),
        launchOptions: {
          // CI runners do not expose a physical microphone. Permission is
          // still controlled by each test; this only supplies an audio source.
          args: ['--use-fake-device-for-media-stream'],
        },
      },
      dependencies: ['setup'],
    },
    {
      name: 'chromium-headed',
      use: {
        ...devices['Desktop Chrome'],
        ...(authStateFile ? { storageState: authStateFile } : {}),
        headless: false,
        launchOptions: {
          slowMo: 100,
        },
      },
      dependencies: ['setup'],
    },
    {
      name: 'firefox',
      use: {
        ...devices['Desktop Firefox'],
        ...(authStateFile ? { storageState: authStateFile } : {}),
      },
      dependencies: ['setup'],
    },
    {
      name: 'webkit',
      use: {
        ...devices['Desktop Safari'],
        ...(authStateFile ? { storageState: authStateFile } : {}),
      },
      dependencies: ['setup'],
    },
    // Mobile project for mobile-specific tests
    {
      name: 'mobile',
      use: {
        ...devices['iPhone 12'],
        hasTouch: true,
        ...(authStateFile ? { storageState: authStateFile } : {}),
      },
      dependencies: ['setup'],
    },
  ],
  webServer: useExternalBackend
    ? [
      {
        command: 'pnpm dev --host 127.0.0.1 --port 5173',
        url: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
        reuseExistingServer,
        // Reduced from 120s - dev server should start faster
        timeout: 60000,
      },
    ]
    : [
      {
        command:
          'cd ../server && if [ -f .env.test ]; then cp .env.test .env; fi && if [ -d .venv312 ]; then . .venv312/bin/activate; elif [ -d venv ]; then . venv/bin/activate; elif [ -d .venv ]; then . .venv/bin/activate; fi && AUTH_RATE_LIMIT_ENABLED=false ASYNC_VECTOR_INDEX_ENABLED=false python main.py',
        url: process.env.E2E_API_HEALTH_URL || 'http://127.0.0.1:8000/health',
        reuseExistingServer,
        // Reduced from 120s - server should start faster
        timeout: 60000,
        stdout: 'pipe',
        stderr: 'pipe',
      },
      {
        command: 'pnpm dev --host 127.0.0.1 --port 5173',
        url: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
        reuseExistingServer,
        // Reduced from 120s - dev server should start faster
        timeout: 60000,
      },
    ],
})
