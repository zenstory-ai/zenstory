# M06 bounded history/comparison UX and snapshot cleanup repair

Direct execution with two bounded native executors; root owns backend/locale/CI
integration. This is **not full M06 or aggregate merge readiness**. Follow-up
main CI/E2E investigation remains queued, not started.

## Repairs / behavior preserved

- `FileVersionHistory.tsx`: existing offset/limit API now exposes pages beyond
  50 versions; file/request generation guards prevent stale list, compare, view
  and rollback completion from contaminating another file. Restore still succeeds
  when best-effort history is omitted; the existing quota upgrade surface or an
  omission warning is shown instead of silently ignoring response flags.
- A built-in read-only historical-content preview works when no external view
  callback is supplied. `SimpleEditor.tsx` no longer supplies a no-op callback.
  Both existing locale namespaces include five new history keys.
- `SnapshotComparisonDialog.tsx`: removed live-file 1+N fetches. Added/removed
  titles/types come from their snapshots; modified entries include old/new title
  and type even for content-only changes. Folder entries omit absent version
  labels. Metadata-less legacy snapshots fall back to IDs, not current titles.
  Effect cleanup fences out-of-order or unmounted comparison results.
- `snapshot_service.py`: cleanup detaches only links to snapshots being deleted,
  preserves all FileVersions/replay, then deletes and commits once. Failure rolls
  back both changes. Retention selection semantics are unchanged; no retention
  scheduler, migration or actual production cleanup was introduced.
- The new cleanup test is added to the existing dedicated serialized PostgreSQL
  CI step; the general xdist suite remains without the optional test PG URL.
  This is local workflow configuration validation, not a remote run/rerun.

## Evidence

Evidence directory: `/private/tmp/zenstory-module-audit-evidence-20261006`.

- History executor: four RED failures (pagination/stale list/quota/preview),
  additional stale rollback and stale compare REDs; final affected three-file
  run **31 pass**, scoped ESLint and types pass. Log prefix
  `m06-file-version-history-`, final suite `m06-file-history-affected-tests-final.log`.
- Comparison executor: **2 failed / 1 passed RED**; dialog + parent panel
  **23 pass**, scoped ESLint/types pass. Historical metadata rendering performs
  **zero live file GETs**, replacing one per changed file.
- Backend comparison contract **three REDs** (missing old_title), then
  cleanup + snapshot repair/service batch **38 pass / 2 optional-PG skips**:
  `m06-snapshot-compare-contract-red.log`, `m06-snapshot-cleanup-compare-green.log`.
- Cleanup with SQLite FK enforcement **two REDs**. Actual PostgreSQL 14.22
  original HEAD cleanup method (loaded in-memory, working source not reverted)
  **two RED FK violations**; final SQLite/PG batch **four pass**, including
  injected post-flush commit failure and atomic rollback. See
  `m06-snapshot-cleanup-{red,postgres-red,postgres-green}.log` and the baseline
  probe artifact. Only a newly created UTF-8/template0 local DB was dropped;
  shared PostgreSQL cluster preserved. CI uses PG15, a documented local gap.
- Local workflow contract **nine pass** (`m06-workflow-contract-green.log`).
  Scoped backend Ruff/py_compile and diff hygiene pass. History en/zh key and
  `{{version}}` interpolation assertions pass; namespace defaultValue checker
  also passes, but its scan alone does not cover unnamespaced history keys.

## Boundaries / next work

Independent reviewer + architect approval for this exact integration is pending.
Snapshot project/file concurrency, all structural creation baselines, Chat undo,
unused unsafe FileVersion cleanup removal, snapshot pagination/query performance,
diff bounds and remaining M04/M14/other module leaves are not closed by these
tests. No new physical browser or complete default/release E2E claim is made.
No remote workflow, push, PR, merge, provider deploy or paid request occurred.

### Independent review / rework in progress

Actual code reviewer returned REQUEST CHANGES: (HIGH) actual SimpleEditor reload
unmounts quota prompt/toast, (MEDIUM) append failure hides all loaded history and
retry, (LOW) snapshot file_metadata DTO differs from stored JSON-string shape.
Architecture lane was initially unavailable (thread limit), later successfully
launched as `m06_history_restore_arch`; no integrated approval claimed yet.

Root added actual Editor→SimpleEditor→FileVersionHistory integration regressions
and retry regression: **three real REDs**, then **17 pass** with proposed callback
state refresh, mounted history/feedback, dirty/debounce reset, pause autosave while
history is open, title/content baseline sync, separate retryable page error and
string|null DTO. Pending save-chain interleaving is under architecture review.
The initial type fixture tsc run passed because app config excludes tests; it is
NOT a type RED. One first pagination RED left an unconsumed mock queue and also
broke the following test; resetAllMocks fixes that harness leak. The later clean
three-RED batch is the product evidence. Pre-rework UI scoped coverage batch was
**54 pass / 84.4% lines / 67.77% branches**, existing thresholds unchanged; this
predates the current rework, so final scoped coverage remains pending.

