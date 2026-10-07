# M19 C6 dashboard inspirations — bounded source repair

Attributed native frontend lane, ordinary direct solo; independent architect DESIGN_CLEAR supplied by root. Uncommitted candidate for independent SOURCE review/narrow integration. Root owns all23/module closure/release. No root writes, sync or prior evidence modification.

## Result

Regression-first against exact untouched hook: **24 ordinary cases,12 genuine RED and12 PASS**, under native evidence and actual repository config/setup, default+one actual root StrictMode. Source baseline hash `9b2014dd6704946889883775741efed02f22e2098ed35c752fb7095c51b90e22` still exact when RED snapshots/receipts were saved. The24 test bytes at RED and GREEN are identical.

After approved minimum source: **24/24 GREEN under both setups**, **7/7 existing affected cases GREEN**, combined scoped run **31/31 in3 suites**. Zero skips/xfail/fails/quiet-result weakening. All process rejection observations after repair are empty. Source types/QAtypes/ESLint maxwarnings0/scoped coverage pass. No whole App build, browser, backend or broader module gates rerun.

Exactly3 owned candidate paths:

1. `apps/web/src/hooks/useDashboardInspirations.ts` — only production edit; cancellation early return at 47, rejection handler at 56.
2. `apps/web/src/hooks/__tests__/useDashboardInspirations.lifetime.test.tsx` — strengthened acceptance/additional rejection cases; old frozen18 proof kept immutable in separate directory.
3. `docs/reviews/2026-10-06-modules/m19-dashboard-inspirations-repair.md` — new attributed review.

Loader/component/cache/flag/APIclient/global config/other tests unchanged. No new ref/helper/dependency or eviction/retry policy.

## Regression-first proof and minimum repair

The original18 proof had4 cancelednonOK UI RED and14 passing assertions, including positive-unhandled characterization. New desired acceptance converts the two cached-rejection cases to quiet-process assertions, preserving direct loader rejection identity/cache observations; adds mounted owned fetch rejection, canceled A reject after B success, and ABA canceled B reject after current A success, each×default/rootStrict. The matrix is now24: **4 original UI RED+8 genuine quiet-rejection RED+12 passing controls** before source.

Every rejection case uses real hook/loader/leaf/localI18nextProvider and actual mounted primitive effect. Deferred network fetch only is fake. Owned firstfetch rejects (one request); canceled A after successful B rejects (two locale requests); ABA reuses original A pending promise, then canceled B rejects (two requests). Actual process observer records emitted unhandledRejection, and assertions require exact empty emissions. In RED actual emissions sum11 per24-case run: default cached/current/canceled-A/ABA one each; Strict cached/current/canceled-A two each and ABA one. Existing exact B/A suggestion arrays stay committed in canceled-rejection cases, but quiet assertions correctly fail. Four cancelednonOK cases additionally lose current DOM items. No generic runner errors suppressed as allGREEN: observer counts/RED assertions/raw emissions explicit.

Only approved production substitutions:

```ts
if (cancelled) return;
if (!bundle) {
  setItems([]);
  return;
}
// unchanged successful rotation/state handler
// second argument to existing loaderPromise.then:
() => {
  if (!cancelled) setItems([]);
}
```

Effect-local cancellation now makes obsolete completion a no-op before own-null handling. Loader rejection clears items only for current effect. Uses `.then(success,rejection)` so exceptions thrown by the **success handler** still propagate; no trailing catch swallowing unrelated handler errors. `semantic-preservation.json` verifies candidate entire source equals exact two approved substitutions and all imports/exports/types/rotation/deps/cleanup/return bytes unchanged.

## Current controls, actual caller and costs

Actual static path: DashboardHome flag-gated suggestion leaf →useDashboardInspirations→dashboardInspirationSource→`fetch('/generated/dashboard-inspirations.v1.{zh,en}.json',{cache:'no-store'})`. No library API/apiClient/auth/SQL/provider request on this path. Tests mount real leaf independently of optional App rollout, using local real i18n resources; no mocked hook/loader/state/generation. The actual optional config staysfalse in unchanged fixtures; no claim about deployed enabledflag or fullApp reachability.

Preserved: current200 two buttons, exact idea click string, local rotate action, current nonOK empty policy, concurrent same-locale dedup and cached remount, unsupported/no-fetch→supported rerender, old consumer unmount/new live consumer isolation, exact B/A retention under cancelednonOK/rejection and ABA. Closest existing hook suite has3 cases; source-loader suite4. No dedicated existing suggestion-leaf test exists; new actualleaf matrix covers it. Existing DashboardHome tests mock the hook and were not unnecessarily rerun.

**Every request trace is byte/structure-equal before/after across24 cases.** Owned rejection and cached rejection stayonefetch; same-locale concurrent consumers/remount stayonefetch; locale A/B and ABA staytwofetches; unsupported initial stayszero. No additional refresh/retry/cache eviction. Aggregate unhandled emissions **11→0**, live items retained in four prior cancelednonOK failures; current error/null remains empty. Fixture response statuses/cache options/callback/DOM checkpoints in `all-observations.json`/`before-after-measurements.json`. These are local controlled counts, not production latency/load claims.

