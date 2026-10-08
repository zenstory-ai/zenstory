# Release, CI, and deployment contract

## Repository CI

`CI / ci-summary` remains the required repository check. It is deliberately
fail-closed:

- `detect-changes` must succeed and emit literal `true` or `false` values.
- A relevant job must succeed; it may not be skipped.
- An irrelevant job must be skipped rather than producing unrelated evidence.
- Main runs are not cancelled because provider-native gates need a terminal
  check suite for every pushed commit.

Backend evidence is split into parallel jobs, all required when backend or CI
files change and all skipped otherwise:

- `backend-test` is a three-shard pytest matrix (`api-a-f`, `api-g-z`,
  `other`) whose targets partition `tests/` by construction; each shard writes
  `.coverage.<shard>` and uploads it as an artifact without gating its partial
  total.
- `backend-coverage` runs only after every shard succeeds, combines the shard
  data, enforces the 80% gate with `coverage report --fail-under=80`, and
  produces the `coverage.xml` uploaded to Codecov.
- `backend-integration` runs ruff, the serial PostgreSQL regressions, the flow
  tests, and production Prefect deployment registration.

`E2E Tests` applies the same detector rules. Scheduled and manually dispatched
runs force E2E execution even when there is no meaningful Git diff. Its reviewed
revision was enabled after production-policy approval on 2026-10-03. Activation
is not a passing-test claim: check the latest exact-SHA run and report a pending
or failed E2E result explicitly. The local/mocked lane starts PostgreSQL, Redis,
the backend, and the web
app; it uses test API keys and does not call a paid model provider. Push,
`default` and `full` runs split the whole suite into eight Chromium shards
(`full` also adds Firefox); nightly (scheduled), release and smoke lanes run
their fixed file lists on one Chromium `1/1` job. Playwright shards by test
count, not duration, so a shard's time depends on which slow files fall in its
contiguous range. Main E2E runs are the release critical path: production
promotion waits for both exact-SHA summaries.

## ZenStory CLI release

The website has no invented version. The existing public npm package is an
independent channel:

- package: `zenstory`
- version authority: `apps/cli/package.json`
- tag namespace: `cli-vX.Y.Z`
- release notes: `apps/cli/CHANGELOG.md`

The checked-in `0.2.0` is an **Unreleased candidate**, not a claim that it has
been published. The public registry currently labels `0.1.0` as `latest`; this
repository change does not alter that registry state. Before a future `cli-v0.2.0` tag, move its notes to an exact
dated `## [0.2.0] - YYYY-MM-DD` heading. PR/manual metadata validation
accepts that prepared dated version but remains nonpublishing; it does not require
a misleading duplicate Unreleased candidate marker.

`.github/workflows/cli-release.yml` has two paths:

1. A main-only manual dispatch packages, inventories, installs, and runs
   `zenstory --help` without an API key. It never publishes.
2. A future stable tag additionally requires exact-SHA successful main CI,
   packages once, promotes the artifact by ID and digest, and separates GitHub
   `contents: write` from npm `id-token: write`.

Publishing fails closed unless
`CLI_NPM_PUBLISH_ENABLED` is exactly `true`. Set that repository variable only after the npm owner has verified the Trusted
Publisher binding for repository `zenstory-ai/zenstory` and workflow filename
`cli-release.yml`. Existing versions and GitHub assets are append-only: lookup
errors are not treated as absence, and different bytes are rejected.

## Recurring checks and authorized development

Every three days, inspect SEO, growth, costs, disk and online health without
changing the product or cloud. Update `zenstory-ai/geo` and report the conclusion
on every check, including unchanged results. A check must not edit product code,
merge its main, deploy staging or production, change cloud settings, restart
services, or create a release tag. Record proposed fixes for a separately
authorized development batch. Preserve dirty product and GEO worktrees.

The draft-recovery and upload-storage batch is authorized for staging validation
only. Do not promote it to production. Future production batches need explicit
release authorization; use one coherent PR/push, reuse exact-source passing
checks and avoid redundant Actions runs.

## Hosted delivery

Provider Git integrations remain the only deployment producers. The target
branch contract separates continuous preview from deliberate production
promotion:

| Environment | Provider source branch | Purpose |
| --- | --- | --- |
| Vercel Preview and Railway staging | `main` | Deploy each merged revision into isolated preview API, database, Redis, uploads, Prefect server, and worker resources |
| Vercel Production and Railway production | `release-production` | Deploy only a commit promoted by a `prod-v*` release tag |

The release branch currently has one trusted admin writer. Production tagging
is an operational convention, not a platform-enforced prohibition on direct admin
updates to that branch. Apply ref and tag protection before adding other writers.

The checked-in `Promote Production` workflow is the branch-promotion control;
it is not a second deployment producer. A `prod-v*` tag must resolve to the immutable triggering event commit
reachable from `main` and have successful exact-commit `CI / ci-summary` and
`E2E Tests / e2e-summary` main-push runs. The workflow then requires the
existing `release-production` baseline to be an ancestor of that commit,
rereads the baseline, and requests a non-forced fast-forward update. It exits
after the ref update so Railway **Wait for CI** does not depend on a workflow
that is itself waiting for Railway or Vercel.

