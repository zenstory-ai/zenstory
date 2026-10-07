# M19 C1/C7 dialog lifetime and selection — bounded source repair

Attributed standalone native frontend lane, solo/direct, local-only. Independent architect DESIGN_CLEAR was supplied by root; this is an uncommitted SOURCE candidate for root independent review/narrow integration. Full M19/all23/release remain root-owned.

## Result and scope

Frozen proof remains immutable: **26 ordinary cases, 8 genuine RED and 18 compatibility PASS**, default and root StrictMode, under native evidence and actual repository configs. The unchanged exact two new test files now produce **26/26 PASS under each setup**. Four closest existing suites produce **116/116 PASS**; combined scoped-coverage run produces **142/142 PASS in six suites**. No skip/fails/xfail, weakened assertion or test adapter/source change was needed in this phase. Eight fresh gate commands exit0.

Actual source changes only:

- `apps/web/src/components/inspirations/InspirationDetailDialog.tsx:71`: keyed Content-local fresh object lifetime per effect setup; matching-owner cleanup invalidates it and clears its stored close timer. Copy captures lifetime before await. Only same live owner enters copied state/schedules the timeout; callback checks that owner, clears timer ref, resets local state and calls the captured close callback. Parent copy/toast/project navigation remain unchanged; issued writes are not cancelled.
- `apps/web/src/components/inspirations/InspirationGrid.tsx:143`: existing hook returns detail/null with F5 ownership already enforced. Caller now opens only for returned detail. No redundant identity/generation or hook/Modal changes.
- `docs/reviews/2026-10-06-modules/m19-dialog-selection-repair.md`: this new attributed review.

Two owned new tests are baseline-identical, retained as handoff dependencies: `apps/web/src/components/__tests__/InspirationDetailDialog.lifetime.test.tsx` and `apps/web/src/components/__tests__/InspirationGrid.selection.test.tsx`. They already self-install native localStorage/sessionStorage before guards to work with ordinary src/test/setup.ts. Existing source/tests/configs outside exact ownership remain protected.

## Genuine mechanism and observed before/after

C1 six original REDs = three actual triggers ×default/root StrictMode: actual InspirationDetailPage copy success PUSHed `/project/new-a`, then an unmounted dialog's 1500ms callback POPped to `/dashboard/inspirations/a`; completed A timer closed replacement B; and pending A copy completed after A close/B selection and created a timer that later closed B. C7 two REDs: real Grid waits A/B detail, B opens and closes, obsolete A completion returns null from actual hook but Grid unconditionally reopens B.

After repair routed destination stays `/project/new-a`; B remains open with its own input; obsolete null does not reopen B. All existing controls retain live copied state, pending disable, exact1499/1500 close timing, error retention, first/latest selection, ordinary close, actual hook latest response, search debounce/filter and real remount behavior.

Fixture request counts: routed-copy default **6→5**, root StrictMode **8→6**, because obsolete POP no longer remounts the old page or issues its extra detail GET (Strict effect replay issued two). All other 24 cases have unchanged request counts. Every copy POST count and global success-notification count stays unchanged: relevant successful copy cases issue one POST/one success; failures issue no false success. Grid stale-selection scenario stays four requests (list/featured/A/B), with B closed after old A completion. These are deterministic local fixture counts, not production latency/load/corruption claims. Raw headers, payloads, response status, router commits and per-stage DOM/timer state are in `observations.json` and `before-after-measurements.json`; frozen raw baseline remains in the prior proof directory.

## Real renderer/caller proof

Tests execute actual Page/Grid, real useInspirations/API/client/refresh ledger, actual DetailDialog/Modal/Card, QueryClient, createMemoryRouter/RouterProvider and local i18next provider. Only fetch recognized fake API responses and peripheral analytics/toast boundaries are mocked. One production-shaped root subtree with optional root StrictMode, no co-mounted fake generation/hook/nav/dialog model. Target project workspace is a route marker fixture; full App auth/feature flags or physical browser are not asserted.

Both configs keep happy-dom; native fixture denies unknown fetch and uses native Storage. Ordinary config wrapper imports actual immutable vitest.config.ts/src/test/setup.ts; overrides root/native-loader dirname/envDir=false/evidence cache/include only. New tests override setup's mock Storage locally and privately restore globals. No product/config/framework/dependency changes.

## Fresh gates

All commands run from `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web` using existing symlinked installed node_modules. Wrapper stores **actual child exit** in receipts; its outer wrapper exit is not used to claim GREEN. No install or npx fetch. Native Node **v25.9.0**, CI Node20 parity is not established.

