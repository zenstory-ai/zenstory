import { defineConfig, devices } from '@playwright/test'

const noProxy = [...new Set(
  `${process.env.NO_PROXY ?? ''},${process.env.no_proxy ?? ''},127.0.0.1,localhost`
    .split(',').map((entry) => entry.trim()).filter(Boolean),
)].join(',')
process.env.NO_PROXY = noProxy
process.env.no_proxy = noProxy

// Isolated native-codec regression: no backend, login, Tencent call or existing Chrome session.
export default defineConfig({
  testDir: './e2e',
  testMatch: 'voice-audio.browser.ts',
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5197',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        permissions: ['microphone'],
        launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
      },
    },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5197 --strictPort',
    url: 'http://127.0.0.1:5197/e2e/fixtures/voice-audio.html',
    reuseExistingServer: false,
  },
})
