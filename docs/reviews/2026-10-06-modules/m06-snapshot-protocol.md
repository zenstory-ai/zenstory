# M06 snapshot rollback protocol — repair plan and evidence

## Before-source evidence

Actual isolated PostgreSQL 14.22 at localhost:55439, owned UTF8 database
`zenstory_audit_m06_protocol_20261006_root`, no production data/provider calls.
Root-created durable `test_snapshot_concurrency_postgres.py` captures:

- cached scoped safety content and scoped/full future persisted-token regressions;
- paused pre-safety rollback vs stale-token writer and Web/Agent/Tool constructors;
- a cached parent soft-deleted during rollback;
- writer-first File lock, then FileVersion FK work, then rollback;
- material/draft/import/tool-root constructors and canonical repair gate lifetime.

Receipts in `/private/tmp/zenstory-module-audit-evidence-20261006/`:
`m06-snapshot-protocol-postgres-red.log` **8 RED**;
`m06-snapshot-writer-first-postgres-red.log` **1 RED** (requested content not restored);
`m06-snapshot-creation-gates-postgres-red.log` **6 genuine RED plus one importer
fixture error** (UUID passed to integer request fields). The first candidate
GREEN run was therefore **15 pass / one fixture error**, not a full GREEN receipt.
After correcting the request to numeric IDs, an in-memory AST probe of the HEAD
importer against the current locked rollback reproduced a genuine missing-gate
RED (`m06-import-valid-gate-postgres-red.log`). This probe has a different baseline
scope from the original eight-case/source run; no worktree source was reverted.
Synchronization uses explicit events and actual backend PID
`pg_stat_activity` Lock waits, not sleep-only inference. Shared cluster preserved.

## Reviewed minimal design / transaction cleanup plan

Independent architect refined earlier Project UPDATE proposal to avoid a FK lock
cycle: rollback Project **NO KEY UPDATE**, then File **NO KEY UPDATE** in ID order
with refresh, all before safety snapshot. Full rollback locks deleted rows too;
scoped rollback locks only its target File. Existing-project constructors and
standalone snapshot creation take Project **SHARE** before validation/gather and
retain it through the final transaction. Independent creators remain compatible;
ordinary one-file writers retain their current File-first locking. FK KEY SHARE
remains compatible with rollback's Project lock, allowing the writer-first version
insert to finish rather than deadlock.

Use one small gate helper on existing file_tree_rules, not a new locking service.
Revalidate Project authorization/active state after refresh; constructor parent
loads bypass stale ORM state. No universal SQLite cross-process locking claim.

Production material/draft uploads call existing private canonical-folder helpers
with commit=False. Preserve default committing mode for standalone helper callers,
but recover deterministic-ID duplicate insertions inside a savepoint, never via
outer rollback that releases the gate. Tool root repair similarly flushes within
its enclosing creation transaction. Successful all-invalid-input draft responses
retain original canonical-folder persistence via caller-owned final commit.
Keep subsequent history's best-effort/quota contracts unchanged.

Collect rollback reconciliation IDs under the service's protected region rather
than before its gate. The API removes that internal field before responding.
Standalone snapshot SHARE only excludes rollback, not ordinary edits; do not
claim all-file point-in-time snapshot consistency without additional File locks.
Aggregate M06 approval and wider modules remain pending.

## Independent review findings and measured repairs

An independent code reviewer reproduced three additional issues; root authored
the source/test repairs and the reviewer remains read-only:

1. First-write SQLite SAVEPOINT release commits a new canonical folder outside
   its caller's apparent transaction. Three actual SQLite/Web/Tool rollback tests
   were genuinely RED (`m06-folder-savepoint-sqlite-red.log`). One small helper
   on existing file_tree_rules starts a real SQLite BEGIN only when its DBAPI
   connection is not already in a transaction, then returns the normal Session
   savepoint. All three repair sites reuse it; no global engine policy change.
   Targeted receipt `m06-snapshot-review-sqlite-green.log`: **3 pass**, 18
   deselected (the -k filter did not run the other two supplied node IDs).
2. Snapshot create/rollback validated Project access before waiting, but ignored
   refreshed deletion/ownership state. APIs now take their existing gate before
   revalidating ownership, retaining the cheap initial access check. Actual PG
   deletion and owner-transfer races across both APIs were **4 genuine RED**.
