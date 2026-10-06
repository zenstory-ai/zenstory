import { defineConfig } from '@playwright/test';
import path from 'node:path';

const evidence = process.env.EDITOR_REVIEW_EVIDENCE;
const baseURL = process.env.EDITOR_REVIEW_BASE_URL;
const evidenceRoots = [
  '/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m05-browser-review',
  '/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m05-late-save-repair',
];
if (!evidence || !evidenceRoots.some(root => path.resolve(evidence).startsWith(root + '/')) || !baseURL || !/^http:\/\/127\.0\.0\.1:\d+$/.test(baseURL)) {
  throw new Error('Provide an owned local origin and evidence directory');
}

export default defineConfig({
  testDir: './e2e',
  testMatch: 'editor-review-mocked.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 7_000 },
  outputDir: path.join(evidence, 'test-results'),
  reporter: [['line'], ['json', { outputFile: path.join(evidence, 'playwright-results.json') }]],
  use: {
    baseURL,
    viewport: { width: 1440, height: 900 },
    serviceWorkers: 'block',
    launchOptions: {
      executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      args: ['--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
    },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
});
