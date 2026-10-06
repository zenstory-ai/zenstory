# M08 bounded UI lifetime repair — native frontend lane

Candidate ready for independent root SOURCE review. Four root/child product baselines matched exactly; no synchronization was needed. The valid untouched-source matrix was **19 RED / 6 compatibility PASS**. All **25** cases now pass; the five affected suites have **70 PASS**. This is a bounded UI candidate, not M08/all-module/release closure.

## Proof and minimum repairs

| Leaf | Grounded observation before repair | Candidate and current contract |
| --- | --- | --- |
| C2 resources | Closing a pending content request reopened the editor or showed its late error. Old save/delete closed a replacement editor; old-skill failure polluted its replacement; a save completed after actual unmount made one extra list request and one parent callback. | `SkillResourcesSection.tsx:81,102,141,179,310`: fresh owner per skill/mount, existing list/content sequences invalidated on cleanup/Close; mutation presentation must match owner/editor. Same-skill committed save/delete still reloads and calls onChange once, while replacement draft remains. Departed save now makes zero extra requests/callbacks. |
| C3 own-list search | Actual 300ms search A then B, B first then A, replaced B rows with obsolete A in default and one root StrictMode replay. | `SkillsPage.tsx:122,144`: one request sequence plus page owner gates rows/error/final loading. Same requests/debounce/cache/ordering/API remain. No request cancellation or speculative performance rewrite. |
| C4 main edit/import | Pending create A, Cancel, new form B; A completion reset/closed B. Same-ID edit reopen lost the new draft. Import success after actual unmount started one extra collection request. | `SkillsPage.tsx:314,333,411,464`: form generation increments on Create/Edit/Cancel; current-page successful server write may refresh, but only its form may close/reset. Import requires live page before result/tab/reload. Departed import now starts zero extra collection requests. Existing pending lock, live import warnings/tab/list refresh, validation/quota and ordinary global error notifications preserved. |
| C5 sharing | Actual SkillsPage share A pending, Cancel, open B; A success closed B in default and root StrictMode. | `ShareSkillModal.tsx:33,45`: fresh owner per mounted skill; stale completion makes no close/success/error/loading writes. Same-current success still calls both callbacks once; result failure remains visible/retryable. |
| C6 stats | Range30 then range7, 7 first then30, replaced current7. Old project error released newer project's spinner before its response. | `SkillStatsDialog.tsx:25`: effect-local cancellation gates result/log/finally across open/project/range/Strict replay. Latest current request owns loader. Owned failure policy is unchanged: initial null shows blank, and previous owned stats are retained on a later current failure. |

All observations use actual React components, actual Modal/focus behavior, local real i18next translations and ordinary DOM edits/buttons. The sharing replacement is driven by its actual SkillsPage parent, not a synthetic parent model. API methods and the ProjectContext/notification boundaries are mocked; these receipts therefore prove frontend API invocation and visible caller behavior, not HTTP/SQL/storage commit or physical-browser behavior. Server operations already issued are allowed to complete. Object identity is fresh at effect setup, so Strict cleanup cannot revive an old continuation. No shared framework/helper, new UX, auth200 policy, API/context/backend/flag or dependency changes.

## Regression receipts and fixture attribution

`red-matrix.json`, `.log`, `.receipt.json` and `red-classification.json` are the authoritative valid pre-source evidence: 25 ordinary cases, 19 failed, 6 passed, 0 skipped; all 25 cleanup observations were clean. `red-test/` preserves exact pre-source test bytes. Default plus one root StrictMode registration covers resource Close/replacement save, search/form and actual share replacement, and stats range; default controls additionally cover actual unmount, skill change, content rejection, same-ID edit and delete replacement.

Earlier receipts remain intact: `red-initial` mixed real findings with invalid mobile/Create selectors and an incorrect expected initial-null stats/Close label; those were fixture errors, not product REDs. `red-valid` and `red-final` are intermediate corrected probes; the final valid matrix supersedes their counts. No skips/fails/weakening. The first QA type attempt exited2 because matcher types were absent, role queries used an unsupported ignored `exact` option, and `i18n.off()` required named events. Only the new fixture was adapted: explicit jest-dom import, default exact-string role queries, and named cleanup events. `test-typing-adapter.diff` records this entire delta; no behavior assertions changed. Its fresh 25-case run/types/lint pass.

## Fresh validation

All commands ran in child `apps/web` with existing installed dependencies, Node **v25.9.0**, `NODE_OPTIONS=--no-experimental-webstorage --max-old-space-size=8192`. The Node25/CI20 gap remains. Evidence-native config is the actual repo Vitest options and **actual `src/test/setup.ts`**, with explicit absolute root/imports, envDir:false and evidence-only cache. The new test installs native Storage per case before its guard; it runs under the repo's normal setup. Local translation resources avoid any locale fetch. This was not a separate unmodified-config invocation.

Exact argv/cwd/exits/timing are in `commands.json` and each receipt. The runner wrapper exits0 even for a failed inner command; all statuses below are the inner recorded exit.

