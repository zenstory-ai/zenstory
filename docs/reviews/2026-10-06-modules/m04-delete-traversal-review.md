# M04 recursive-delete bounded review — corrected after independent review

Status: traversal and production-expiration performance repaired and locally verified; source uncommitted for coordinator integration. This closes only the assigned recursive-delete slice, not full M04/all23/release approval.
Base HEAD: `b88d9af1b90519b226b8200c82fcbc87d47621a1`. Combined changes are measured against saved starting-source, not HEAD. The follow-up delta is measured against coordinator-saved read-only `delete/phase1-source`.
Evidence directory: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/`; final detailed receipt: `REPORT.md`.

## Independent finding and correction

Independent architect review cleared the phase1 traversal but found a real performance blocker: production `get_session()` constructs `Session(sync_engine)`, which defaults to `expire_on_commit=True`. The deletion paths committed, then accessed each expired File to schedule index deletion. That issued one additional File SELECT per deleted row. The earlier52→3 claim applied only to the nonexpiring fixtures and was insufficient to claim production performance.

Before follow-up product edits, new tests used actual Web/tool scheduling wrappers and default-expiring Sessions, stubbing only the provider scheduling function. They produced **8 valid RED / 6 PASS**: recursive sizes1/50/1200 each failed the3-SELECT budget; nonrecursive cases each failed the2-SELECT budget. Exact-once/postorder descriptors, already-deleted-root compatibility, and failed-commit zero-schedule cases were already correct. No fixture failure is counted as this product RED.

Measured File SELECT counts for **each** Web/tool entrypoint:

| Scenario | Phase1 default-expiring Session | Final default-expiring Session | Final postcommit File SELECTs |
| --- | ---: | ---: | ---: |
| Recursive1 node | 4 (1 after commit) | 3 | 0 |
| Recursive50 nodes | 53 (50 after commit) | 3 | 0 |
| Recursive1200 nodes | 1203 (1200 after commit) | 3 | 0 |
| Nonrecursive root with children | 3 | 2 | 0 additional expired-row reads |

The original starting-source50-node count52 and phase1 count3 remain valid **nonexpiring-fixture** observations. Original starting-source production-shaped count102 is a hypothesis, not a measured result, and is not claimed. Final actual PostgreSQL50-node default-expiration tests measure3 total/0 postcommit for both entrypoints. These are File query counts, not total SELECT counts, latency measurements or constant database-work claims.

The minimal repair captures immutable `(project_id, file_type, id)` descriptors from loaded deleted rows **before commit**, then schedules **after successful commit** using only descriptors. Web also captures the user ID; tool captures the project ID for completion logging. Postcommit logging/count/scheduling does not access File properties. `_schedule_index_delete` has one repository caller, so its parameter now accepts the immutable descriptor tuple; no compatibility layer was needed. Existing best-effort catch boundaries and tool-context user-ID policy remain intact.

## Phase1 traversal findings and repair (unchanged)

The supplied valid baseline has7 FAILED /7 PASSED: both Web/tool fail on1200-depth chains, Web fails on self/two/three-node cycles, and both traverse legacy foreign-project parent edges. Seven compatibility cases passed, including tool cycle handling and nonrecursive child survival.

The architect-approved data-only `load_live_subtree_postorder(session, root)` anchors the root regardless of deletion, recurses through active same-project children only, and uses `UNION` distinct to terminate cycles. Its outer File query orders by ID, uses `populate_existing`, and takes PostgreSQL `FOR NO KEY UPDATE OF file` on subtree rows only. Explicit stack/visited DFS returns each row in stable child-ID postorder without Python recursion. Existing callers retain exclusive Project gates, authorization, root refresh and transactions; wrappers retain each row's original soft-delete/timestamp operations.

Three new compatibility cases passed before phase1 product edits: Web nonrecursive-then-recursive deletion of an already-deleted root, tool rejection of that root, and both entries' deleted-intermediate pruning. The helper and both `_delete_recursive` definitions are unchanged in phase2 (helper byte-identical; recursive wrappers AST-identical).

## Exact changed paths

- `apps/server/services/file_tree_rules.py`: phase1 subtree loader/export only; no phase2 edit. Existing helpers AST-identical to starting-source.
- `apps/server/api/files.py`: phase1 loader import/recursive wrapper; phase2 `delete_file` precommit descriptor capture and postcommit scheduling/logging only.
- `apps/server/agent/tools/file_ops/crud.py`: phase1 loader import/recursive wrapper; phase2 `delete_file` capture/logging and `_schedule_index_delete` descriptor adaptation only.
- `apps/server/tests/test_api/test_tree_delete_traversal.py`: traversal and descriptor/expiration/failure regressions. Shared `_delete` now stubs provider scheduling, leaving tool wrapper intact. Explicit User flush retains PG FK validity.
- `apps/server/tests/test_services/test_tree_delete_traversal_postgres.py`: depth/cycle/scope/row-lock/snapshot proofs plus default-expiration index budget for both entries.
- `docs/reviews/2026-10-06-modules/m04-delete-traversal-review.md`: corrected review.

No canonical helpers, other writers, Project gate semantics, expiration configuration, dependencies, migrations, production settings or global progress/workflow files changed. Baseline inventory1735 files: exactly four existing owned paths changed; PG test/review are added paths. `source-baseline.diff` is the final combined patch; `source-phase2.diff` is the narrow follow-up delta for coordinator three-way integration.

## Validation and preserved contracts

Phase2 fresh receipts:

- **57 SQLite PASS**: both default-expiring and existing nonexpiring fixtures,1/50/1200-depth budgets, immutable descriptor values/order, each index request once, unchanged Web response/tool bool, deleted-root/pruning/nonrecursive/clock/payload compatibility, failed commit enqueues/schedules zero, and best-effort scheduling failures retain successful deletion/return behavior. Web background tasks are inspected then executed against the provider stub; no actual provider call occurs.
- **10 PostgreSQL PASS**: existing8 deletion-focused depth/cycle/scope/lock/snapshot cases plus2 default-expiration50-node budget cases. Descendant NOWAIT write lock fails before mutation; unrelated same-project content row commits while delete holds its gate; KEY SHARE remains compatible; snapshot wait is observed through `pg_stat_activity`, with retained ORM references.
- Scoped coverage gate retained at80: real `-o addopts= --cov=services.file_tree_rules --cov-append --cov-fail-under=80` gives **94.05% (79/84 statements)**, using valid phase1 data for the byte-identical helper plus current deletion tests. Helper statement coverage is100% (25/25). Boundary changes are validated by production-shaped behavior/query tests; no whole API/CRUD coverage or branch-coverage claim.
- Ruff four follow-up Python paths **PASS**; compile/AST ownership/static checks **PASS**. No postcommit File-property accesses; all source definitions outside the two delete endpoints/tool scheduler remain AST-identical to phase1.
- Mypy1.20.2 with provided Python executable and `--follow-imports=skip`: same10 scoped pre-existing diagnostics as starting-source, exit1. No typecheck GREEN or whole-backend GREEN claim.

Phase1 historical evidence remains valid within its scope:82 SQLite integration PASS,8 new PG PASS,8 existing delete/create/reparent/fresh-content PG PASS; one helper File SELECT for1/50/1200 nodes. Those unchanged unrelated integration batches were not rerun in phase2.

## Receipt distinctions and limits

Phase1 initial PG6 were **invalid fixture failures** (Project-before-User FK violation before deletion). Explicit User flush fixed them; they are not product RED. Phase2 default-expiration8 failures are genuine product performance RED. One phase2 Ruff import-order diagnostic was corrected. There were no invalid phase2 test fixtures. Phase1 mypy-module absence was an environment gap resolved by installed mypy with `--python-executable` pinned to the provided venv. A phase1 patch-check harness initially misread git no-index exit1 for differing files; the corrected check saw no whitespace diagnostics, not a product failure.

Actual runtime: Python3.12.13, PostgreSQL14.22; no CI PostgreSQL15 equivalence claim. Owned databases explicitly used UTF8/template0; shared maintenance DB uses SQL_ASCII. No remote Actions, providers, real purchases, package changes, frontend E2E, publication or full-backend gates ran in this local lane. PG uses bound Sessions plus `database.is_postgres=True` monkeypatch.

Both phase1 `zenstory_m04_native_delete_20261006` and phase2 `zenstory_m04_native_delete_phase2_20261006` owned DBs were dropped with0 sessions/catalog0/sharedhealth1. Shared PostgreSQL was never reset, stopped, reconfigured or subjected to session termination. All source remains uncommitted; coordinator owns integration, independent aggregate review, CI and publication.