| Gate | Child exit | Elapsed | Exact command |
|---|---:|---:|---|
| native-26 | 0 | 2.767s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/vitest.evidence.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/native-26.json` |
| default-26 | 0 | 3.251s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/vitest.default-setup.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/default-26.json` |
| affected-existing | 0 | 2.68s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/vitest.affected.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/affected-existing.json` |
| affected-lint | 0 | 4.417s | `node node_modules/eslint/bin/eslint.js src/components/inspirations/InspirationDetailDialog.tsx src/components/inspirations/InspirationGrid.tsx src/components/__tests__/InspirationDetailDialog.lifetime.test.tsx src/components/__tests__/InspirationGrid.selection.test.tsx --max-warnings 0` |
| qa-types | 0 | 4.669s | `node node_modules/typescript/bin/tsc -p /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/tsconfig.qa.json --noEmit` |
| scoped-coverage | 0 | 8.327s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/vitest.coverage.config.mjs --coverage --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/scoped-coverage.json` |
| source-types | 0 | 21.466s | `node node_modules/typescript/bin/tsc -b /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/typecheck/tsconfig.json --force` |
| app-build | 0 | 16.491s | `NODE_OPTIONS=--no-experimental-webstorage node /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dialog-selection-repair/build.mjs` |

Scoped coverage includes exactly the two modified product files, unchanged thresholds **68 lines /55 branches /61 functions /66 statements**. Actual aggregate **96.22L /85.39B /100F /95.53S** (102/106 lines,76/89 branches,33/33 functions,107/112 statements). Source forced tsc -b config extends actual noEmit/strict app config, points root at actual src with evidence-only tsBuildInfoFile; two product root entries recorded. QA config imports both actual renderers through tests and real parent dependency graph and uses noEmit; type-source-provenance.json proves entries. ESLint four owned source/test paths maxwarnings0.

One Vite **App** build from actual source with unchanged actual Vite config, configLoader native/envDir=false, evidence-only cache/outdir/cwd. Installed Tailwind v4 scan base explicitly apps/web compensates evidence cwd; no utility injection. CSS `dist/assets/index-BYMu9Xg0.css` **174745 bytes**, includes `.hidden`, `.md\:flex`, `.w-80`, `.h-12`, `.z-50`. `build-command.json`/`css-provenance.json` capture provenance. Org/docs static postbuild scripts and browser runtime were not rerun; no full site/CI/global closure claim.

## Invalid/historical accounting

No new runtime/bootstrap/test/type/lint/build failure in this source phase. First ad-hoc metadata inspection incorrectly expected fileNames in build-mode tsbuildinfo and raised KeyError; corrected inspection uses actual root list. This is an inspection error, not product RED or gate failure. Prior proof's alias/bootstrap and initial test-only types errors remain frozen and classified INVALID, never reclassified as product RED. Prior C2 default-setup adapter is root-attributed drift in its test file; no sync/edit in this lane. Frozen C2/native receipts remain immutable.

## Preservation and ownership

34 reviewed source/test prerequisite paths matched root exactly at phase start; all34 root bytes remained unchanged at end, only these two child products differ. Four existing owned paths saved read-only before repair; new doc absent baseline. `phase-baseline-manifest.json` and `starting-source/` are immutable phase basis, **not whole HEAD diff**. Dialog outside React import/Content local copy lifetime and render tail byte-identical; Grid outside handleView byte-identical. Two tests untouched. **616 explicitly hashed unowned child paths** (all captured Web source/test/docs/E2E/config prerequisite scope) unchanged. This enumerated check is not a claim of hashing every unrelated backend/dependency file. Start/end git status and prerequisite hashes retained.

All files in prior proof and C2 proof/repair evidence captured at source start stay byte-identical, including old snapshots/reports/manifests. `final-source/`, `final-source-manifest.json`, `source-phase.diff` and `owned-change-manifest.json` provide exact candidate/scope. Final source review/integration has not been claimed.

## Cleanup and remaining WATCHs

All26 observations in each native/default/coverage run show unsettledAtAssertion0, pending0, unexpected[], zero timers after actual unmount/query cleanup and after explicit fixture clear, zero Modal portals, body overflow restored. Escape after unmount changes no route. Owned gates/pending requests settle, QueryClients cancel/clear, router listeners unsubscribe/dispose, i18n listener removed; clearAuthStorage clears owned ledger, native Storage cleared and globals/timers restored. No owned server/browser/DB/provider resource created. Build/cache/coverage artifacts retained only under new evidence path; shared dependencies/configs untouched.

WATCHs not repaired: success:false semantics; parent pending copy after unrelated manual route departure; latest-own detail error UX; physical browser/full App flags/auth integration. No server-write cancellation/undo/distributed contract or module SOURCE closure inferred. Scope ends at this candidate and evidence, ready for root independent SOURCE review; lane returns idle.
