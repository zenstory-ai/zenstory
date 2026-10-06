# M04 production file-tree frontend review — 2026-10-06

The local M04 frontend slice repairs project-boundary races in both production renderers and restores desktop draft uploads under StrictMode. Source remains uncommitted for coordinator integration. This review compares only with the supplied read-only starting snapshot; coordinator base HEAD is `b88d9af1b90519b226b8200c82fcbc87d47621a1`.

## Bounded plan and runtime architecture

The plan was recorded in the lane evidence `PLAN.md` before source edits: inspect real callers/loaders/search, reproduce defects and lock UX, measure actual renderer costs, make minimal repairs, then run scoped tests/coverage/types/lint/build. The lane stayed solo, local-only; no nested agents, Team/Conductor, remote CI, database, providers, package changes, or publication.

Production desktop routing is `components/Layout.tsx` -> `components/sidebar/Sidebar.tsx` -> `components/sidebar/FileTreePane.tsx`. Its local `renderNode` recursively maps children when folders are expanded (around lines 471/633/728). Production mobile routing is `components/Layout.tsx` -> `components/MobileFileTree.tsx`; its local `renderNode` recursively maps expanded children (around 523/671/817). Mobile also memoizes a recursive title filter (around 482); focused search overlays the full tree, while unfocused search retains matching leaves and ancestor folders without overriding expansion.

Both import the real `hooks/useFileSearch.ts`, `FileSearchInput` and `SearchResultsDropdown`. `useFileSearch` debounces, flattens descendants, preserves parent paths, scores exact/prefix/contains matches and limits dropdown results. Its visited-ID guard is distinct from renderer recursion. No search/traversal code was changed.

`useVirtualizedTree.ts` has no production import. `useFileTreeDragDrop` is exported from `useFileTreeDrag.ts` with no production caller; its `fileTreeDrag.ts` import is an unused leaf path. `fileApi.move/reorder` have definitions/documentation examples, no runtime callers. Tests of those paths do not certify live rendering. No drag/drop or virtualization was wired or removed.

Both renderers own separate tree/loading/expansion/search state. Initial loading and silent `fileTreeVersion` refresh use the file API. Successful same-project create/delete/material upload trigger a full refresh; desktop draft uploads remain sequential, aggregate chapter counts/errors, and perform one final refresh. Mobile provides material upload but not desktop's draft-upload entry. Neither renderer uses optimistic tree mutation/rollback. Tree titles/order, action availability, attachment limits and mobile editor switching are preserved.

## Confirmed findings and minimal repairs

1. **High: project clearing/switching left old files visible or accepted a stale response.** The initial-load effect did nothing when `currentProjectId` became null. Clearing a pending request then resolving it rendered the old file; clearing an already loaded project retained its tree; switching to a pending new project retained selectable old files. Six paired desktop/mobile RED cases proved these behaviors. The project effect now clears the tree, sets the null-project state idle, and aborts on project change/cleanup. Successful responses already checked their signal/request ID; catch handling now also ignores any rejection from an aborted signal.
2. **High: old mutation completion could resurrect the previous tree.** Seven RED cases covered desktop create/delete/material/draft uploads and mobile create/delete/material upload completing after a switch. Each made a third `getTree` call for the old project and replaced the current tree. An active-project ref and loader guard reject obsolete callbacks before they can abort the new request or issue that old refresh. Fixture call counts changed from three to two, with the new file retained.
3. **High: late old-project create/delete completion cleared new-project UI.** Four further RED cases proved old deletion cleared the new selection and old creation closed the new create form. Successful create/delete completion now verifies the active project before selection/form updates. Same-project successful/failing mutation behavior remains covered.
4. **Medium: desktop StrictMode disabled all draft uploads.** Effect cleanup set `mountedRef=false`, but setup never restored it. The next upload returned before its first API call and could leave the uploading state active. Setup now sets it true. The same sequential two-file fixture was GREEN outside StrictMode and RED inside it; both now call uploads in order, translate aggregate errors, emit success, refresh once, and restore the input/button. Real unmount still stops subsequent batch files and suppresses notifications/refresh.

The source repair is limited to existing effects, one project ref/guard per renderer, and successful mutation guards. No dependencies, new traversal abstraction, renderer extraction, or performance rewrite was needed.

## Real-renderer measurements

The offline fixture mounts the actual component with a root folder containing 1,000 draft files. It counts actual DOM elements/file delete controls and mocked production `fileApi.getTree` calls, then collapses the root. SQL and real network latency were not measured; no backend/provider traffic was used.

| Renderer | File rows before/after | Expanded DOM before/after | Collapsed DOM before/after | getTree calls before/after | Mount/query wall ms before/after | Process CPU ms before/after | Collapse wall ms before/after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| desktop | 1000 / 1000 | 16025 / 16025 | 24 / 24 | 1 / 1 | 992.73 / 1458.59 | 1328.64 / 1590.86 | 141.73 / 144.14 |
| mobile | 1000 / 1000 | 15024 / 15024 | 23 / 23 | 1 / 1 | 1311.46 / 2421.55 | 1107.40 / 1495.07 | 191.48 / 307.83 |