Create `release-production` once from the currently deployed production commit
before enabling this flow. Do not let the workflow create a missing branch or
repair a diverged branch: either state is a failed promotion that needs an
operator to reconcile history. The workflow uses only the scoped built-in
`GITHUB_TOKEN`; it does not need Vercel or Railway deployment credentials.

The web routing contract sends `/api/*` on `geo-preview.zenstory.ai`,
`app-preview.zenstory.ai`, and Vercel preview hosts to the preview API origin.
All other hosts keep the production API fallback. Replace the checked-in
preview API origin only after its stable Railway domain has been assigned and
verified.

This repository contract does not prove that either provider has switched its
production branch or isolated every staging resource. Until authenticated
provider readback confirms those settings, continue to report
`PROVIDER_GATE_NOT_VERIFIED` and treat production as using its previously
verified configuration. If provider path filtering skips a deployment because
the promoted commit has no relevant files, retain the existing deployment SHA;
do not force a deployment or claim the promoted SHA is live.

`zenstory Online Smoke` is a manual, read-only, source-bound receipt workflow.
It requires a 40-character deployed source SHA and the exact successful main CI
run ID, checks only the canonical public origins, uses no credentials, and
executes only the trusted main readiness controls (never supplied-source code), and
records `providerSourceBinding: NOT_VERIFIED` until provider deployment metadata
is read back independently. It is post-deployment observability, **not** a
pre-deployment or pre-promotion gate. Its reviewed revision was enabled after
production-policy approval on 2026-10-03. Query the live workflow state rather
than inferring activation or successful execution from this file.

After an exact provider source readback, run a bounded receipt for a known
deployed production commit:

```sh
gh workflow run zenstory-online-smoke.yml --ref main \
  -f source_sha=<40-character-deployed-main-sha> \
  -f ci_run_id=<successful-ci-run-id> \
  -f frontend_origin=https://app.zenstory.ai
```

Do not substitute arbitrary URLs, credentials, a moving `main` reference, or a
"latest successful" run. Record the resulting run ID/attempt and retain the
downloaded receipt alongside the provider deployment IDs and source readback.

## Effect on active users

The API currently has one volume mounted at `/app/chroma_data`. Besides derived
Chroma indexes, it contains material source uploads and feedback screenshots.
Do not detach or reset it as a vector cleanup shortcut. Railway cannot overlap
two deployments using that volume, so health checks do not guarantee uninterrupted
API availability. Vercel's static frontend promotion alone is not proof that
an in-flight API stream will finish.

The API uses `/health/ready` for deployment readiness and a 300-second drain
window, with Uvicorn's graceful shutdown bounded to 295 seconds so the final
usage-ledger flush has time to run. Streams longer than the configured shutdown
window can still be interrupted; retrying a generation automatically can duplicate
work, so do not introduce transparent POST retries. Main updates only staging;
production changes are batched behind release tags. Offline vector maintenance
extends the startup gap and needs a verified offsite backup plus a quiet release
window. Preserve material files and screenshots when compacting Chroma.

For seamless API releases, first migrate those uploaded source files and screenshots
to object storage using verified reads and a reversible data migration. Only
after the API no longer reads its local volume can the old vector volume move
to a private maintenance service and the API use overlapping instances. Object storage is being developed and validated in staging; this does not
authorize migrating production data or removing the production volume.

The production startup entry point is `scripts/start_api.py`. With no maintenance
variables, it replaces itself with Uvicorn. The bounded cleanup runs only when
both `ZENSTORY_VECTOR_MAINTENANCE_OPERATION_ID` and
`ZENSTORY_VECTOR_MAINTENANCE_RECEIPT_JSON` are explicitly configured. It finishes
before Uvicorn starts, preserves a private success marker on the volume, and
fails startup if backup or candidate checks fail. Disable both variables together
after checking the success marker; a restart with the same operation is a no-op.

Railway has deprecated Config as Code for new services, with legacy support
ending on December 1, 2026. Cloud service settings must explicitly retain the
start command, readiness path, and drain window; a checked-in TOML file alone
does not prove those values are active.

SQLite compaction is optional: the wrapper runs it only when free volume space
is at least twice the catalog size, as required by the [SQLite VACUUM guidance](https://www.sqlite.org/lang_vacuum.html).
The wrapper first captures the candidate collections’ exact HNSW segment UUIDs
from the catalog, removes the collections, and rechecks that those UUIDs have no
remaining catalog references. It deletes only those direct UUID directories,
rejecting symlinks and preserving all other files and orphan directories.
If compaction has insufficient space, it records deferred compaction; freed
SQLite pages remain reusable while those specific HNSW files are released.

Prefect process-worker shutdown does not wait for active flow processes in the
pinned 3.6.28 version. Before a production worker release, pause `zenstory-pool`,
confirm there are no pending/running/cancelling/paused flows or processing
ingestion jobs, and only then promote the tag. New submissions queue while the
pool is paused. Confirm the replacement worker heartbeat and resume the pool
only after the old deployment is REMOVED; a longer Railway drain window alone
does not protect flows. An online replacement by itself is insufficient.
Deployment registration invokes each named YAML configuration separately with
its configured pool, because Prefect 3.6.28 ignores CLI overrides in multi-deploy
mode. Staging uses `zenstory-staging-pool`; production retains `zenstory-pool`.
