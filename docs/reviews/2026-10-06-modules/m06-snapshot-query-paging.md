# M06 snapshot queries and pagination — plan, repair and local evidence

Scope: remaining snapshot gather performance and reachable/predictable history
pages. All23-module goal remains intact; Undo and other leaves are not closed by
this slice. Root owns source; native reviewer/advisory lanes remain read-only.
No Actions, dependencies, schema/producer/config/retention changes or providers.

## Measured before-source evidence

`m06-snapshot-query-paging-red.log`, actual installed SQLite/Python3.12 stack:
**7 genuine RED / two unchanged reconstruction contracts pass**. Twenty populated
files with existing base history generate **26 SQL statements** during one
snapshot; twenty with one latest delta generate **66**. Snapshot gather already
loads latest FileVersion rows in bulk, but calls single-version reconstruction
again per file. Both target+nearest-base and legacy no-base/empty-start replay are
locked before refactoring. Equal-created-at paging lacks a deterministic ID tie;
limit0/-1/101 and offset-1 all return200 instead of validated422.

Corrected frontend/client pre-source receipt
`m06-snapshot-panel-paging-valid-red.log`: **seven genuine RED** (six panel paths
and client offset): missing50+ paging, append recovery, old-project response
overwrites new, initial retry, close/duplicate after submitted restore, and silent
description failure. First candidate RED receipt contained mock-once queue leakage
and an incorrect installed Lucide icon alias. Per-test mockReset and installed
Pen icon correction fixed those harness errors before accepting genuine REDs.

## Separate plan review

Native architect `/root/m06_undo_design` reviewed this written plan read-only:
**APPROVE**, with required private loaded-Mapping helper, per-file target ranges,
non-base-only replay, chunking rather than a file cap, mixed-target/base/no-base/
401-file regression, synchronized-live repair preservation, project+outline
generation fences, raw-offset-before-dedupe, append/initial retries, duplicate
load-more guard and POST+async-parent-reconciliation close guard. Description
updates should preserve pages by replacing their returned Snapshot locally.
Author and plan reviewer are separate. Final source verdict still required.

## Minimal cleanup/performance plan (before implementation)

1. Reuse loaded full-base content without another query. On the existing
   FileVersionService, extract bulk reconstruction for one selected version per
   file, keyed by file ID. The single-target method keeps its existing lookup,
   typed missing-target error and delegates to that reconstruction logic.
2. For delta targets, select nearest base numbers per file, then only their
   base-to-selected-target chains in a bounded batch (200 files); replay with the
   existing `_apply_diff`, preserving no-base empty-start behavior. Do not fetch
   all history, include later versions, cross file scopes, or add a new service.
   Gather reuses this loaded-version map. Expected20-file base6 / delta8 SQL.
3. Keep snapshot list response/default50 and optional file filter. Bound HTTP
   limit1..100/offset>=0 like existing file history, order created_at DESC then
   id DESC for ties. Add backward-compatible optional client offset.
4. VersionHistoryPanel gets50-row load-more, raw consumed-row offset, ID dedupe,
   separate append error/retry and project/file request-generation fencing.
   Preserve loaded rows on append failure and existing compare/restore behavior.
   Do not claim offset pagination is immutable across new inserts.
5. Lock UI paging/stale-context/real client offset defects with RED tests before
   source changes. Review in-flight mutations/close boundary instead of copying
   the prior FileVersionHistory bug. Preserve existing post-restore parent
   reconciliation and surface description failures; no visual redesign.

Independent plan review is required before the source refactor. Final affected
backend/Web tests, scoped coverage, lint/types/build, representative query counts
and a separate code-review verdict are required for bounded closure. Previous
protocol tests are reused only where their source/behavior is unchanged; shared
reconstruction changes require affected regression checks. Whole-M06/aggregate
release and exact-SHA production remain unverified.

## Local validation findings and test-alignment plan

The first affected coverage invocation collected zero tests because the API file
was named `test_versions.py`, not `test_file_versions.py`. The corrected nine-file
run reached 92.22% service coverage (unchanged80% gate), but was **not GREEN**:
113 pass / two existing skips / ten failures. Those ten API cases create files
through the real Web endpoint yet still assume that populated creation has no
version1 baseline. The new baseline contract is already explicitly reviewed and
covered in the creation slice; do not delete baselines or disable that contract
to make these tests pass.

Before changing those tests: assert the real system/base creation baseline;
adjust exact sequential counts/pages and latest expectations; use returned
version numbers for intended content/compare/restore targets (including quota
restore) and verify exact reconstructed content instead of the existing commented
out delta assertion. Keep a separate direct-SQL legacy-file empty-history case,
since old unversioned files remain supported. Then rerun the affected API cases
and the service integration/coverage gate. No production behavior or gate changes
are planned for this test-alignment step.