Before is `regressions-red-valid.log` without coverage; final after is `final-green.log` with V8 coverage and concurrent validation work. These single-run wall/CPU timings include harness/query costs and have different instrumentation/concurrency. They establish cost receipts, not an acceleration/regression or production latency claim. DOM and row counts are unchanged because renderer code was preserved. Expanded DOM remains proportional to visible files; collapsing prunes descendants without a request. Further virtualization needs a bounded browser/workload study and UX design, not tests of the unused hook. `render-measurements.json` also retains an intermediate noncoverage receipt.

The demonstrated request saving is narrower and deterministic: finishing an obsolete-project mutation now makes zero obsolete refresh calls, changing the switch/mutation fixture from three total tree requests to two. Same-project mutations still refresh normally.

## Phase 1 verification and coverage

Phase 1 final: **121 tests passed across four targeted suites**, including 63 new lifecycle/UX/measurement cases. Component tests import both production renderers and use the real search hook, input and dropdown, while project/material/API/toast boundaries are offline fixtures. Existing search/cancellation/create/delete/mobile/hook tests also pass.

New coverage includes project null/switch races, late mutation refresh and UI races, StrictMode/unmount, generic rejection from an aborted transport, successful/failing create/delete/material/draft uploads, upload ordering/aggregation, confirmation, selection/order/expansion, attachments/limits, search parent paths/filter ancestors, a 24-level live nested renderer, and a 1,000-file DOM fixture.

| File | Lines | Branches | Functions | Statements |
| --- | ---: | ---: | ---: | ---: |
| Total (scoped only) | 94.83% | 86.27% | 88.18% | 93.32% |
| MobileFileTree.tsx | 94.36% | 84.86% | 80.39% | 92.07% |
| FileTreePane.tsx | 94.84% | 86.01% | 93.87% | 93.65% |
| useFileSearch.ts | 96.55% | 96.66% | 100% | 96.61% |

The run scopes V8 inclusion to the two renderers plus their actual `useFileSearch` hook; repository thresholds are unchanged (lines 68%, functions 61%, branches 55%, statements 66%). This is scoped coverage, not full-web coverage. Outputs are in this worktree's `apps/web/coverage/m04-frontend`.

Fresh affected ESLint passes with `--max-warnings=0`. Forced `tsc -b` passes using temporary configs extending the unchanged repository configs, with build-info outputs under lane evidence. The production Vite build and both existing static-page build scripts pass; outputs are local `apps/web/dist` and `apps/web/stats.html`. Exact commands/exit codes are in lane `REPORT.md` and raw logs.

## Invalid receipts, setup deviations and limits

- First RED run included one **invalid mobile search fixture**: it selected the disabled loading searchbox and sent events after that input was detached. Awaiting the loaded Book title corrected the fixture before source edits; mobile search then passed. No source repair was based on that failure.
- The initial spinner hypothesis was not promoted: latest completion sets `isInitialLoad=false`, so a residual `loading=true` alone did not prove a visible spinner defect.
- Initial default `tsc -b` passed but wrote two configured build-info caches under shared `node_modules/.tmp`. This was a constraint deviation discovered after the run. No dependency source/package/lockfile edits or installs occurred; subsequent forced checks relocate caches to evidence-only wrappers. The first wrapper attempt lacked nearest type resolution and failed with TS2688; an evidence-local symlink to the existing installed dependencies restored resolution.
- Vite's runner loader failed on the existing config's `__dirname`. The final build uses native config loading under Node25 with `globalThis.__dirname=process.cwd()` supplied by the validation process. Config/source was not changed for this workaround; standard bundled-loader/CI20 equivalence is not claimed. Build also reports existing stale Browserslist data; no package update was run.
- An intermediate ESLint run exposed a missing `currentProjectId` dependency in the newly guarded mobile delete callback; that dependency was added and final lint is clean.
- Happy-dom does not prove browser layout, scrolling, touch latency or real network/SQL performance. No browser E2E was needed for the effect/handler defects reproduced here. Local Node is v25.9.0, not CI20. No PostgreSQL was used, and no PG-version equivalence is claimed.
- The 24-level fixture certifies that depth only, not unbounded depth. Literal object-reference cycles cannot arrive through ordinary JSON. Duplicate-ID malformed hierarchies and extreme-depth recursive renderer/filter/search limits remain unverified; no speculative graph-normalization layer was added.
- Same-project mutation interleavings, navigation away and back to the same ID while an operation is pending, and mutation notifications after navigation remain separate WATCHs. Pending non-draft completion after unmount is addressed by the phase 2 follow-up below. Backend permissions/mutation atomicity belong to the coordinator's separate backend lanes.

## Exact lane delta and handoff

Compared with the starting snapshot, only these source/document paths are changed/added:

