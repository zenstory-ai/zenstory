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
runs force E2E execution even when there is no meaningful Git diff. Check the
exact-SHA run and report a pending or failed E2E result explicitly. The local/mocked lane starts PostgreSQL, Redis,
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
been published. Read the public registry before publishing; the checked-in candidate does not
prove which version is currently published or labelled `latest`. Before a future `cli-v0.2.0` tag, move its notes to an exact
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

Staging development authorization does not authorize production. Production
batches need explicit release authorization; use one coherent PR/push, reuse exact-source passing
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
`E2E Tests / e2e-summary` main-push runs (or the explicit exact-source recovery
described below). The workflow then requires the
existing `release-production` baseline to be an ancestor of that commit,
rereads the baseline, and requests a non-forced fast-forward update. It exits
after the ref update so Railway **Wait for CI** does not depend on a workflow
that is itself waiting for Railway or Vercel.

Create `release-production` once from the currently deployed production commit
before enabling this flow. Do not let the workflow create a missing branch or
repair a diverged branch: either state is a failed promotion that needs an
operator to reconcile history. The workflow uses only the scoped built-in
`GITHUB_TOKEN`; it does not need Vercel or Railway deployment credentials.

Main push remains the preferred evidence path. If an exact SHA has **no push
run instance** for a required workflow, an operator may dispatch that existing
workflow on `main` with `expected_source_sha` set to the immutable target SHA.
The source guard rejects a moved branch or another ref. CI forces all four
scopes; E2E requires `default` or `full`, with all eight Chromium shards (and
all eight Firefox shards when present). Promotion validates the authenticated
source-guard job step, successful summary, check suite, workflow, SHA and run
attempt, and records the actual `workflow_dispatch` event. It never calls this
push evidence. Any existing failed, cancelled, pending or ambiguous push run
blocks this fallback; resolve or rerun that push instead. Missing source-guard
evidence, partial shard sets and smoke/release/nightly subsets cannot qualify.

```sh
gh workflow run ci.yml --ref main -f expected_source_sha=<exact-main-sha>
gh workflow run e2e.yml --ref main -f expected_source_sha=<exact-main-sha> -f suite=default
```

The web routing contract sends `/api/*` on `geo-preview.zenstory.ai`,
`app-preview.zenstory.ai`, and Vercel preview hosts to the preview API origin.
All other hosts keep the production API fallback. Replace the checked-in
preview API origin only after its stable Railway domain has been assigned and
verified.

This repository contract does not prove that either provider has switched its
production branch or isolated every staging resource. Until authenticated
provider readback confirms those settings, continue to report
`PROVIDER_GATE_NOT_VERIFIED` and treat production as using its previously
verified configuration. Compare provider watch paths over the **actual deployed
SHA to target SHA**, not just the last CI-only commit. If that cumulative tree
is unchanged, retain the existing deployment SHA and record that fact. If an
undeployed relevant change was skipped, use the existing bound provider Git
integration's deploy-latest-commit/targeted Git-reference action after verifying
its repository, environment, branch and immutable source. Do not redeploy an old
artifact, upload local files, create another project/producer, change the source
branch, or alter mounts/drain parameters to manufacture a deployment. Read back
the new deployment source before claiming the target is live.

`zenstory Online Smoke` is a manual, read-only, source-bound receipt workflow.
It requires a 40-character deployed source SHA and the exact successful main CI
run ID, checks only the canonical public origins, uses no credentials, and
executes only the trusted main readiness controls (never supplied-source code), and
records `providerSourceBinding: NOT_VERIFIED` until provider deployment metadata
is read back independently. It is post-deployment observability, **not** a
pre-deployment or pre-promotion gate. Query the live workflow state and exact
run before claiming successful execution.

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

## Staging ingestion test windows

