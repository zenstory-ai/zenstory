# M20 HomePage timer candidate — bounded source handoff

Status: COMPLETE candidate for independent SOURCE review, uncommitted. Conditional independent DESIGN_CLEAR received before source repair; genuine actual-component pre-RED recorded first. Root alone owns integration, static legal/route registry work, module closure and release. Evidence directory: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof`.

## Evidence and minimum repair

Current authoritative root and child HomePage began byte-identical (`b187a8031ec6ace9c9ec11a1da4798a526eb33fb52b17918e8e9cba1484c9ffa`). Existing HomePage tests and 15 other reviewed dependency/config/locale leaves also matched; the initial `icons.tsx` lookup miss was resolved to actual `components/icons.ts`, never counted as equality. No source synchronization was necessary. Seventeen reviewed source/config/test/locale paths are recorded in baseline-manifest.json. End comparison shows only the authorized child HomePage changed; root and sixteen other reviewed leaves stayed exact. Frozen earlier phases were neither edited nor regenerated.

Pre-source: **10 RED / 14 PASS**, all ordinary tests, no skip/fails and no invalid bootstrap/timing cases. Six failures were visible scene-state regressions, four were resource cleanup only. Each new case runs default and one root StrictMode:

| Actual trigger | Before | After |
|---|---|---|
| Click Edit at 5000ms; manual commits at 5300; old automatic cycle completes at 5340 | Manual-selected scene loses its active indicator; old callback executes `prev + 1` (Edit → Create) | Edit indicator and real File Updated scene remain |
| Automatic transition pending at 5040ms; enable reduced motion before 5340 | Scene changes despite reduced-motion reset | Current Create remains |
| Manual Edit pending; enable reduced motion at 100ms and choose Suggest immediately | Old Edit callback replaces Suggest at 300ms | Suggest and its real Writing scene remain |
| Actual unmount with pending automatic or manual transition | One owned timeout remains (1), DOM already unmounted | Zero timers (0) |

Unmount failures establish a leaked pending callback resource, **not visible corruption after unmount**. Fake-time millisecond boundaries are fixture timing, not production latency or physical-browser measurements. The 80ms interval first reaches its 5000ms threshold at 5040ms. Assertions check ordinary scene buttons and actual SceneCreate/SceneSuggest/SceneEdit content; no fake carousel/state/request model.

Final source adds one component `sceneTransitionTimeoutRef` (HomePage.tsx:56). Both callbacks clear the ref before winning state updates. The automatic effect clears timeout/interval on cleanup and pending timeout before reduced-motion setup (162-203); a deadline tick yields when a manual transition is already pending (184-185). This guard is necessary for the proven 5000/5300/5340 case: blindly replacing the pending manual timeout with an automatic timeout would discard the user choice. Manual selection clears prior pending transition before its own 300ms schedule (206-227). Existing active-scene no-op remains before that cancellation, as approved. No new hook/framework, button disabling, memoization, navigation/CTA/metrics, translation or rollout changes.

## Validation

Final **24 PASS = 16 new + 8 existing**, default/root StrictMode lifetime controls. The two already-pending-auto → newer-manual cases and two rapid-distinct-manual/latest-choice cases were positive before repair and remain positive. Ordinary automatic/manual/current-scene no-op, 299/300ms transition boundaries, 80ms progress, reduced-motion immediate/manual and 20s no-rotation controls pass. Existing English/Chinese metrics, registration-disabled entry, project CTA source attribution/plan preservation and keyboard-focus controls remain passing. The original existing test file is unchanged.

Scoped HomePage v8 coverage: **89.65% lines / 77.14% branches / 86.48% functions / 89.40% statements**, unchanged thresholds **68/55/61/66**, exit 0. Source and QA forced TypeScript builds explicitly include production HomePage, with evidence-only tsBuildInfoFile. Impacted ESLint `--max-warnings 0` passes. Actual repository setup.ts runs; new tests replace its mock Storage with native happy-dom Storage locally, use real local i18next resources, MemoryRouter, HomePage children, useMediaQuery and PublicHeader. Only AuthContext boundary is mocked; matchMedia is a controlled browser facility, fake timers control real Date/timer callbacks. Unexpected fetch throws and every test verifies zero fetches. No providers, browser, backend, server or builds executed. Build is deferred to root consolidation as instructed; Node25.9.0/happy-dom does not establish CI20 or physical-browser behavior.

Command cwd: `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web`. Runner sets `NODE_OPTIONS=--no-experimental-webstorage --max-old-space-size=8192`; its receipt records the inner command exit (wrapper itself returns 0). Config uses repository plugin/setup/thresholds and isolated fork policy, with envDir:false and evidence-only cache/output. Commands/results:

| Gate | Exact command | Exit |
|---|---|---|
| pre-source | `node node_modules/vitest/vitest.mjs run --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof/vitest.config.mjs --configLoader native --reporter default --reporter json --outputFile /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof/pre-source.results.json` | 1 |
| candidate | `node node_modules/vitest/vitest.mjs run --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof/vitest.config.mjs --configLoader native --coverage --coverage.include src/pages/HomePage.tsx --coverage.reportsDirectory /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof/coverage --reporter default --reporter json --outputFile /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof/candidate.results.json` | 0 |
| source-types | `node node_modules/typescript/bin/tsc -b /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof/tsconfig.source.json --force` | 0 |
| qa-types | `node node_modules/typescript/bin/tsc -b /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m20-home-timer-proof/tsconfig.qa.json --force` | 0 |
| lint | `node node_modules/eslint/bin/eslint.js src/pages/HomePage.tsx src/pages/__tests__/HomePage.timerLifecycle.test.tsx --max-warnings 0` | 0 |

Raw logs, JSON results and case-classification.json retain exact RED/GREEN facts. No source edits were based on a static-only candidate. No unrelated gates were repeated.

## Owned paths and preservation

Only `apps/web/src/pages/HomePage.tsx`, new `apps/web/src/pages/__tests__/HomePage.timerLifecycle.test.tsx`, and this new attributed review doc changed. source-only.diff and source-phase.diff compare the immutable phase baseline, not HEAD. final-source contains exact three-path copies, final-manifest.json contains exact byte counts/SHA256, baseline snapshots and valid RED receipts stay immutable.

- `apps/web/src/pages/HomePage.tsx`: 50733 → 51650 bytes; final SHA256 `8070f23abd0e72e9345f8b3587450a02a133d6a252be7df37cb375a6260ed1d8`.
- `apps/web/src/pages/__tests__/HomePage.timerLifecycle.test.tsx`: None → 7271 bytes; final SHA256 `9927c6f22ed9de8a4418abdb24015e2ec8f4b7131604f2f9fcb2e3c43086e265`.

The rest of HomePage (rendered layout, scene definitions and child functions, metrics, CTA/navigation helpers) is byte-preserved apart from shifted line numbers. Root products/config/tests, static legal/registry ownership, all shared dependencies and older frozen phases are untouched.

Cleanup: actual React unmount/RTL cleanup removes real media listeners (per-test listener-set assertion); fake timers are drained only after pre-RED unmount assertions and then restored; zero timer count is asserted during cleanup; local native Storage cleared and original globals restored; i18next listeners detached, fetch spy verified unused, mocks restored. No resource/service process started and no unknown files deleted. Evidence cache/build-info/coverage are lane-owned.

Gaps/WATCH: no full-App/auth/flags, physical browser/animation, SSR, reduced-motion OS interaction or production latency claim. Pending-transition current-active-button click retains the existing no-op policy; this bundle does not redesign it. Automatic transition already pending then later manual succeeds was already a passing compatibility case, not claimed RED. Root static privacy/ordinary sitemap work and whole M20/M21 closure remain separate. Stop at candidate handoff.