- `apps/web/src/components/sidebar/FileTreePane.tsx`
- `apps/web/src/components/MobileFileTree.tsx`
- `apps/web/src/components/__tests__/FileTree.lifecycle.test.tsx` (new)
- `docs/reviews/2026-10-06-modules/m04-frontend-tree-review.md` (new)

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/`. Snapshot-only patch: `lane-source.diff`; validation receipts, coverage summary, measurements and final report are there. Source is uncommitted. No other module, deferred main-CI, integration, PR/merge/deploy or publication work was undertaken.

## Phase 2: pending non-draft completion after same-project unmount

The coordinator reports independent architect APPROVE/CLEAR, narrow four-file phase 1 integration and fresh parent121GREEN. Phase 1 source is retained read-only in lane evidence `phase1-source`; all four owned files matched it byte-for-byte before this follow-up. Full M04/all23/release remain coordinator-owned; this is only the authorized bounded lifecycle follow-up.

The actual unmount path is grounded: `sidebar/Sidebar.tsx:18-37` conditionally mounts `FileTreePane` by active tab; `SidebarTabs.tsx:24-36` changes that tab. `Layout.tsx:456-479` changes responsive component branches. Phase 1 cleanup aborted an existing request but left `activeProjectIdRef` equal to the project ID. A pending server mutation completing after the tree unmounted therefore still passed the loader/completion guards.

`PHASE2-PLAN.md` was recorded before tests/source edits. The default/StrictMode regression matrix uses both actual production renderers and their real loaders/search/input/dropdown with the existing offline API/context/provider/toast fixtures. It starts create/delete/material upload, actually unmounts without changing the project ID, simulates another panel replacing the shared selection, then resolves or rejects the mutation. Soft assertions independently record request and shared-setter effects; no visible browser corruption is claimed.

**Valid RED:** `phase2-red.log`, exit 1: 12 failed /22 passed /58 skipped. All 12 successful-completion cases made one extra mocked `getTree` call after unmount (default total 1 -> 2; StrictMode total 2 -> 3). The four successful delete cases additionally invoked the captured shared setter with `null`, replacing the fixture's newer selection. The 12 rejection cases were already GREEN. Four nonzero-version loading/refresh cases and six desktop draft cases were also GREEN before repair. There were no invalid phase 2 fixtures or setup receipts.

**Minimal repair:** set the existing `activeProjectIdRef.current=null` in each existing project-effect cleanup, before aborting the existing request. Existing effect setup restores the project ID, so StrictMode double setup and subsequent project loading work. Existing loader and successful create/delete guards then fence stale refresh/shared-selection writes. The production phase 2 delta is two assignments and explanatory comments. No renderer/search/mutation API policy or dependency/framework/generation layer changed.

Notification policy is preserved: completed material upload still emits its global success toast in both renderers after hiding; rejected create/delete still emit their existing global error toasts; desktop material failure retains its error toast. Mobile material rejection retains its existing local-error policy, with no invented global toast. Desktop draft uploads keep their existing separate mounted-flag policy: default and StrictMode batches remain sequential, and real unmount stops subsequent files and suppresses draft notifications/refresh.

**Final GREEN:** `phase2-targeted-green.log`, exit 0: 34 passing /58 skipped; `phase2-final-green-coverage.log`, exit 0: 150 passing across four suites, including 92 lifecycle-suite cases (29 added in this phase). Scoped coverage is 94.85% lines (498/525), 86.76% branches (354/408), 88.18% functions (97/110), 93.34% statements (519/556). Existing thresholds remain 68/55/61/66% for lines/branches/functions/statements. Affected ESLint with zero permitted warnings, forced evidence-only typecheck, Vite production build and both existing static-page builds all pass.

Phase 2 Vitest uses the installed `startVitest` native-config entrypoint, `cache:false` and evidence-only Vite `cacheDir`; build uses native configuration loading and evidence-only `cacheDir`. The existing evidence-only type wrappers are reused. No phase 2 shared dependency/cache/config/package writes were performed. Outputs stay in the worktree (`coverage/m04-frontend-phase2`, `dist`, `stats.html`) or lane evidence. The phase 1 cache-write deviation remains documented as historical; it was not repeated. Local Node25/CI20 and happy-dom/offline-network limitations remain.

The demonstrated performance effect is elimination of exactly one stale post-unmount tree request per successful-completion fixture; this is mocked request count, not production latency. The unchanged 1,000-file renderer still produces desktop 16,025/24 and mobile 15,024/23 expanded/collapsed DOM elements. Phase 2 local wall/CPU receipts are saved separately and are not compared as speed claims.

The same four owned paths constitute the combined lane source delta. `source-baseline.diff` and `lane-source.diff` compare the combined work with the original starting-source; `phase2-delta.diff` compares only this follow-up with phase1-source. `phase2-source-hashes.json` records baseline/final hashes for narrow three-way integration. Latest STATUS/REPORT summarize exact receipts; phase 1 reports/patches remain archived. Stop condition: bounded follow-up verified, uncommitted in this lane, ready for coordinator integration. Same-project overlapping form/selection and A->B->A generations remain WATCHs without mandatory scope expansion.