Resolved null and rejected-promise cache retention remains deliberately unchanged. The direct loader still rejects its cached promise, and hook consumes that rejection quietly. Cache recovery/eviction remains **WATCH**, not a promise of retriable UI; visible “Refresh” rotates cached items and is absent when empty. No automatic refresh policy invented.

## Fresh commands and gates

Cwd `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web`. Existing symlinked node_modules only; NODE_OPTIONS=--no-experimental-webstorage/native configLoader/envDir=false/evidence-only cache/buildInfo. Wrapper outerexit is not gate evidence; table uses actual child exit. All RED commands precede production edit and red-source snapshots freeze hook/test identity.

| Receipt | Child exit | Time | Exact command |
|---|---:|---:|---|
| acceptance-red | 1 | 2.119s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/vitest.evidence.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/acceptance-red.json` |
| acceptance-default-red | 1 | 2.042s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/vitest.default-setup.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/acceptance-default-red.json` |
| native-green | 0 | 2.976s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/vitest.evidence.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/native-green.json` |
| default-green | 0 | 3.017s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/vitest.default-setup.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/default-green.json` |
| affected-existing | 0 | 1.581s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/vitest.affected.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/affected-existing.json` |
| qa-types | 0 | 2.356s | `node node_modules/typescript/bin/tsc -p /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/tsconfig.qa.json --noEmit` |
| affected-lint | 0 | 3.285s | `node node_modules/eslint/bin/eslint.js src/hooks/useDashboardInspirations.ts src/hooks/__tests__/useDashboardInspirations.lifetime.test.tsx --max-warnings 0` |
| source-types | 0 | 12.407s | `node node_modules/typescript/bin/tsc -b /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/typecheck/tsconfig.json --force` |
| scoped-coverage | 0 | 2.869s | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/vitest.coverage.config.mjs --coverage --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-dashboard-inspirations-repair/scoped-coverage.json` |

Scoped coverage includes **exactly modified hook**, unchanged **68L/55B/61F/66S thresholds**. Actual **96.55L /88.88B /100F /96.77S** (28/29 lines,16/18 branches,8/8 functions,30/31 statements). Coverage runs all31 cases, not synthetic sole implementation-mirroring tests. Source forced `tsc -b --force` includes actual src/noEmit strict config with evidence tsBuildInfoFile. QA compiles test and actual hook/loader/leaf dependency imports with noEmit and existing explicit node types for process observer; metadata source entries saved. ESLint owned hook+test maxwarnings0. Local Node25 vsCI20 remains unverified.

**No invalid new-phase fixture/runtime bootstrap/type/lint failures.** Prior proof's missing node declarations/nestedStrict limitation receipts remain immutable and honestly classified there. Actual updated test installs native Storage itself before guards, restores original globals; actual default config/setup inherited unmodified. Root's M23 nine older-test adapters readonly, not synchronized.

## Exact preservation, hashes and cleanup

Phase-baseline saved source/test snapshots read-only before edits; new doc absent. `red-source-manifest.json`/`red-source/` prove unchanged hook and updated acceptance bytes before repair; `final-source-manifest.json`/`final-source/` and `source-phase.diff` provide candidate against this phase snapshot, **not whole HEAD diff**. Hook candidate SHA `e45c977b338bb915519d4b3e5e5679a25c9454b6e89a3fc6183205e78febaa0e`. Acceptance test SHA `2624318f4840e1344ffcb85813593581d060f9b1389623997a81aebe786864fa` (identical RED/GREEN). All16 inspected root prerequisites remain start-identical; child differs only owned hook. **621 enumerated pre-existing unowned child paths** unchanged, including loader/leaf/cacheconfig/F5/C2/Modal/prior docs/tests. No claim all unrelated backend/dependency bytes were hashed; rootM23 adapters outside reviewed scope attributed separately.

All five prior proof/repair evidence directories captured at start stay byte-identical; no old REPORT/STATUS/manifests rewritten. Source-level semantic assertion above locks all other hook bytes. Read-only root and starting-source never edited. Start/end git status preserved.

In every24-case RED/GREEN/coverage observation: unsettledAtAssertion0,pending0,unexpected[],suggestions0/body overflow restored after actual unmount. Fake network gates settled before test assertions. Owned promise/macrotask flushes awaited; production hook/loader has no timers. Query/router not part of this path and not fabricated. Local i18n listener removed; process observer removed with original count asserted; zero final unhandled emissions; Date spy restored individually; native local/session Storage cleared/unstubbed and original references asserted; fetch returned exact original deny fence/default fetch. Actual module cache reset percase solely isolation, shared cache inside each scenario remains real. All owned test/type/lint command processes exited; evidence cache/coverage/metadata retained only here. No server/browser/DB/provider resources created, no install/Actions/network/secrets.

## Stop boundary and remaining gaps

Candidate ready for root independent SOURCE review/narrow integration. Cache-null/rejected retention/recovery policy WATCH unchanged; successful-handler exception propagation unchanged by design, not fabricated as a new reachable fault. Full App feature/auth topology, physical browser, deployment flags/production/backend/CI Node20 not verified. No wholeApp build by explicit bounded authorization. No M19/all23/release closure. Same lane idle; root owns next task and broader gates.
