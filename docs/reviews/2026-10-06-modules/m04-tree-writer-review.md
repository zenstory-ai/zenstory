# M04 tree-writer bounded closure

Independent architect final **APPROVE / Architectural CLEAR**; no remaining
HIGH/MEDIUM source finding in this exact slice. Not full-M04/all23/release approval.
Plan/invalid-candidate accounting: [approved plan](m04-tree-writer-plan.md).

## A / P / B result

- Architecture: reuse the existing Project gate and File lock patterns. Creators/
  reorder keep SHARE, topology/deletion/rollback use NO KEY UPDATE; Project before
  File, fresh auth/live row and every ancestor after waiting. Content-only writers
  remain File-only. Existing single-target SQLite stripes participate; no new
  multi-lock subsystem, schema, dependency or global transaction policy.
- Performance: sorted bulk load for requested reorder IDs gives **50→1 File
  SELECT**, while applying positions in original request order and leaving omitted
  siblings untouched. This is a controlled query count, not production latency.
- Bugs: actual PG reciprocal/deep parent cycles, delete-vs-create/reparent across
  Web/Agent/tool, stale reorder/reuse and frozen/future/nonadvancing tokens fixed.
  Fresh descendant locks preserve a concurrent content writer's body/token; parent
  plus content commits its history under the compatible gate. Unrelated content
  and other-project structural work still complete without global serialization.
- Security rereview: initial bulk query could lock a foreign File before rejection.
  Genuine NOWAIT RED → authorized-project lock predicate + unlocked error-path
  lookup. Legacy FILE_NOT_FOUND/VALIDATION_ERROR400 distinction retained.
- Contracts: partial sibling reorder accepted, chapter normalization unchanged,
  Web nonrecursive delete leaves existing children active, endpoint-specific
  already-deleted responses retained, no-op reused episode returns actual body/
  length and false mutation marker without advancing its token. Index/history
  best-effort policies unchanged.

## Fresh exact evidence

Evidence root `/private/tmp/zenstory-module-audit-evidence-20261006`:

- `m04-tree-final-coverage.log`: current stable19-file **425pass/3existing Agent
  API skips**, four-module coverage **82.40%** > unchanged80. Includes all23
  current actual PG tree cases. Not whole-server coverage or backend mypy GREEN.
- Prior `m04-tree-writers-current-green.log`: **46pass**; separately
  `m04-tree-new-pg-green.log`: **6pass/17deselect**. Not one52-case tree run.
- `m04-shared-protocol-pg-compatibility.log`: four-file **52pass** =42 actual PG
  +10 SQLite hierarchy cases; plainpostgresql/psycopg2 URL as CI shape. LocalPG14
  remains different from CI15; not a remote CI result.
- Ruff/py_compile/diff pass for all changed backend/test files; no Web source
  change, so unchanged passed Web build/types were not repeated.
- `m04-pg-ci-contract-red.log` targeted1 genuine RED →
  `m04-pg-ci-contract-green.log` **9pass**. Existing serialized step includes
  tree/hierarchy/mutation PG modules and owned UTF8/template0 database producer.
  Full-suite xdist coverage/gates unchanged; no extra workflow job/remote run.
- Owned `zenstory_audit_m04_tree_20261006_root` dropped, catalog0/sharedhealth1.

## Nonblocking WATCHs / remaining full-M04 leaves

- SQLite has no cross-process Project gate or universal multi-row structural
  atomicity. Concurrent reorder/recursive descendants/reuse vs another SQLite
  writer is not guaranteed by this PG-focused repair.
- Exact-title duplicate reuse locks in recency order; theoretical overlapping
  duplicate deadlock has no actual RED. Simultaneous first-create phantom dedup
  is not promised. Do not add a lock/gap/uniqueness layer from speculation.
- Canonical-folder raw clocks, legacy cyclic/deep Web recursive deletion, upload/
  tree/export full leaf contracts and actual desktop/mobile render performance
  remain M04 work. Production uses local recursive renderers; virtualization and
  drag helpers have no production imports, move/reorder client methods no callers.
  This is an inventory fact, not authority to invent a new UI feature.
- Global mypy/env, aggregate all23, remote CI, PR/merge and exact-SHA production
  checks remain open. Deferred main-CI task is registered/queued, not investigated.

No paid calls, production/provider/schema/retention/dependency mutations,
commit/push/PR/merge/deploy or Actions dispatch/rerun used in this slice.