### Unused delta cleanup removed

Existing architect-approved cleanup plan appended before source deletion. Whole
repository caller search found only the unsafe unused definition (no production
caller, job, API, docs or CLI). Root removed the method and its exclusive imports,
not an active retention path or public version creation API. Aged auto-save base
+ two deltas pinned by snapshot reconstruct all three contents and rollback the
pinned content; corrected new baseline **one pass**, existing reconstruction
class **four pass** before deletion. Initial helper already supplied v1, so the
first test accidentally created v2-v4 and failed its base assertion; fixture error,
not product RED. After deletion, affected version service/snapshot repair/quota
batch **50 pass**, Ruff/py_compile/diff pass. Reviewer independently approved
this removal extension (but overall UX integration still REQUEST CHANGES).

### Save-chain barrier / final local rework evidence

Independent architect found two concrete lifecycle boundaries, then verified
repair: finish queued writes **before** submitting rollback; don't consume old
content while the state refresh is still in flight. Active-save ordering test was
**one RED**. File history now awaits SimpleEditor's captured serialized save-chain
before POST and checks current file generation again. Autosave/manual/portal
save are fenced while history is mounted, without flushing a merely unscheduled
local draft. Parent Editor re-fetches its owned file/content/token; a dedicated
history-token baseline effect waits for changed `updated_at`. The original
async-file-switch content-only effect is preserved separately (the first combined
coverage attempt caught a real title-dependency regression, then repaired it).
The stale-completion test now waits until POST starts before navigating, rather
than accidentally exercising cancellation-before-POST due to the new await.

Final current integration evidence: **80 pass in seven files**, scoped two-UI
coverage **85.93% lines / 69.89% branches / 78.94% functions / 83% statements**,
existing thresholds unchanged (`m06-history-rework-coverage-final.log`). This
includes real Editor/SimpleEditor/history, delayed file refresh with subsequent
fresh-token one-character save, active-save ordering and dirty-draft portal
Ctrl-S fencing. Scoped ESLint/types pass (`*-final2.log`), Vite app build **11.80s**
(`m06-vite-app-build.log`). Full org/docs/release build/E2E remain aggregate work.

Architect independently returned **CLEAR** for this bounded restore/append/DTO
slice, fresh 32/32 affected tests. Non-blocking caveat: a future throwing
onRollback consumer could be mislabeled rollbackFailed; actual Editor.loadData
catches its own errors. Final independent code re-review launched after earlier
native follow-up thread-limit failures; its verdict is pending. Wider snapshot
locking/baselines/Undo/diff/performance cannot inherit this bounded approval.

### Closing an already-submitted restore — additional reviewed blocker

Final code re-review again REQUEST CHANGES for one HIGH: after POST starts,
closing history invalidates the component generation and skips parent refresh,
leaving the still-mounted Editor stale. Root confirmed **one actual RED** and
implemented the reviewer's bounded critical-section option. Pre-POST close still
cancels queued preparation. Once POST is submitted, header/backdrop/Escape close
and duplicate rollback are blocked until version-list and parent refresh settle;
only the submission owner releases the ref/visible busy state. File-context
changes reset visible busy state, and stale file results remain fenced. Tests
cover real Editor close/Escape/backdrop + duplicate prevention, retained quota
feedback/fresh content and close-before-POST cancellation. Final affected two-file
verification and repeat independent bounded approval are pending readback; do not
claim this last HIGH closed solely from the earlier 80-case batch.

### Last-guard readback / bounded closure

Final independent code reviewer **APPROVE**, zero findings in the last three-file
close-after-POST delta; preceding HIGH/MEDIUM/LOW findings separately closed.
Current final affected two-file **18 pass**, FileVersionHistory-only coverage
**85.29% lines / 73.48% branches / 75.86% functions / 82.35% statements**;
existing thresholds unchanged (`m06-history-close-coverage.log`). Fresh scoped
ESLint/tsc/diff pass, final Vite app build **11.54s**
(`m06-vite-app-build-post-guard.log`). Do not sum overlapping 80/18 test batches or
combine the different coverage scopes. Architecture CLEAR applies to the reviewed
restore-chain/token/append/DTO slice; no full M06/23-module/release claim.
Remaining work continues under the matrix; main CI follow-up still queued only.
