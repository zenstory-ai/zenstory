# ZenStory agent guide

## Scope and working tree

ZenStory is a file-centred writing workbench: React/TypeScript/Vite in `apps/web`, FastAPI/SQLModel in `apps/server`, PostgreSQL and Redis in hosted environments, Prefect for material ingestion. Keep API handlers thin; use the owning service and existing patterns. Read `DESIGN.md` before changing the UI and the nearest agent guide before changing a subsystem.

Respect the user's latest scope. Protect uncommitted user work: inspect status first and use a clean isolated worktree when needed. Do not revert other agents' changes. Prefer small, reviewable batches; no new dependencies unless the user requests them. Preserve file-version concurrency, quota/refund accounting, private uploads, authentication, and SSE cancellation/ledger flush behavior.

## Environment and release rules

| Environment | Git source | Contract |
| --- | --- | --- |
| Local | Feature branch | Mock paid models and payments; use disposable test data. |
| Railway staging / Vercel Preview | `main` | Independent DB, Redis, upload storage, Prefect server/pool, JWT and credentials. Test authorized changes here first. |
| Railway / Vercel production | `release-production` | Promote an explicitly authorized release through a `prod-v*` tag after exact-source main CI and E2E pass. |

- A recurring check is read-only for the product and cloud: every three days inspect, update `zenstory-ai/geo`, and report conclusions, including unchanged results. Do not edit product code, merge its main, deploy either environment, restart services, change cloud configuration, or create release tags during a check. Record evidence and recommended fixes for a separately authorized development batch.
- Development authorization for staging does not authorize production. This draft-recovery/storage batch is staging-only. Never push directly to `release-production`, force a ref, deploy from a dirty checkout, or create a second deployment producer. Batch commits before pushing; reuse successful CI evidence and do not rerun unchanged passing workflows.
- Preview origins are `app-preview.zenstory.ai` and `geo-preview.zenstory.ai`; API is `staging-api-staging-a289.up.railway.app`. Keep Vercel login protection and `noindex`. Verify API routing to staging; never copy production secrets or user data into staging. Disable live models, OAuth and payments; tests must not incur model charges.
- Production origins are `zenstory.ai`, `app.zenstory.ai`, and `api.zenstory.ai`. Check provider source SHA and health separately from Git status. Health checks do not prove uninterrupted user sessions.
- API still mounts a volume containing Chroma, material originals, and feedback screenshots. Keep the volume until every consumer is migrated and verified. Object migration copies and checks bytes before updating references; keep legacy reads and original files for rollback. No automatic source deletion, volume detachment, or SQLite compaction during checks.
- Before an authorized Prefect worker release, pause the target pool and drain active jobs. Confirm the replacement worker is online **and the old deployment is REMOVED** before resuming. Staging uses `zenstory-staging-pool`; production uses `zenstory-pool`. Register named deployments separately (Prefect 3.6.28 ignores pool overrides with `--all`).

See `docs/ops/release-ci-cd.md` for gates, drain windows, source receipts, and volume limitations. Do not automatically create or link Vercel projects from an unlinked directory; use verified project/environment IDs for cloud operations. Keep credentials and private receipts outside Git, and redact command output.

## Product invariants

- Free accounts have **10 AI messages per Beijing calendar day** in code and persisted plan data. The internal **¥15 daily cost backstop** is an operational safeguard: never display its amount, percentage or a second quota. Exhaustion presents the standard pause/reset-time/upgrade prompt. Preserve Pro rules.
- `VECTOR_EMBEDDINGS_ENABLED=false` and `ASYNC_VECTOR_INDEX_ENABLED=false` remain the hosted baseline. Do not restart paid embedding/indexing work without explicit scope.
- Protect the latest unsaved editor draft before reload. Scope recovery by user/project/file; keep its server version token. A conflicting server version requires an explicit recovery choice, never an automatic overwrite. Storage failures must not falsely claim a draft is protected.
- Preserve the site's reading experience and organizational identity. Record actual search/activation/payment evidence in GEO; publishing code, IndexNow acceptance and traffic are not ranking, registration or revenue proof. Do not re-request unchanged Google indexing or generate bulk keyword pages during checks.

## Development and verification

Use the existing installed runtime and root pnpm lockfile. Typical commands:

```sh
# Frontend (from repository root)
pnpm --dir apps/web dev
pnpm --dir apps/web exec vitest run <affected-test-files>
pnpm --dir apps/web lint
pnpm --dir apps/web lint:tokens
pnpm --dir apps/web lint:i18n-keys
pnpm --dir apps/web build:typecheck

# Backend (from apps/server, with the project virtualenv active)
python main.py
pytest <affected-tests> -q --no-cov
ruff check agent/ api/ services/
alembic upgrade head  # local/disposable test DB only unless separately authorized
```

For behavior changes, first add a meaningful regression when coverage is missing, then implement and run targeted tests. Run relevant lint/typecheck/build and required CI. For cross-service storage/reload changes, also exercise real staging with synthetic users/files, outage/conflict cases, and byte/hash verification; do not substitute mocked tests for runtime proof. Keep validation claims tied to the final source SHA; report any gap. No automatic retry of generation POSTs or paid-provider calls.

For cleanup, write a bounded cleanup plan before editing, preserve behavior with regression coverage, prefer deletion/reuse, and keep writer/reviewer passes separate. Do not add tests that merely mirror a reversible text edit. Finish when the requested behavior and required checks pass.

## Decision notes

Before non-trivial behavior, architecture, wire/storage/config, process or test-strategy changes, search `.agents/notes/` for the owning decision. Update its facts or draft a note under `proposed/` and move it to `implemented/` with the code. Mechanical edits need no note.

Use `.agents/notes/{proposed,implemented,rejected}/{feature,bug-fix,simplification,architecture,process,testing}/yyyy-mm-dd-topic.md`. Write notes in Chinese with English headings: `## Problem`, `## Decision` (implemented facts) or `## Proposal`, `## Alternatives considered`, and `## Consequences` (benefits and costs). Give each actually considered alternative its strongest argument before explaining rejection. A reversal gets a new linked note; do not rewrite an old note into the opposite decision. No INDEX.md or dated decision log in DESIGN.md.
