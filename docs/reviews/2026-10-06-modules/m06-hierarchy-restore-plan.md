# M06 hard-missing hierarchy restoration — approved repair plan

Final all-leaf architect found a real MEDIUM, original matrix boundary:
Snapshot restore iterates a set and resolves parents with session.get before
all missing rows exist/flushed. Child-first or autoflush=False can permanently
lose nested parent links. Public deletes soft-delete, but explicit recreated_files
service behavior is not an accepted policy to drop hard-missing hierarchy.

Root cleanup plan independently **APPROVE** before source change:
1. Lock valid folder-only hard-missing three-level hierarchy with actual rollback,
   child/parent metadata orders and autoflush modes; include mixed existing/new,
   scoped child with existing outside-target parent, and phase-2 failure atomicity.
   Use real PG FK evidence too. No invalid dangling content-version references.
2. First materialize all metadata-backed missing rows in effective scope with
   parent=None, retain other metadata/new timestamps, undelete existing as before.
   Flush only when rows recreated, no intermediate commit.
3. Second apply all scoped metadata and parents from the same-project loaded plus
   recreated map. Existing outside-scope parent remains usable; missing outside-
   scope parent is not recreated. Preserve permissive existing parent semantics,
   no new folder/cycle/deleted-parent validation.
4. Existing metadata rows advance timestamp exactly as before; recreated retain
   creation timestamp unless content restore later advances it. Keep content/
   version loop, missing-metadata behavior and extra-file cleanup unchanged.
5. Focused RED/GREEN, affected coverage/integration/real PG and independent review;
   no schema/provider/dependency/Actions changes. Full M06/all23 still open.

## Separate HIGH discovered in the same integration review

FileVersionHistory ties loadVersions to translator identity, and file context reset
to loadVersions. Locale rerender during successful pending rollback increments
fileContextGeneration and suppresses owning Editor onRollback reconciliation.
Native executor owns only FileVersionHistory and tests: genuine deferred-POST /
dynamic-translator RED, reuse approved VersionHistoryPanel translationRef pattern,
fileId-only context identity, retain actual file-switch stale fences, busy state,
callback await, errors and quota feedback. Root owns hierarchy and docs. Both
Web slices are now repaired and source-approved; final evidence is below.

## Editor translator/dirty-draft HIGH — separate bounded repair

Final integration reviewer found Editor.loadData depends on t and its effect
reloads same selected file on locale identity changes. This skips genuine file-
switch flushing and replaces a dirty visible controlled textarea with server
content. Preserve existing save queue/refresh/generations/file/project identity.
Executor owns Editor/tests only: dynamic-translator dirty-draft RED before changes,
translation ref updated independently, load async messages use current ref, remove
t from data-loader identity; retain render translation and real file-switch fences.
Focused unchanged coverage/types/lint; one final build after both Web slices stable.
No broad autosave refactor or policy change. Architectural minimal plan independently
proposed by final reviewer; root approved bounded existing-pattern repair.

## Implementation / exact validation

Two-phase source is now repaired and independently rereviewed **CLEAR**:
materialize metadata-backed scoped rows parent=None, flush only if recreated,
assign parents from same-project loaded/recreated map; no intermediate commit.
Existing rows advance once; recreated metadata-only rows keep creation timestamp.
Content/version and extra-file loops unchanged. No new parent policy/graph layer.

Evidence under /private/tmp/zenstory-module-audit-evidence-20261006:
- m06-hierarchy-red.log:initial10 failures included two invalid scoped targets
  missing Snapshot.file_id FK rows. Do not count these as genuine product RED.
- m06-hierarchy-valid-red.log:corrected existing scoped target fixture; six exact
  hierarchy-loss failures, two phase-2 injection-path failures, two compatibility
  passes (eight fail/two pass). m06-hierarchy-green.log:ten pass/four deselected.
- m06-hierarchy-postgres-red.log:four real PG failures (two hierarchy/two injection),
  without dangling content-version refs. Folder-only snapshot format is valid.
- m06-hierarchy-coverage.log:six-file affected batch76 pass/six skip; four new PG
  cases not configured plus two existing optional PG cases. SnapshotService
  coverage95.07%, unchanged80 gate. SQLite foreign keys enabled in isolated engines.
- m06-hierarchy-postgres-green.log:31-case candidate21 pass/ten fail, NOT GREEN.
  Temporary CREATE DATABASE inherited shared template1 SQL_ASCII; canonical Chinese
  folder inserts raise UnicodeEncodeError, then wait hooks are never reached.
  Shared template1 encoding was read back SQL_ASCII. No product source change was
  made for this local fixture condition and it is not a remote main-CI diagnosis.
- Created only owned UTF8 database with TEMPLATEtemplate0; server/client UTF8
  verified. m06-hierarchy-utf8-postgres-green.log:14 pass/ten deselected:only the
  ten failed concurrency nodeids and the four hierarchy/atomic PG cases. Unchanged
  17 previously passing protocol cases were not rerun. Not a single31-green receipt.
  Owned zenstory_audit_m06_hierarchy_20261006_root removed; catalog0/sharedhealth1.
- Scoped Ruff, compile and diff RC0. Initial test-only import ordering/C401 warnings
  corrected without changing source behavior or quality gates.

## Both translator HIGH fixes

FileVersionHistory: independent translateRef effect, fileId-only list identity,
current async feedback; actual file-switch/unmount generation fences retained.
Dynamic translator same-file pending POST retains busy state, avoids duplicate
initial GET, resolves with one reload and awaited owning rollback callback.
Source independently **CLEAR**. m06-file-version-translator-red.log:one genuine
fail/13 pass; green14 pass; scoped S82.63/B72.72/F76.66/L85.54 exceeds unchanged
66/55/61/68; types/lint/diff0.

Editor: live translateRef for async load/switch errors, removed only t from
loadData identity. Same-file locale rerender performs no extra GET and retains
visible dirty body; selected file/project/generation/history/switch fences kept.
Source independently **CLEAR**. m06-editor-translator-red.log:one genuine fail/
21 pass; dependent Editor/Editor.history/SimpleEditor green43 pass. Scoped
S71.55/B61.17/F62.85/L73.23 exceeds unchanged gates; types/lint/diff0. Focused
Editor fixture mocks SimpleEditor, so new test proves pre-debounce preservation
through parent visible value/GET count, not real timer coupling; unchanged real
SimpleEditor dependent deferred/debounce/switch cases pass.

Consolidated final Web source m06-integration-current-web-build.log:Vite13.46s,
org475x2 and docs24 pass. No Actions/push/remote logs/paid providers/new deps/schema
or production changes. M06 final all-leaf source integration is independently
**APPROVE / Architectural CLEAR**; see `m06-final-integration.md`. Full23 quality
gates and actual exact-SHA release are still required. main-CI follow-up stays
queued after already-promised work/delivery.