| Receipt | Inner exit | Evidence |
| --- | ---: | --- |
| red-matrix | 1 | 19 genuine RED, 6 PASS, untouched products |
| green-related | 0 | New25 + SkillsPage39 + resources1 + share2 + stats3 = 70 PASS / 5 suites |
| green-final | 0 | Final test adapter bytes, 25 PASS / 1 suite |
| source-types | 0 | Forced tsc-b over actual app source tree/noEmit; evidence tsBuildInfoFile |
| qa-types | 2 | Preserved fixture typing failure described above |
| qa-types-final | 0 | Forced strict QA compile of final new test and its production imports |
| affected-lint | 0 | Four candidate products + new test, max-warnings0 |
| qa-lint-final | 0 | Final test adapter, max-warnings0 |
| coverage | 0 | 70 PASS, exactly four modified products, unchanged thresholds |

Reproduce using `E=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m08-ui-lifetime-repair` and the same Node options:

```sh
node node_modules/vitest/vitest.mjs run --config "$E/vitest.default.config.mjs" --configLoader native
node node_modules/vitest/vitest.mjs run --config "$E/vitest.coverage.config.mjs" --configLoader native --coverage
node node_modules/typescript/bin/tsc -b "$E/tsconfig.source.json" --force
node node_modules/typescript/bin/tsc -b "$E/tsconfig.qa.json" --force
node node_modules/eslint/bin/eslint.js src/pages/SkillsPage.tsx src/components/skills/SkillResourcesSection.tsx src/components/skills/ShareSkillModal.tsx src/components/SkillStatsDialog.tsx src/components/__tests__/Skills.lifecycle.test.tsx --max-warnings 0
```

Coverage: **84.16% lines / 70.74% branches / 77.53% functions / 81.89% statements**, above unchanged **68/55/61/66** thresholds. Per-file L/B/F/S: SkillsPage 80.27/67.11/69.69/78.14; resources88.18/76.19/95.83/84.45; share100/77.27/100/97.14; stats100/89.28/100/100. Coverage/source-types/product-lint use identical final product bytes. Coverage/70-case run preceded only the nonsemantic QA fixture adapter; final25 was rerun after that adapter. No unrelated test loops. App build deferred to root consolidation as authorized; no current build claim.

## Ownership and preservation

Exactly four products, one new test and this new doc changed. Existing affected tests stayed untouched. `baseline/` + `baseline-manifest.json` store exact starting product bytes. `source-phase.diff` is only baseline-to-candidate product delta; `owned-phase.diff` additionally includes the new test/doc, never the entire HEAD diff. `final-source/` and `final-manifest.json` are the exact six-path handoff. Baseline/final hashes and bytes below are also in the manifests.

`preservation-readback.json` confirms all four current root products still match this phase baseline, and the read-only api.ts, four existing affected tests, Modal and test setup match current root at final readback. This is a scoped preservation check, not a new whole-worktree audit. Frozen C1 api.ts SHA remains `0dc00a813b998fe823174dcb96dc79a5160c0f9ee85b06d2887b0c230a445fa3`. Previous handoffs/reports/manifests were not rewritten; root is read-only.

- `apps/web/src/pages/SkillsPage.tsx`: 58052 → 59354 bytes; final SHA256 `26d178e9b2a19a6684dfd3b6e1160d019083f8709bafb1998fe36658f463d788`.
- `apps/web/src/components/skills/SkillResourcesSection.tsx`: 12441 → 13731 bytes; final SHA256 `4978e43c538421f4ac92c91fea0c6e55b92802149abb2b67c516fd3fc141ba56`.
- `apps/web/src/components/skills/ShareSkillModal.tsx`: 3111 → 3571 bytes; final SHA256 `e628e3f2c2672fa73d1e953a2ed5c6ee588206cc9f7b482efba2b51c0c18d424`.
- `apps/web/src/components/SkillStatsDialog.tsx`: 7073 → 7188 bytes; final SHA256 `00a9d1c1da74486f7dcce9a6d51cae61f387728e7b98ce38720dcb5d051c4f13`.
- `apps/web/src/components/__tests__/Skills.lifecycle.test.tsx`: new → 22580 bytes; final SHA256 `5bb225e928f05da8db312ff284fa2cec5113b0c5c898825780737ecf0bb87a80`.

## Cleanup and remaining boundaries

Valid RED, first70 GREEN and final25 GREEN each record 25 clean lifecycle observations: 0 unresolved deferred gates, 0 timers, 0 dialog portals, restored body overflow and no unexpected fetch. Each test restores native local/sessionStorage, global fetch/matchMedia/mocks/timers and named local i18n listeners after actual unmount. No server, browser, DB, provider, network, credentials, installs, shared cache, Actions or Git mutation occurred.

KEEP/WATCH: the existing same-skill/page pending lock is retained until its old mutation settles; this candidate does not authorize concurrent write queues. Full unfiltered post-mutation reloads, mounted import tab intent, public discovery/category request lifetimes, and previous stats retained on a current error were not redefined without a genuine contract/proof. No latency/performance, full App/auth/query isolation, physical browser or backend finalization claim. Quota/readOnly/path/size validation and broader usage/discovery controls rely on affected existing tests plus remaining untested branches, not a claim of complete module coverage. Root owns independent source approval, narrow integration, aggregate build/gates and all23/release.
