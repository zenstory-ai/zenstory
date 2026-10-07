# M13 user reward/referral timestamp repair — bounded native frontend lane

Ready for independent SOURCE review; no M13/all-module/release closure claim. Root was read-only. All three products and three existing tests matched root at phase start; no sync occurred. Exact root baseline products/tests remain stable at readback. Prior M09 seven-path and M18 five-path candidates remain byte/hash identical to their frozen manifests.

Actual production path: SettingsDialog renders PointsHistory and ReferralStats (root `apps/web/src/components/SettingsDialog.tsx:611,624`); InviteCodeList maps InviteCodeCard (`components/referral/InviteCodeList.tsx:96`). Backend `models/points.py:44` and `models/referral.py:68,191` initialize created timestamps with datetime.utcnow; DTOs expose datetime (`api/points.py:77`, `api/referral.py:49,94`). These are source observations, not live backend/DB measurements. Existing `lib/dateUtils.ts:65` normalizes naive UTC and preserves Z/offset timestamps.

## Genuine proof and minimum change

Before source changes, actual React components with a real QueryClient/local I18nextProvider and only API mocks produced **8 RED / 19 PASS**, 27 ordinary cases, zero skips. Each case explicitly sets and restores TZ=America/Los_Angeles, verifies offset=420, and freezes Date at 2026-10-06T02:00:00Z. It uses shipped en/zh translations and native Storage installed after ordinary repo setup; an unexpected-fetch fence receives zero calls.

| Component/site (candidate line) | Proven pre-source mismatch | Repair |
| --- | --- | --- |
| PointsHistory:50 | Naive 01:55Z equivalent fails the 5-minute label in both locales; aware Z/+08 controls pass. | parseUTCDate(dateStr); same floor/day/hour/minute logic. |
| ReferralStats:144 | Naive 00:32 equivalent shows Oct 6 while the same instant locally falls on Oct 5. | parseUTCDate(created_at); same local locale formatting. |
| InviteCodeCard:59,73 | Naive 01:30 equivalent remains Available/Expires tomorrow despite UTC deadline passing; long expiry local date is one day late. | parseUTCDate in expiry text and status comparison. |

Four parser substitutions and three imports only. No changes to helper, queries, APIs, cache, clipboard/share, mutation, status precedence, day ceiling, future clock handling or server policy. InviteCode has is_active/max/current uses, not a server is_expired field; PointsHistory continues to respect its server is_expired flag. Existing missing and invalid date behavior remains. Z/+08 hour and minute controls, en/zh calendar parity, ceil-day/tomorrow, cap/exhausted/inactive/missing-expiry and Invalid Date cases pass. No currency/quota/financial semantics changed.

## Fresh validation

Working directory for commands: child apps/web. Installed Node v25.9.0; CI Node20 equivalence is not claimed. Wrapper run.py records exact argv, cwd, exit and log; NODE_OPTIONS=--no-experimental-webstorage --max-old-space-size=8192. Evidence Vitest config copies actual repo setup/options, changes only root/envDir=false/cache/include/scoped report locations; thresholds remain 68 lines /55 branches /61 functions /66 statements.

- `red`: installed `node node_modules/vitest/vitest.mjs run --config E/vitest.config.mjs --configLoader native src/components/__tests__/M13.timestamps.test.tsx --reporter=verbose --reporter=json --outputFile=E/red.json` → exit1, genuine 8 failed/19 passed. Frozen pre-source test and baseline snapshots retain the proof.
- `green-coverage`: same runner/config, all four affected suites, `--coverage --reporter=verbose --reporter=json --outputFile=E/green.json` → exit0, **135 PASS (27 new +108 existing)**, zero skips. Scoped three-product coverage: **96.51L /94.62B /77.77F /93.61S**. Per-file summary and reports retained. Existing three tests unchanged.
- Initial `types` → exit2: new fixture omitted matcher typing import and called i18next.off without its required event argument. This is QA typing failure, not a product RED/runtime fixture failure. Fixed only owned test: import jest-dom/vitest and off('languageChanged'); assertions and source unchanged.
- `types-final`: `node node_modules/typescript/bin/tsc -b E/tsconfig.source.json E/tsconfig.qa.json --force` → exit0. Configs force owned source and QA imports with evidence-only buildinfo; source policy/config unchanged.
- `lint-final`: `node node_modules/eslint/bin/eslint.js src/components/points/PointsHistory.tsx src/components/referral/ReferralStats.tsx src/components/referral/InviteCodeCard.tsx src/components/__tests__/M13.timestamps.test.tsx --max-warnings 0` → exit0.
- `green-final-fixture`: final typed fixture, same targeted runner, JSON report → exit0, **27 PASS**. Earlier full coverage applies identical production/assertion bodies; only matcher import and explicit listener cleanup changed afterward. No repeat unchanged full coverage/build.

No invalid runtime tests were counted as product RED. A read-only guessed referral_service.py lookup missed; it was not used as evidence. No test skips/xfails/threshold changes, physical browser, App build, backend/DB/network/provider/payment calls, installs, envfile/secrets, Git or Actions. Build intentionally deferred to coordinator consolidation.

## Ownership, cleanup and limits

Changed exactly five paths: three listed products, new `apps/web/src/components/__tests__/M13.timestamps.test.tsx`, and this new review doc. baseline-manifest.json/final-manifest.json contain exact byte counts and SHA256, source-phase.diff contains only three runtime deltas, owned-phase.diff adds test/doc from absent baselines; neither is a whole HEAD patch. final-source snapshots are exact candidate bytes. Preservation evidence checks six adjacent dependencies, unchanged old tests, root baseline and all twelve M09/M18 candidate paths; it is not a claimed exhaustive worktree audit. Reconstruction verifies all product bytes outside parser substitutions/imports unchanged.

Owned cleanup: React unmounts before QueryClient.clear; local i18next listener removed; fake Date, TZ, native Storage, fetch and mocks restored after each case. No clipboard/download/server/browser/DB resources created. Query/network counts remain one transaction GET or one stats+one rewards GET in corresponding proof cases; no performance/production latency claims. The happy-dom proof establishes actual component date behavior, not physical-device or production SQL/expiry enforcement. Root owns independent source approval, narrow integration, aggregate gates and publication.