Hosted staging Prefect Server/Worker may be stopped outside explicitly opened
material-test windows; the API, databases and volumes remain running. Repository
CI/E2E uses local Prefect and does not wake these hosted services. Before testing
hosted material ingestion, follow [the staging test-window runbook](staging-ingestion-test-windows.md).
Before pushing or merging a change that triggers the staging worker Git deployment
(current watch pattern: `/apps/server/**`), open that window first: a new worker
needs the hosted Server and does not wake it automatically. After deployment and
testing, close using the new source/deployment receipt, never revive old code.
This does not authorize production changes or add a deployment workflow.

## Database migrations before promotion

Neither environment runs Alembic on deploy: the Railway API service has no
pre-deploy command, its start command only launches the API, and
`init_db` only creates missing tables (it never adds columns). Code that reads
a new column fails with `UndefinedColumn` on every query that touches the table
until the column exists. The 2026-10-09 audit reproduced this with
`usage_quota.material_trial_used_at`: quota reads and every AI message returned
500 against an unmigrated database.

Before promoting a release whose diff contains `apps/server/alembic/versions/`:

1. Compare the release's Alembic head with the production `alembic_version`
   and list the pending revisions.
2. Confirm each pending revision is safe for the code that is still running:
   additive only (new tables, nullable columns or columns with a server
   default that does not rewrite large tables), no drops or renames. A revision
   that is not backward compatible needs its own expand/contract plan and
   release window.
3. Take and verify a production backup with a client that matches the server
   major version (`pg_dump` 17 for the current Postgres), then run
   `alembic upgrade head` against production with an explicitly checked
   Postgres URL. An empty `DATABASE_URL` makes Alembic fall back to a local
   SQLite file without failing.
4. Read back `alembic_version` and the new columns, record the revision and
   backup receipt, and only then push the `prod-v*` tag. Vercel can serve the
   new frontend minutes before Railway switches the API, so the database must
   already be ready for both the old and the new API.

Staging was created by `create_all` and has no `alembic_version` table. Apply
the same additive DDL there by hand (`ADD COLUMN IF NOT EXISTS ...`) before the
staging API runs the new code. Do not add a startup schema check that fails
`/health/ready`: it would take staging and production down instead of
surfacing a missed step, and it does not protect the old API from the new
frontend.

## Upload storage acceptance

`/health/ready` checks only the database and Redis, and stays that way: a bucket
outage or credential fault would otherwise pull the whole API out of rotation,
including chat and editing that never touch uploads. Upload storage is verified
by an explicit operator step instead.

