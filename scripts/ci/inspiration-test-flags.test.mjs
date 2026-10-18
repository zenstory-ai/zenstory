import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const action = readFileSync(new URL('../../.github/actions/setup-backend/action.yml', import.meta.url), 'utf8');
const playwright = readFileSync(new URL('../../apps/web/playwright.config.ts', import.meta.url), 'utf8');

for (const configured of [undefined, 'true', 'false', '']) {
  test(`external-backend E2E inspiration flag matches frontend (${String(configured)})`, () => {
    const line = action.match(/^\s*(INSPIRATIONS_ENABLED=.*)$/m)?.[1];
    assert.ok(line, 'The external test backend must explicitly opt in to the browser-test flag');
    const env = { ...process.env };
    delete env.INSPIRATIONS_ENABLED;
    if (configured !== undefined) env.INSPIRATIONS_ENABLED = configured;
    const backendFlag = execFileSync('bash', ['-c', `${line}; printf '%s' "$INSPIRATIONS_ENABLED"`], { env, encoding: 'utf8' });
    assert.equal(backendFlag, configured ?? 'true');
    assert.match(playwright, /const inspirationTestFlag = process\.env\.INSPIRATIONS_ENABLED \?\? 'true'/);
    assert.match(playwright, /VITE_INSPIRATIONS_ENABLED \?\? inspirationTestFlag/);
  });
}


test('browser and API fixtures share the E2E API origin in both server launch modes', () => {
  const environments = readFileSync(new URL('../../apps/web/e2e/config/environments.ts', import.meta.url), 'utf8');
  const expression = "process.env.E2E_API_URL || 'http://127.0.0.1:8000'";
  assert.ok(environments.includes(`apiBaseUrl: ${expression}`));
  assert.ok(playwright.includes(`const e2eApiBaseUrl = ${expression}`));
  assert.equal([...playwright.matchAll(/VITE_API_BASE_URL: e2eApiBaseUrl/g)].length, 2,
    'Both external-backend and local-backend Vite launches must use the fixture API origin');
});
