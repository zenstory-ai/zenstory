# Development and maintenance scripts

Run these commands from the repository root. Use Node.js 20.19+ or 22.12+ and pnpm 10 (CI uses 10.29.3); the backend uses a Python 3.12 or 3.13 project environment. Keep the root `pnpm-lock.yaml` as the workspace lockfile.

## Choose an entry point

| Directory | Purpose |
| --- | --- |
| `dev/` | Start local Compose or deliberately remove its containers and volumes |
| `ci/` | Local lint/test/browser orchestration and release-control checks |
| `quality/` | Selected static and translation checks |
| `github/` | Inspect GitHub Actions and create PRs with `gh` |
| `wait-for-services.sh` | Check local test-service readiness |

## Local checks

```bash
# Lint and tests, using Docker test services
pnpm gate

# Lint and tests in SQLite mode
pnpm gate:lite

# Include browser checks (uses Docker)
pnpm gate:e2e

# Local browser smoke, with managed SQLite backend/frontend
pnpm e2e:zenstory:local
```

The local runner and GitHub workflows cover different scopes. Before using the runner, read the [CI guide](ci/README.md): some modes write backend test environment files or replace `.env`, and Lite E2E starts fresh servers on ports 8000/5173. Use an isolated test checkout. Production releases follow [the release contract](../docs/ops/release-ci-cd.md).

## Docker Compose

```bash
./scripts/dev/docker-up.sh     # build and start local Compose
docker compose down           # stop, retaining named volumes
```

`scripts/dev/docker-down.sh` runs `docker compose down -v`: it also deletes named volumes. Use it only for an intended data reset with any needed backup. Deployment setup and data locations are described in [Docker Compose](../docs/docker-compose.md).

## Quality checks

```bash
./scripts/quality/check-quality.sh backend
./scripts/quality/check-quality.sh frontend
./scripts/quality/check-quality.sh security
./scripts/quality/check-quality.sh i18n
```

The quality helper skips missing optional backend tools with warnings; its success alone does not prove all CI checks ran. See [quality commands](quality/README.md) for exact coverage.

## GitHub helpers

```bash
./scripts/github/gh-utils.sh logs <run-id>
./scripts/github/gh-utils.sh watch
```

The PR helper creates a PR with a generic body. For a reviewable change description, use `gh pr create --body-file <file>` or edit the PR body afterwards. See [GitHub helpers](github/README.md) and [optional act usage](ci/README-LOCAL-CI.md).
