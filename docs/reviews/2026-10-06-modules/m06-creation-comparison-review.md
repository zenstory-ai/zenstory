# M06 creation/history contracts, comparison and inline diff

Root/backend author and bounded Web executor; separate read-only architect
`/root/m06_undo_design`. This is bounded module progress, not full M06 or release
completion. Main CI investigation remains queued, with no remote run inspected.

## Creation and explicit history policy — reviewed keep decisions

- Agent API populated create/update: user-attributed, quota checked, same outer
  transaction. Only expected quota overflow permits content without history;
  unexpected history failure aborts. Empty create has no row. Existing quota and
  injected-abort tests: `test_agent_api_structure.py`, `test_agent_review_invariants.py`.
  No source strictification or attribution change.
- FileCRUD AI create/fill/update: existing quota-free AI provenance and optional
  history savepoint; content survives unexpected history failure. Three direct
  new compatibility cases (`m06-ai-history-contract-green.log`): populated create
  injected failure persists/no row; empty create0→fillAIv1; update injected failure
  persists/no extra row. Three pass/nine deselected; no source change required.
- Public Web POST versions intentionally checkpoints supplied content, including
  content different from live File. It forces user provenance/quota regardless of
  compatibility source hint. Keep route, DTO, client and direct API tests; it is
  not a baseline-only endpoint. CLI/Agent version commands expose list/detail/
  rollback; production Web's only create caller was the removed Editor bootstrap.
- Legacy empty history remains supported. No mutation-on-GET, backfill migration,
  universal strict-history guarantee or invented pre-first-edit legacy policy.
  New populated Web constructors already transactionally own a system baseline.

## Editor read-side mutation removed

`Editor.loadData` formerly loaded File then GET versions→POST loaded content when
empty. Another writer/two tabs could append stale/duplicate history; the server
forces user source, so merely opening could consume quota. Every reload also
paid a history GET. Delete only this bootstrap/import/doc/dependency; real save
quota errors/upgrades remain, as do Editor refresh/save-queue boundaries.

`m06-editor-bootstrap-red.log`: three genuine failures/17 compatibility passes.
`m06-editor-bootstrap-green.log`: Editor + Editor.history + SimpleEditor **42 pass**.
Populated/legacy/out-of-order loads assert zero version GET/POST and no load-time
quota prompt; existing actual-save quota modal retained. Scoped Editor coverage
S71.62/B61.76/F61.76/L72.85 passes unchanged66/55/61/68. Full tsc, scoped ESLint and
diff checks pass. This repairs a client read defect, not public API semantics.

## Local backend E2E baseline alignment

Both existing request-driven contracts asserted that explicit checkpoint after a
populated create was v1. Current source correctly returnsv2. Authentic local run
`m06-e2e-baseline-candidate.log`: two genuine assertion failures (actual2 vs1).
It also emitted nonfatal background indexing/default-engine missing-table noise;
the GREEN environment explicitly disables async vector indexing before imports.

Keep populated creation, directly verify systemv1 original `seed`/`draft zero`,
then checkpointsv2/v3, list3/2/1 with system attribution, compare2/3, restore2→newv4.
`m06-e2e-baseline-green.log`: **two pass/13 deselected**. One previously unused
snapshot test assignment was deleted while preserving its awaited file creation
to satisfy existing Ruff. No test escape through empty creation or reduced gates.
These are current M06 local tests, not evidence about remote main failures.

## Comparison duplicate work removed with exact payload fidelity

Load targets once using existing get_version_by_number; reconstruct from the
reviewed loaded-target helper. Identical target reuses row/content. Count added/
removed structured rows instead of rerunning the same splitlines matcher for
stats. Unified diff keeps its separate keepends semantics. No replay layer,
dependency, truncation or response shape change.

`m06-comparison-work-red.log`: **11 genuine failures/five compatibility or
measurement passes**. SELECTs base/base4→2, base/delta6→4, delta/delta8→6,
same-base4→1, same-delta8→3; SequenceMatcher passes3→2. Exact unified/html/stats/
timestamps preserved, including empty, trailing-newline-only, repeated and Chinese
content; missing first/second/same targets keep exact ValueError contract.

`m06-comparison-work-green.log`: unified **100 pass/five backend files**, scoped
service coverage **93.63%** against unchanged80. Subsequent reversed-target cases
`m06-comparison-reversed-green.log`: two pass/16 deselected; not a single102 receipt.
Actual missing-version conditional and no-body rollback400→typed404 siblings:
two RED→`m06-rollback-not-found-green.log`43 pass, with zero content/token/history/
index/cache changes. Ownership, stale409 and service compatibility unchanged.

Controlled `m06-comparison-benchmark.py/json`: only the original b88d9af comparison
body executed with the same current replay helpers, exact response equality, same
SQLite process/seed, no coverage, ten alternating samples each. Base queries4→2.
200lines/5,600chars median0.909→0.648ms; 5,000lines/140,000chars14.394→10.476ms.
These isolate the comparison repair; they are not production PostgreSQL latency
or SLA. RED uninstrumented timing vs coverage GREEN timing is not compared.

## Inline diff bug/performance observation

Inline alone ignored Hide unchanged, and collapsed whitespace/merged changed
paragraphs. Apply the existing equal-row filter, pre-wrap container and separators
for every structured row. Full default, all content, toolbar, numbering and other
view modes remain. This is line-oriented preview, not byte-faithful trailing-newline
representation (backend splitlines does not preserve that metadata).

`m06-diff-inline-red.log`: two genuine failures/two passes. GREEN four pass. Scoped
DiffViewer coverage S89.55/B82.65/F100/L89.06 above unchanged66/55/61/68.
Offline real Chromium, actual component bundle/i18n stub/built Tailwind, network
aborted, no server/provider: before Hide inline retains201/5001 rows and normal
whitespace; after it renders two changed rows/pre-wrap with distinct paragraph
separators. Reproducible script and before/after JSON/logs: `m06-diff-browser-*`.
Large full-default mount remains25,035 DOM nodes/~260.6 vs260.9ms; **no full-mount
speedup claimed**. No truncation, default change or speculative virtualizer added;
existing focused view now works. Larger full-view windowing is a nonblocking
measured tradeoff for future UX, not an asserted repaired latency invariant.

## Gates and remaining work

Latest stable Web source build `m06-comparison-editor-build.log`: Vite11.67s plus
org/docs routes pass. `m06-comparison-editor-hygiene.log`: Ruff/py_compile/diff0;
dependencies/locks clean. Native specialist rereview approved comparison/404/inline
patches; final independent architect Editor/E2E/keep-policy review is
**APPROVE / CLEAR**. Existing save quota feedback and distinct creation contracts
remain intact. LOW watch:
existing broad service ValueError→VERSION_NOT_FOUND may label a rare concurrent
File deletion as version-not-found; status remains404 and no write occurs.

M06 auto snapshot trigger/lifecycle repair is now independently **APPROVE / CLEAR**:
normal server done uses exact commit-owned mutation flags; prose/read-only/no-op/
all-failed/partial-cancel no longer trigger whole-project snapshots, and ChatPanel
forwards completion metadata. See m06-snapshot-trigger-inventory.md for exact
RED/GREEN receipts and current-project-not-turn-atomic WATCH. Final all-leaf M06
integration review remains pending. All23 module leafs and aggregate
local/necessary CI/PR/merge/exact-SHA production gates remain required. No push,
Actions/rerun, provider/dependency/schema/retention changes in this turn.
