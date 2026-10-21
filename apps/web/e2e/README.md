# Playwright browser tests

Choose a local execution scope, prepare isolated fixtures and inspect the source-bound result. Browser mocks test frontend behavior; real test-service runs test integration. Neither replaces a production provider/source receipt.

## Run locally

From the repository root:

```bash
pnpm e2e:zenstory:local          # SQLite, managed servers, Chromium smoke
pnpm e2e:zenstory:local:default
pnpm e2e:zenstory:local:nightly
pnpm e2e:zenstory:local:release
pnpm e2e:zenstory:local:full
```

These commands call `scripts/ci/ci.sh e2e --lite`. The runner creates a disposable database, seeds standard/admin test users and starts fresh servers on ports 8000/5173. Use an isolated checkout; do not point this harness at an existing writing session. For exact local file lists and test-environment mutations, see [CI scripts](../../../scripts/ci/README.md).

With dependencies and test services already prepared, run from `apps/web`:

```bash
pnpm exec playwright install chromium
pnpm exec playwright test e2e/smoke.spec.ts --project=chromium
pnpm exec playwright test e2e/auth.spec.ts --project=chromium --debug
pnpm exec playwright show-report
pnpm exec playwright show-trace <trace.zip>
```

`playwright.config.ts` owns browser projects, managed-server startup and timeouts. It uses `trace: on-first-retry`, screenshots on failure, two retries and one worker in CI. `PLAYWRIGHT_EXTERNAL_BACKEND=1` leaves backend startup to the caller; it does not create test data. Install Chromium for the shared authentication setup even when the selected test project is Firefox.

## Local and GitHub lanes

The local script and GitHub workflow use some of the same names for different selections:

| Scope | Actual selection |
| --- | --- |
| Local `smoke` | `smoke.spec.ts`, Chromium |
| Local `default` | Fixed account/business-flow file list in `run_playwright_suite` |
| Local `nightly` | Versions, skills and skill-flow files with their flags |
| Local `release` | Visual, performance, large-document, concurrent and voice files |
| Local `full` | Chromium suite with the local opt-in flags enabled |
| GitHub push to `main`/`develop`, or manual `default` | Whole selected Chromium suite, eight shards |
| GitHub manual `full` | Eight shards each for Chromium and Firefox, with workflow opt-in flags |
| GitHub schedule or manual `nightly` | Three versions/skills files, Chromium `1/1` |
| GitHub manual `release` | Visual, performance, large-document and concurrent files, Chromium `1/1` |
| GitHub manual `smoke` | `smoke.spec.ts`, Chromium `1/1` |

The current `.github/workflows/e2e.yml` has push, schedule and manual triggers; it has no PR trigger. `CI / ci-summary` and `E2E Tests / e2e-summary` on the exact main commit are the production promotion gates. Shards divide test counts, not measured duration. Fixture prerequisites and feature flags may still skip individual tests, so inspect the report before claiming a scenario was exercised.

Historical 7/14-day promotion targets belong to the [April lane design](../../../docs/archive/design/e2e-lane-governance-2026-04.md). No automatic gate implements those targets. Current execution changes require an explicit script/workflow change and review.

## Fixtures and configuration

| Source | Responsibility |
| --- | --- |
| `config/environments.ts` | API/frontend origins and helper timeout values |
| `config/test-users.ts` | Synthetic standard, skills, admin and pool users; process env overrides defaults |
| `auth.setup.ts` | API login and browser storage state |
| `fixtures/page-objects/` | Existing page interaction helpers |
| `utils/` | API setup, data names, waits and assertions |
| `mocks/` | Reusable MSW handlers and responses; tests must attach mocks explicitly |

Origins come from `E2E_BASE_URL` and `E2E_API_URL`. Credentials and browser storage state are test-only private material; avoid copying real account tokens into fixtures or reports. `PLAYWRIGHT_AUTH_FILE` can isolate the browser auth file per run. Helpers do not create their own users merely because a credential is configured.

The product defaults to the inspiration module disabled. Managed browser servers enable both `INSPIRATIONS_ENABLED` and `VITE_INSPIRATIONS_ENABLED` so library scenarios can run; set both false for a disabled-state check. Managed browser servers also set `VITE_POINTS_ENABLED` and `VITE_REFERRAL_ENABLED` to true (product default false) for the points and referral specs. Reused external servers need matching configuration. Version, skill creation, chat, voice and specialized checks have their own opt-in flags; read the selected spec.

## Write focused regressions

Use an existing page object when it owns the interaction. Prefer accessible names and roles, or stable test IDs where needed; avoid CSS tied to presentation. Register response/event waits before triggering the action, then assert the visible result. An idle network or an arbitrary sleep does not prove an editor save or generation completed.

Keep data unique per test and separate mutable state between parallel workers. Use the actual exports in `utils/api-helpers.ts` (`createProjectViaAPI`, `createFileViaAPI`, `cleanupTestData`), with the current request shape; do not invent helper methods or assume project creation remains on the dashboard. Close or clean up resources in fixtures.

Preserved selector comparison:

```typescript
// Good - uses testid
await page.getByTestId('login-submit').click();

// Avoid - fragile CSS selectors
await page.locator('button.btn-primary').click();
```

Name mocked browser tests with `mocked` in the filename. MSW handler exports do not automatically intercept Playwright pages; attach the relevant handler or `page.route` setup in the test. A streamed AI mock must use the actual SSE event schema. Keep live models and payments out of normal regression runs.

## Debug failures

Inspect the earliest failing action, locator scope, fixture state and response. A 401 needs a valid seeded test account and the intended origin; a connection refusal needs the correct local service. Avoid increasing timeouts or changing assertions before identifying the failed condition.

```bash
# From apps/web
DEBUG=pw:api pnpm exec playwright test e2e/auth.spec.ts --project=chromium
pnpm exec playwright show-report
```

Reports and trace files can contain synthetic account data and manuscript fixtures; keep verification artifacts outside Git. GitHub reports are uploaded per browser/matrix job; use the exact run and artifact names from the workflow.

## Visual, voice and domain checks

Visual comparisons require an intentional, reviewed baseline. Read the rendered change before updating snapshots; do not lower thresholds to hide a regression. Playwright's `maxDiffPixelRatio` is a fraction (`0.01` means 1%).

Preserved dynamic-content comparison:

```typescript
// Bad - dynamic timestamps cause flaky snapshots
await expect(page.locator('.chat')).toHaveScreenshot('chat.png');

// Good - mask dynamic content
await expect(page.locator('.chat')).toHaveScreenshot('chat.png', {
  mask: [page.locator('.timestamp'), page.locator('.user-avatar')],
});
```

Wait for the relevant page state and fonts rather than an arbitrary buffer. Keep viewport, theme and fixture data consistent. A DOM assertion does not replace keyboard/focus testing or visual review.

Voice codec checks have a separate [fixture guide](fixtures/audio/README.md) and `playwright.voice.config.ts`; they do not prove physical microphone or native Safari permission behavior. Organization/domain checks use `playwright.geo.config.ts` and the [domain guide](../../../docs/ops/domain-migration.md). Online production checks and paid-provider calls require their own explicit scope.

For release authorization, source binding and migration/drain requirements, use [the release contract](../../../docs/ops/release-ci-cd.md).