On 2026-10-21 Railway reset every Bucket credential after a security incident
([disclosure](https://blog.railway.com/p/security-disclosure-recommendation-redeploy-buckets-and-reconnect-github-after-the-credential-reset)).
Production kept the old `UPLOAD_S3_SECRET_ACCESS_KEY` with an unchanged access
key ID. Every signed request returned `403 SignatureDoesNotMatch`, material
uploads failed with a generic 503 and readiness stayed 200. Replacing only the
secret and redeploying the same source fixed it.

`scripts/release_preflight_check.py --probe-upload-storage` loads the API's
`UploadStorageSettings` and signs through the same `S3UploadStorage` request
path. It PUTs one random 64-byte object at `preflight/<32 hex>.bin` with
`If-None-Match: *`, reads it back, compares SHA-256 and deletes it; it never
names, reads or overwrites an upload. A PUT the provider rejects with a 4xx wrote
nothing and is not cleaned up (a 412 is never deleted); after a network error or
5xx it still sends DELETE. A failed cleanup prints the synthetic key for manual
removal. Output is one fixed reason class, never the response body or a
credential:

| Reason | Meaning |
| --- | --- |
| `signature` | `SignatureDoesNotMatch`: the secret usually no longer matches the bucket credentials |
| `auth` | `InvalidAccessKeyId`, `AccessDenied` or any other 401/403: not proof that the secret is wrong |
| `clock` | `RequestTimeTooSkewed` |
| `config` | `NoSuchBucket`, `AuthorizationHeaderMalformed`, `PermanentRedirect`, or invalid variables |
| `network` | the endpoint could not be reached or the read was cut off |
| `provider` | 5xx or any other unexpected response |
| `not_found` / `conflict` / `integrity` | read-back missing, random key already taken, or bytes differ |

With `--strict`, an S3 backend fails unless the round trip was requested; the
local backend passes without a network call. Run it with production API
variables (service `server`):

1. Before pushing a `prod-v*` tag, from the release source checkout. This proves
   the variables the new deployment will read work with the release's signing
   code:

   ```sh
   cd apps/server
   railway run --service server --environment production -- \
     python scripts/release_preflight_check.py --strict --probe-upload-storage
   ```

2. After the new deployment is live, inside it, to exercise the deployed code
   and process environment (pass `--deployment-instance` if more than one
   instance is listed):

   ```sh
   railway ssh --service server --environment production -- \
     python scripts/release_preflight_check.py --strict --probe-upload-storage
   ```

3. After any bucket credential or `UPLOAD_*` variable change, including a
   provider-initiated reset outside a release: update the variable, let the API
   redeploy, then repeat step 2.

Staging uses `--environment staging`, its API service and its own bucket. Never
run `railway run env` or `printenv`; the script prints no secret values.

This is a point-in-time check. It cannot prevent or detect a credential reset
that happens after it ran, and the October reset happened outside any release.
Between checks, every failed signed request logs `Private object storage
<METHOD> failed: reason=<class> status=<HTTP status> code=<known S3 code>`, so
a generic upload 503 can be traced to its class in the API logs; the response to
users stays a generic 503. The probe writes and deletes one object, so it is not
part of the read-only three-day check. It is not scheduled and not part of
readiness.

## Effect on active users

The production API volume (`server-volume`, checked 2026-10-12) is still mounted at
`/app/chroma_data`. Feedback screenshots are written there through
`FEEDBACK_UPLOAD_DIR=/app/chroma_data/feedback`, and the volume also holds material
originals. Staging's `staging-api-volume` is mounted at `/app/uploads`. Volume
attachments live in the Railway service settings: removing the old `chroma-data`
entry from `railway.toml` does not detach anything (the name never matched).
Preserve both volumes, their mount paths and all original bytes.
Railway cannot overlap deployments sharing a volume, so readiness does not prove
uninterrupted API availability. The existing process cannot guarantee uninterrupted sessions while a
volume-bound deployment switches.

LlamaIndex/Chroma and their startup maintenance scripts have been removed.
`scripts/start_api.py` immediately replaces itself with Uvicorn; it no longer
opens a vector catalog or waits for cleanup before starting the API. Requirements
and local Compose no longer install/mount the unused indexing stack. Existing
local named volumes remain on disk; do not remove them with a cleanup command.
Historical maintenance receipts and original files remain private rollback data.

Keep `/health/ready`, the 300-second Railway drain and 295-second Uvicorn shutdown
budget. Shortening them to speed deployment would cut active streams. On SIGTERM the
old API asks every running generation to wrap up and ends any still running after
240 seconds with an `ERR_AGENT_SERVER_RESTARTING` terminal frame, saving partial
output and settling billing before the process exits
(`.agents/notes/implemented/architecture/2026-10-21-deploy-drain-ends-running-generations.md`);
never automatically retry a generation POST. Actual build/switch timing must be measured after the exact
revision is deployed; dependency deletion alone does not prove an elapsed-time
improvement. Main continues to be the staging source and `release-production`
the explicitly authorized production source, with provider Git integrations as
the only deployment producers.

Any future volume-free release first needs verified, reversible object migration
for every material/screenshot reference, with legacy originals preserved. Preserve production data and volumes unless a separately authorized migration
has verified every consumer. Railway's checked-in
TOML does not prove active cloud configuration; read back start command,
readiness, drain window and source SHA from the provider.

For a coordinated staging cutover, use the bounded authenticated GET probe and
separate SSE/write/accounting checks in
[staging cutover validation](staging-cutover-validation.md). Its read-only
receipt does not itself certify provider source binding or uninterrupted SSE.

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
