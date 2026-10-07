# M06 immutable Chat undo — bounded repair and final review

Read-only native architect `/root/m06_undo_design`, 2026-10-06: **APPROVE**
fail-closed immutable descriptor plus exact-token conditional restore. Root owns
backend tests/source; bounded executor owns the Web slice. No complete M06 claim.

## Current defect / call graph

ToolResultCard passes only file ID through MessageList. ChatPanel queries the
current latest two versions at click time and restores the second one. An old
v1→AI-v2 card followed by user-v3 can infer another target and overwrite later
work; GET→POST also races. FileEditor returns no original-edit provenance.
Raw result objects already persist end-to-end through MCP/SSE/tool_calls[].result
and history parsing. No new chat table/schema or stored full before-content needed.
Content saves can legitimately omit history at quota/best-effort failure, so the
latest history head is not automatically a safe original-edit anchor.

## Reviewed minimal contract

Under the existing refreshed File write lock, best-effort resolve/reconstruct the
latest head. Keep its number only when content exactly equals old_content. Do
not insert a pre-edit row or fail an edit for provenance lookup. Emit only for a
real change with an exact existing anchor after a successful commit:

`undo: { before_version_number, expected_after_updated_at }`

Token is the edit's own normalized File.updated_at, captured while its write lock
is held before commit; a post-commit refresh must not supply provenance. No descriptor for no-op,
all-failed, missing/corrupt/mismatched history. Optional after-history creation
stays best-effort; an exact before anchor/token still supports undo if it fails.

Add optional expected_updated_at to Web rollback/client/shared rollback service.
Check exact normalized equality after fresh File lock and before mutation/quota/
history; mismatch => typed409, zero content/version/index/cache success effects.
No request body preserves intentional history/legacy unconditional restore.

One FileEditUndoTarget carries file ID, before number and token through card/list/
panel. Validate descriptor; old/malformed cards hide Undo, **never dynamic fallback**.
ChatPanel removes getVersions and POSTs immutable target/token. Success reconciles
tree/editor and reports quota/history omission from existing flags/upgrade surface;
409 uses existing error feedback without success refresh.

## Required local regressions

- Exact v1→edit descriptor/token; head/live gap, missing/corrupt head and no-op/
  all-failed omissions; optional after-history failure still commits safely.
- Matching conditional restore, absent-body compatibility, intervening Web/Agent/
  Tool/title/snapshot mutation409 without changes; repeated old undo409.
- Quota-full restores content with correct flags; unexpected restore-history
  failure reports omission, not content failure.
- Real SSE/persistence/history descriptor survives; card valid/absent/malformed;
  Chat zero getVersions/exact args/success refresh/omission and409 no refresh.
- Actual PG writer-first wait/re-read conflict in existing serial PG test file.

Architect references: server file_ops/edit.py, tools/mcp_tools.py, stream_adapter.py,
agent/service.py, core/message_manager.py; Web ToolResultCard/MessageList/ChatPanel;
shared file_version_service.py and api/versions.py. Re-read current source before
implementation. Extra provenance replay is correctness work to measure, not a
universal-token guarantee beyond audited writers. All23-module goal stays intact.

## Current before-backend-source evidence / implementation refinement

`m06-undo-backend-valid-red.log`: **14 genuine failures/nine compatibility cases
pass**, including actual HTTP ignored tokens returning200 instead of409 and
missing immutable edit provenance. The first candidate had22 fixture errors
from a reused unique Agent API key hash; per-user hash fixed that harness before
accepting this RED. Test persistence helper and API error_detail field were aligned
to the actual existing contracts before the valid run; no source was reverted.

`m06-undo-postgres-red.log`: **two genuine failures** on the actual PG engine.
The HTTP rollback is observed waiting on the writer's real File lock with a
strong cached ORM reference retained, then returns200 instead of409. A real
optional provenance SELECT failure (`division by zero`) currently affects the
after-history path instead of being isolated before an otherwise successful edit.

Refine the already-approved best-effort provenance step: place the lookup and
replay **inside a read savepoint while the fresh File lock is held**, before
changing its content. A real PostgreSQL query failure must not poison the outer
edit transaction. Reuse the loaded-target private replay helper (already reviewed
in query/paging) to avoid selecting the same base target twice. Exact equality is
still required; no extra baseline or full before-content in the chat payload.
Conditional rollback locks/refetches File before checking optional normalized
timestamp equality and reading the anchored version; mismatch comes before all
content/quota/history mutation and success reconciliation.

Executor reports Web170pass/four files, tsc/lint/diff clean, with old card/no-body
compatibility and exact target preserved; root integration/final review pending.
All validation local. No push/Actions/deps/providers/retention changes; owned PG
test DB still present for the forthcoming GREEN and will be dropped when finished.

## Root counterexample to candidate review — post-commit token race