3. Scoped snapshot prevalidation/gather reused a File cached before rollback.
   API scoped validation now occurs after its SHARE gate with refresh, and the
   service's scoped gather also uses populate_existing. Actual PG restored/deleted
   races were **2 genuine RED**; an independent-session SQLite gather test was
   also genuinely RED. Full-project gathering already uses fresh column reads.

The six PG review REDs are in `m06-snapshot-review-races-postgres-red.log`.
Root's reconciliation-ID regression separately reproduced a creator committed
while rollback waited being omitted from index reconciliation; protected service
collection repairs it. Existing real ASGI reconciliation also caught an accidental
root removal of the still-needed `select` import during that repair. That local
regression was restored, not suppressed. Its prior RED and the scoped SQLite
gather RED are in `m06-snapshot-scoped-reconciliation-sqlite-red.log`.

## Current bounded verification

- `m06-snapshot-protocol-postgres-green-final.log`: the corrected first **17 pass**.
- `m06-snapshot-protocol-postgres-review-green.log`: fresh **23 pass**, including
  the six additional review races, 3.56s.
- `m06-snapshot-gate-compatibility-postgres.log`: **4 pass** in a separate run:
  three concurrent canonical-folder duplicate races recover their savepoint
  without losing Project SHARE; a scoped rollback permits an unrelated actual
  Web writer to commit before it releases. This is **23 + 4 across two receipts**,
  not one 27-case run. Source unchanged except unused-import removal.
- `m06-snapshot-protocol-affected-coverage.log`: 14-file SQLite affected
  compatibility batch **338 pass / three existing Agent API skips**, 159.72s;
  includes both exact SQLite scoped-gather/ASGI-reconciliation prior REDs. Two
  services' scoped coverage **91.09%** passes the unchanged 80% gate; source was
  unchanged during the run. JSON receipt: `m06-snapshot-protocol-services-coverage.json`.
  This is targeted service coverage, not whole-server coverage or complete M06.
- Current scoped Ruff, py_compile and aggregate diff check: pass. First local
  Ruff caught unused json, then a new-test import order error; both fixed.
- Existing workflow contract tests: **9 pass**. New actual-PG file joins the
  existing serial dedicated-DB step, with step-local URL; full xdist/80% gate
  untouched. No remote workflow dispatch/rerun/push.
- Only the owned PG test database was dropped after all PG processes finished;
  readback verifies absence and shared localhost:55439 cluster still reachable.
- Independent native code reviewer final **APPROVE**, zero remaining findings
  in the bounded protocol slice. Separate source author/reviewer maintained;
  reviewer read the final receipts and independently checked default helper
  compatibility **11 pass**. LSP diagnostics unavailable; no LSP-GREEN claim.

No frontend source change; unchanged frontend build/types evidence is reused.
Previously documented full backend SQLModel type errors are not a typecheck-GREEN
claim. PG14.22 vs CI15 and local Node25 vs CI20 remain environment gaps. No provider
calls, dependency/schema changes, retention activation or production-data edits.
M06 Agent/Tool/legacy creation contracts, immutable Undo and snapshot paging/query/
diff performance, other module leaves and aggregate exact-SHA release remain open.

## Official semantics and limits

Lock compatibility/lifetime and consistent-order guidance were verified against
[PostgreSQL 14 explicit locking](https://www.postgresql.org/docs/14/explicit-locking.html).
The selected gate topology and FK deadlock avoidance are architecture inferences
to be verified by the writer-first real-PG case, not official product guarantees.
SQLAlchemy's flags were checked in the installed compiler and
[with_for_update documentation](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.GenerativeSelect.with_for_update).
Refresh must precede mutations: [populate_existing](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing)
can replace pending ORM state. No dependency/API/version change.
The bounded SQLite repair is grounded in the official
[SQLAlchemy sqlite3 legacy transaction documentation](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#legacy-transaction-mode-with-the-sqlite3-driver)
and the actual three rollback regressions, not a universal SQLite concurrency claim.

No remote GitHub runs/logs/artifacts inspected or workflows triggered/rerun. User's
main CI follow-up remains queued after current work and promised delivery.
