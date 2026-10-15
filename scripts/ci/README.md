# Local CI scripts

`ci.sh` orchestrates local lint, tests and browser suites. `local-gha.sh` selects common combinations. The GitHub workflows remain the source for merge and release gates; a local run is evidence for the commands it actually executed.

Run from a clean, isolated test checkout with installed frontend dependencies, a backend virtual environment and disposable data. Docker migrations and Docker E2E overwrite `apps/server/.env` with test settings. Lite E2E replaces listeners on ports 8000/5173. These scripts are local test tools, not production deployment commands.

## ci.sh

```bash
./scripts/ci/ci.sh <command> [--docker|--lite]
```

| Command | Checks |
| --- | --- |
| `all` | Lint, frontend/CLI/backend tests and browser tests |
| `frontend` | Frontend lint and coverage tests |
| `backend` | Backend lint and pytest |
| `lint` | Frontend ESLint and backend Ruff |
| `test` | Frontend, CLI and backend tests, without browser E2E |
| `e2e` | Browser suite selected by `E2E_SUITE` |
| `start` / `stop` | Start test Docker services / remove test services and volumes |
| `help` | Command help |

`--docker` is the default. It uses `apps/server/docker-compose.test.yml` with PostgreSQL on 5433 and Redis on 6380. `--lite` uses `apps/server/test_zenstory.db`; it sets a Redis URL on port 6379 but does not start Redis. Ordinary backend tests mock Redis; cases requiring a real Redis service still need their dedicated setup.

The script selects `.venv312`, `.venv` or `venv` and checks the Python runtime. It sets synthetic model/auth test values. Docker bootstrap creates tables and stamps the disposable schema at Alembic head; it does not test an existing production database upgrade.

```bash
./scripts/ci/ci.sh backend --lite
./scripts/ci/ci.sh frontend
./scripts/ci/ci.sh e2e --lite
```

## Local browser lanes

| `E2E_SUITE` | Local selection |
| --- | --- |
| `smoke` (default) | `e2e/smoke.spec.ts`, Chromium |
| `default` | Fixed account and business-flow list in `run_playwright_suite`, Chromium |
| `nightly` | Versions and skill creation/flow files, with their feature flags |
| `release` | Visual, performance, large-document, concurrent and voice files, with their flags |
| `full` | Chromium suite with the listed opt-in flags enabled |

```bash
E2E_SUITE=default ./scripts/ci/ci.sh e2e --lite
E2E_SUITE=nightly ./scripts/ci/ci.sh e2e --lite
E2E_SUITE=release ./scripts/ci/ci.sh e2e --lite
```

The script seeds test users and enables the optional inspiration module for managed browser servers. To test its disabled state, set both inspiration flags to false. A local lane and the GitHub lane with the same name can select different files; inspect `.github/workflows/e2e.yml` before comparing results.

## local-gha.sh

```bash
./scripts/ci/local-gha.sh
./scripts/ci/local-gha.sh --skip-db-tests
./scripts/ci/local-gha.sh --with-e2e
ZENSTORY_E2E_SUITE=nightly ./scripts/ci/local-gha.sh --with-e2e
```

This wrapper runs lint and tests in Docker mode by default. `--skip-db-tests` chooses Lite mode for those stages, rather than skipping all database-backed tests. `--with-e2e` always invokes Docker E2E, even when combined with `--skip-db-tests`. The wrapper attempts to activate pnpm 10.29.3 through Corepack and prints the version actually used.

## GitHub checks

`.github/workflows/ci.yml` detects changed backend, frontend, CLI and CI paths, then requires the relevant jobs through `ci-summary`. Backend pytest has three shards and a combined 80% coverage gate. `.github/workflows/e2e.yml` owns browser runs and `e2e-summary`; exact-source main CI and E2E are required for production promotion.

The historical 7/14-day suite-promotion criteria are recorded in the [April lane design](../../docs/archive/design/e2e-lane-governance-2026-04.md). They are not implemented as automatic gates. Change execution scope explicitly in the current scripts/workflow and review its fixtures, runtime and failure evidence.

## Other helpers

- `diagnose-gha-failfast.sh <run-id>` or `--sha <commit>` reads check annotations when Actions fail before test execution.
- `changed-path-quick-gate.sh` is an unconnected helper with stale references to deleted workflows. It is not a supported substitute for the current gates.
- `check-workflow-results.mjs` validates CI/E2E result states. Deployment promotion, read-only receipts and staging cutover each have dedicated controls in this directory.

For browser authoring, reports and fixtures, see [the Playwright guide](../../apps/web/e2e/README.md). For production authorization, source receipts, migrations and Worker draining, use [the release contract](../../docs/ops/release-ci-cd.md).