The candidate derives its token from `session.refresh(file)` after commit. The
PostgreSQL File lock has already been released, so another normal writer can
commit before that refresh. The card then combines the original before-version
with the later writer's token, and conditional Undo can overwrite later work.
The previous candidate approval is superseded pending a genuine PG regression,
repair and re-review. Capture the edit's own assigned monotone timestamp while
its File lock is held; emit that immutable token only after commit succeeds.
Do not use a post-commit refreshed value as edit provenance. Add a deterministic
real-PG test interposing actual Web save before editor refresh, then invoking
the actual conditional rollback API: later content must remain and return409.

The eight-file affected candidate run completed205pass/onefailure: its missing
File test still expects version-first lookup, but the approved contract locks
and verifies File first. Update that stale expectation, preserving the distinct
existing-File/missing-version test. Scoped coverage75.32% is below unchanged80;
the run is not GREEN. Add relevant existing editor behavior suites to coverage,
not exclusions or a reduced threshold.

## Final bounded evidence and verdict

Read-only architect `/root/m06_undo_design` final rereview: **APPROVE / CLEAR**.
The initial HIGH post-commit race and frontend LOW malformed-validator findings
are resolved. This verdict supersedes the candidate approval and intervening
BLOCK; it does **not** close full M06, the23-module audit or release gates.

- Backend provenance lookup is read-only inside a savepoint under the fresh File
  lock; only an exact reconstructed before-content anchor is retained. No new
  pre-edit history row, schema or full before-content payload. Optional history
  failure remains best-effort. Exact conditional restore checks normalized token
  equality after fresh lock, before history/quota/content writes. No-body restore
  intentionally preserves existing history/legacy compatibility.
- `m06-undo-postcommit-token-http-red.log`: genuine real-PG/HTTP RED, a later
  Web save commits before editor refresh; contaminated card returns200, restores
  Original over later work and adds a fourth row. The fix captures a local ISO
  scalar immediately after assigning the edit's monotone timestamp. Commit/
  refresh stay unchanged; successful emission uses the captured scalar.
- `m06-undo-postcommit-postgres-green.log`: eight compatibility passes; the new
  race assertions correctly returned409/preserved content, but its final observer
  assertion mistakenly included the legitimate Web save's cache bump. Test-only
  correction snapshots the observer baseline before rollback. Corrected affected
  case `m06-undo-postcommit-postgres-target-green.log`: **one pass/eight deselected**,
  exact AI token/typed409/later Web content and token/three rows preserved, no
  rollback-added index/cache effects. **Not a single nine-case GREEN receipt.**
- `m06-undo-affected-coverage-final.log`: fresh unified **229 pass/nine files**,
  including25 provenance/HTTP/MCP→SSE→persisted Chat result/query cases. Combined
  edit/service coverage **80.95%** against unchanged80%. Missing-File expectation
  aligned to approved File-first locking; existing-File/missing-version remains
  separately tested. The earlier205pass/onefailed75.32% receipt remains retained.
- Provenance query costs are measured correctness overhead: base head one SELECT,
  delta head three SELECTs, plus a read SAVEPOINT pair. Loaded-target replay avoids
  re-selecting the head. Chat Undo removes its click-time latest-history GET:
  two requests become one conditional POST. No backend latency improvement claim.
- Web immutable target flows through live/persisted card/list/panel/client. Old,
  missing or malformed cards hide Undo; no dynamic target fallback. Exact token
  is forwarded unchanged. Conditional409 has existing error feedback and no
  success refresh; successful quota/history omission is shown while restored
  content still reconciles tree/editor.
- `m06-undo-web-final-tests.log`: initial four-file **170 pass**. Semantic validator
  genuine two-case RED (`m06-undo-web-validator-red.log`) then55pass, safe positive
  integer, real calendar/clock/offset bounds; valid microsecond/offset token exact.
  Fresh card/list coverage `m06-undo-web-card-list-coverage.log`: **105 pass**, scoped
  lines80.16/branches70.69/functions74.64/statements77.63, above unchanged repository
  thresholds68/55/61/66. Scope is these two components, not global Web coverage.
- Full `tsc -b`, scoped ESLint, Ruff, py_compile and diff-check pass. Latest source
  build `m06-undo-web-validator-build.log`: Vite11.45s plus org/docs routes pass.
  Nonfatal localhost3000 telemetry/teardown warnings in Web tests are not provider
  calls or test failures. No dependency/lock changes. Local Node25/PG14 vs CI20/15
  parity gap and aggregate local/CI/release gates remain explicit.
- Owned PG database `zenstory_audit_m06_undo_20261006_root` dropped after tests;
  catalog count0/shared-cluster health SELECT1. Shared cluster was not stopped.
  No commit/push/Actions/deploy/provider/retention changes; main CI followup queued.

Remaining M06 leaves: Agent/Tool creation and legacy fallback contract closure,
public history mutation semantics, streaming snapshot trigger frequency/lifecycle,
representative large-diff measurements, missing-version typed404 consistency and
other all-leaf integration items in `m06-review.md`. Preserve all23 modules and
their aggregate gates before the already-promised PR/merge/exact-SHA deployment.
