# M03 W2: atomic daily activity, streak and cache publication

## Result and claim boundary

The record route now stages daily activity and streak in one transaction,
captures its response before commit, and publishes the dashboard version only
after commit. A failed transaction leaves no additive activity or version bump;
retrying after that failure records once. Unexpected cache invalidation failure
does not falsely report an already-durable write as HTTP failure.

This is not general POST idempotency, a distributed cache/database transaction,
or protection against network ambiguity after COMMIT. Concurrent Python updates
to an already-existing streak remain a separate WATCH. Existing cache TTL bounds
freshness if best-effort invalidation is unavailable.

## Grounded failure evidence

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/m03-stats-record-transaction-proof/`.

- `red`: five actual JWT/ownership/HTTP tests, **two genuine RED, three PASS**.
  A real streak `before_commit` failure left daily `words_added=100` and version2
  despite HTTP500; one retry produced200. A held commit let actual GET cache the
  old streak1 under version2; after durable streak2, final GET still returned1.
- `expanded-red`: four targeted actual runtime failures, no invalid probes.
  These cover primed-memory-cache ordering, unexpected bump failure, new-row
  rollback and fallible postcommit ORM response reads. Original receipts remain.
- Final new HTTP suite: **nine PASS**, including zero/below-threshold and
  historical controls. Real memory backend, no ORM/service replacement.

## Minimal source change

- Four existing service methods gain keyword-only `commit=True`; route calls
  explicitly use `False`. Atomic SQL additive counters remain unchanged.
- First daily/streak inserts run inside SAVEPOINTs; uniqueness recovery re-reads
  the winning row without rolling back the outer staged activity. A tiny local
  helper starts a real outer SQLite transaction before a first-write savepoint.
- One effective date is shared by daily and streak operations. The complete DTO
  is captured before the sole commit; postcommit work uses scalars/DTO only.
- The approved AI summary loader and **35 other service functions**, plus all
  **five other stats API handlers**, are AST-identical to this phase baseline.

## Local verification and resources

- Affected SQLite suite: **69 PASS**, unchanged combined three-module coverage
  threshold80 met at **82.09%**. This was before a typing-only SQLiteConnection
  cast; the final nine HTTP cases passed again after that annotation correction.
- Real UTF8/READ COMMITTED PostgreSQL: **five PASS**—three existing atomic-counter
  controls plus two actual HTTP first-row races. Both racers observe real absent
  rows on distinct backend PIDs. B's real23505 is recovered in each case; same-day
  counters total30 in one row, and distinct-day streak creation ends in one row
  with latest date and streak2. No poisoned outer transaction.
- Scoped Ruff/diff/AST checks pass. Scoped product mypy remains **NOT GREEN**:
  89 baseline and89 final diagnostics, zero added after multiplicity-aware
  normalized comparison. Intermediate90 was corrected without suppression.
- Required serial CI enrollment adds the two new project-capacity/stat-record
  PG files. Local enrollment contract RED, then **nine workflow controls PASS**.
  No workflow run, dispatch or remote rerun occurred.
- Every positively owned request/session/engine/file/cache key is closed or
  removed. Owned PG lease:0 sessions before nonforced drop,0 catalog entries
  after, shared health1. The old unidentified conftest attribution gap from an
  earlier phase is retained; no guessed deletion.

Independent SOURCE APPROVE / CLEAR received for this bounded repair. M03 also requires W1 capacity source
approval/integration; no all23, full typecheck, final CI or release claim here.