## Implemented bounded repair

The private loaded-target helper now lives on the existing FileVersionService;
single-target reads still select/validate their target, then delegate. Full-base
targets reuse the loaded content with zero reconstruction queries. Delta targets
use two queries per200-file chunk: nearest applicable bases, then only non-base
rows within each file's selected target range. Empty/legacy-no-base and mixed
targets are preserved. Snapshot gather reuses its existing bulk latest-version
selection and retains live/head mismatch synchronization instead of silently
referencing stale history.

Snapshot HTTP paging now validates limit1..100/offset>=0 and orders tied creation
times by ID descending. The optional client offset preserves the default URL.
The panel keeps loaded pages on append failures, retries the same consumed raw
offset, deduplicates IDs, fences project/file request generations and updates a
returned description locally. Restore submissions are guarded immediately and
Close remains disabled through both POST and awaited parent reconciliation.
The workbench callback no longer waits for a throwaway history GET before its
required file-tree/editor reconciliation.

A further genuine local RED reproduced changing the translator while restore
POST was pending: the translator-dependent load callback reset the context and
abandoned reconciliation. A separate translation ref now updates error messages
without treating locale changes as project/file changes. The exact regression
passed afterward; no unmount/cancellation of the already-submitted restore is
claimed.

## Receipts and honest intermediate failures

- `m06-snapshot-query-paging-green.log` is **not GREEN**: the first source
  candidate placed the bulk map before its input existed (three fail/eight pass).
  Corrected placement receipt `m06-snapshot-query-paging-corrected-green.log` is
  **12 pass**. Actual20-file gather SQL: bases **26→6**, deltas **66→8**;
  401 deltas **1209→12**, with all401 exact references, not a product cap.
  Mixed later-base/target/no-base replay and empty/base zero-query cases pass.
- First panel/client implementation receipt: **87 pass**. Additional boundary
  receipt: **96 pass/one failure** caused by trying to spy on an undefined test
  environment alert; explicit global stub corrected that harness. The affected
  one-case receipt then passed. These are separate invocations, not one97/97.
  New cases cover raw-offset-before-dedupe, duplicate paging, stale outline
  response/error/finally while the new outline loads, reset comparison/edit state,
  async parent reconciliation, failed/stale-context restore and description
  outcomes, and older-page preservation.
- Translator-only restore RED→GREEN: `m06-snapshot-translator-restore-{red,green}.log`.
- Backend affected candidate: **113 pass/two existing skips/ten old expectation
  failures**, service coverage **92.22%** (unchanged80%). Test-only baseline
  alignment then gives **16 pass** in `m06-versions-api-baseline-alignment.log`,
  including restored exact delta content assertion and legacy-empty history.
  No claim of one fresh all-GREEN125-case batch is made.
- Shared reconstruction's fresh actual-PG protocol compatibility:
  `m06-snapshot-query-protocol-postgres.log`, **27 pass**, 3.86s. Only the newly
  owned test database was dropped afterward; separate catalog readback0 and
  shared cluster health1 preserve the running cluster.
- Fresh `tsc -b`, scoped ESLint/Ruff, Python compilation and aggregate diff check
  pass. `npm run build` passes (Vite10.24s plus org/docs page generation).
  Whole-server typecheck retains the documented SQLModel gap.
- Fresh panel coverage receipt `m06-snapshot-panel-coverage.log`: **36 pass**,
  96.10% lines /93.71% statements /84.45% branches /92.85% functions, exceeding
  the unchanged68/66/55/61 thresholds for this component-only scope. This is not
  whole-Web coverage; the full aggregate release gates remain separate.

## Separate final source review

Native architect `/root/m06_undo_design` reviewed the final source, test alignment
and receipts read-only: **APPROVE / architectural status CLEAR**, no HIGH/MEDIUM
defects in the bounded query/paging slice. Author and reviewer are separate. The
review explicitly preserves the distinction between the failed coverage batch
and the later aligned16-case API GREEN; a fresh all-affected single-run backend
and complete aggregate release checks are not asserted here.

One non-blocking LOW watch remains: if a future production `onRollback` callback
rejects after a successful POST, the combined catch would label refresh failure
as rollback failure. The current sole production callback in Header is synchronous
and nonthrowing. Before using async reconciliation in production, separate the
success/refresh-failure feedback and add its rejection regression; no speculative
production behavior is changed for that unused branch in this slice.

No Actions or GitHub runs/logs/artifacts were used, no push/PR/merge/production
mutation occurred. The later main CI task remains queued, not started. This is
one M06 slice: immutable Undo, other M06 leaves, remaining modules, aggregate local
checks and exact-SHA delivery gates remain open.
