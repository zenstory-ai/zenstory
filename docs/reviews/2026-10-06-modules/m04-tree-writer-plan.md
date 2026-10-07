# M04 tree writers — regression-first bounded plan

Current source mapping finds dedicated Web move/reorder/delete and Agent API move
outside the locked fresh-row discipline used by content updates. FileCRUD's
screenplay reuse and canonical-folder recovery also assign raw clocks. This is
an implementation gap, now reproduced by isolated actual-PostgreSQL regressions.
It is not a claim that these races occurred in production.

## Contract and repair boundaries

1. Keep authorization, error codes, chapter-order normalization, body/history,
   vector best-effort behavior and soft-delete response/idempotence contracts.
   Valid partial sibling reorder is supported: do not require every sibling.
2. Lock frozen/future clocks and untouched sibling/body/no-op behavior with real
   SQLite calls before changing product source. Native test engineer owns only
   a new timestamp regression file; root owns source and PostgreSQL tests.
3. Prove reciprocal/direct and deeper-ancestor parent cycles under actual PG,
   including Web move/parent PUT, tool parent update and external Agent move.
   Hold strong stale ORM references; use database wait observation and bounded
   events, not sleep timing as race proof. Prove delete versus child-create if
   deletion participation is required. Use only owned UTF8/TEMPLATEtemplate0 DB.
4. Independently review minimal existing Project gate and fresh File lock order
   BEFORE implementation. Structural writers acquire project before file;
   ordinary content writers should not become project-wide serialized. Reuse
   current helpers/NO KEY UPDATE/FK-compatible semantics rather than a new lock
   subsystem. Refresh parent traversal after waiting; preserve SQLite limitations.
5. Reorder/reuse must use fresh authoritative rows and monotone tokens; remove
   only proven duplicate work. Measure affected SQL counts before/after where
   batching eliminates per-ID reads, without claiming production latency.
6. Run affected tests, real PG races, coverage and Ruff/compile; independently
   rereview source. Do not repeat unchanged Web checks absent Web changes.

No schema/dependency/provider/retention/Actions/push/merge/deployment changes.
M04 full leaf review and all23 aggregate gates remain open. Deferred main-CI
run/log/artifact audit stays queued until current promised work/delivery completes.
## Independent minimal-plan approval

Native architect **APPROVE / CLEAR** for this bounded implementation, conditional
on retaining endpoint-specific contracts and the additional delete cases below.

- Keep `rollback=` on `lock_project_for_files`, add `exclusive=False`; strong
  mode is `rollback or exclusive` (Project NO KEY UPDATE). Creators/reorder keep
  SHARE. Graph/live-membership writers take exclusive. Always Project before
  fresh File NO KEY UPDATE; revalidate auth/live target after waiting. Pure
  content/title/order writes stay File-only unless explicitly changing parent.
- Participants: Web move, Web PUT with parent field, Agent move, tool update with
  non-None parent argument; Web/Agent/tool deletes, recursive or nonrecursive.
  Refresh every ancestor and tool normalization read after the structural gate.
- Reorder loads requested rows once in sorted-ID lock order, applies positions
  in request order, advances selected tokens only, leaves omitted siblings alone.
- Delete fresh-locks every mutated descendant before deriving its token; retains
  Web nonrecursive active-child behavior and already-deleted/error differences.
- Screenplay reuse adds fresh/locked existing-query semantics, advances only
  true promotion/order changes, preserves newest/type/content/no-op behavior.
  No simultaneous first-create phantom-dedup guarantee is asserted.
- Canonical-folder raw clocks remain a separately noted M04 leaf, not swept into
  this proven boundary without another regression/approved repair.

RED evidence: SQLite13cases8fail/5compat; first PG14fail candidate contains12
genuine failures (8 cycle,3 delete-create,1 stale reorder),2 invalid noncanonical
screenplay fixtures. Corrected canonical screenplay2 genuine failures separately.
First SQL budget listener used wrong plural table, invalid0 count; corrected
table-name listener proves50 File SELECTs. Added delete-vs-reparent across all
three delete entrypoints. Initial18-failure deletion candidate contained four
genuine failures and14 key-hash fixture collisions, not18 genuine RED. Corrected
ephemeral-key fixture against isolated original HEAD delete AST functions gives
15 genuine timestamp RED, without reverting product source. Preserved Web/Agent
parent validation status400 (initial new PG tests incorrectly expected404).

SQLite has no cross-process Project gate; this plan makes no universal SQLite
reciprocal-cycle guarantee or project-wide ordinary-content serialization claim.

## Current bounded implementation checkpoint

Project exclusive mode/ancestor freshness, structural writer participation,
monotone stamps and sorted bulk reorder are implemented. Independent reviewer
caught foreign-row locking in the first bulk query; real PG NOWAIT regression
was RED, fixed with authorized-project predicate and unlocked error-path lookup.
Current19-file affected batch425pass/3existing skips, scoped four-module82.40%
above unchanged80; includes all23 current PG cases. Prior focused46pass and six
new PG pass are separate receipts. Reorder SQL50->1, omitted sibling untouched.
Ruff/compile/diff pass; local workflow contract9pass after adding required PG
modules and UTF8/template0 owned DB producer to the existing serialized step.
No remote Actions/mainCI inspection or execution. Shared protocol compatibility four-file52pass includes10SQLite hierarchy and
42actualPG with CI plainpostgresql URL; localPG14/CI15 gap remains. Owned UTF8
M04DB droppedcatalog0/sharedhealth1. Independent final bounded source
**APPROVE / Architectural CLEAR** is recorded in `m04-tree-writer-review.md`;
M04 full leaves remain open.
