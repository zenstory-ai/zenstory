# Inspect GitHub Actions locally with act

`act` runs selected workflow jobs in Docker. The repository's usual local entry points are [ci.sh and local-gha.sh](README.md); use act when you need to inspect a workflow job itself.

## Setup

Install Docker and act using the [act installation guide](https://nektosact.com/installation/index.html). The checked-in `.actrc` maps `ubuntu-latest` to `node:20-bookworm`, loads root `.env.test`, and chooses `linux/amd64`.

Create a dedicated `.env.test` containing synthetic test settings. Do not copy credentials or a production environment into it. The mapped container image and host must still support each job's actions and services.

## Inspect before running

```bash
# List jobs from the actual CI workflow
act -W .github/workflows/ci.yml -l

# Preview one job without executing it
act -W .github/workflows/ci.yml -j backend-test -n

# Run its pytest matrix locally
act -W .github/workflows/ci.yml -j backend-test

# Inspect the browser workflow separately
act -W .github/workflows/e2e.yml -l
```

`backend-test` is the current three-shard pytest matrix in `ci.yml`; there is no `backend-ci.yml`. Other current workflows include `e2e.yml`, `cli-release.yml`, `promote-production.yml` and `zenstory-online-smoke.yml`. Do not use a bare `act` command as a routine gate: it can select unrelated release or observation workflows.

## Services and diagnosis

Read the selected job's `services` and environment values before choosing a connection address. Current CI test services expose PostgreSQL on host port 5433 and Redis on 6380; the correct address from a container depends on the runner's network mode.

```bash
docker ps
docker logs <container-id>
act -W .github/workflows/ci.yml -j backend-test -v
```

If Docker is unavailable, use the documented Lite commands for the checks they support. For workflow syntax, CI runs `yamllint` over `.github/workflows/` and `.github/actions/`; act execution is not a replacement for the exact GitHub check run.

Local act does not inherit repository secrets, environment approvals or provider metadata. Record the workflow/job, event, matrix and source SHA with the result, and use GitHub's exact-source summaries for merge or release evidence.
