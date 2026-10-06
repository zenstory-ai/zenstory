# M16 frontend audit/repair — native frontend lane

**Candidate ready for root independent SOURCE review.** Valid untouched proof **11 RED /8 compatibility PASS →19 GREEN**; final scoped **60 PASS /6 suites**. Source/QA types and lint0 pass; five-page coverage thresholds pass. This is a bounded frontend handoff, not M16/all23/release closure.

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m16-ui-repair` (E). Root backend/API ownership remains separate.

## Baseline and complete owned-leaf review

Only five authorized pages and five existing tests were compared/synchronized from authoritative `/private/tmp/zenstory-module-audit-20261006`. Plan/subscription/code page+test pairs differed: six exact-root copies. Payment/quota pairs already matched. `baseline-manifest.json`, `child-before-sync/` and `baseline/` attribute this prerequisite. Root's four pending header locks and eight cases remain intact; their old valid baseline was reused.

Actual root callers: `apps/web/src/App.tsx:65–77,588–609` lazy admin routes under AdminRoute/AdminLayout; `components/admin/AdminSidebar.tsx:54–75,102` navigation. Each owned page's full implementation was inspected. Candidate page refs below; API refs are read-only `apps/web/src/lib/adminApi.ts`.

| Leaf | Live contracts and A/P/B judgment |
| --- | --- |
| SubscriptionPlanManagement:164–263,266–489 | GET plans(:1383), catalog cards, prices and boolean/-1/array/extra feature display; hidden unenforced writing/agent keys and flagged inspiration copies. Edit captures names/cents/active/features JSON. Syntax failure blocks PUT; updatePlan(:1392) invalidates plans/closes/toasts; failure retains form. Header/footer pending parity stays. **KEEP**, product baseline exact; real-query error/retry/success and existing JSON/payload/flag cases pass. |
| SubscriptionManagement:203–397,400–811 | Keyed page/status GET(:1053),20/page/30s; plan lookup(:1383)/60s. Responsive cards/table use effective plan/status aliases, no-record free/long-term labels and localized price-sorted plan options retaining unknown current plan. Details show period/date/time. Modify rejects no-op/missing positive duration for plan change, permits status-only, PUT(:1136) invalidates/closes/toasts. Root pending close/footer locks preserved. **REPAIR** date/dateTime parser only; remaining contracts **KEEP**. |
| CodeManagement:95–251,254–735 | Keyed GET(:903),20/page/30s; tier/active filters/reset/page1, cards/table/copy, per-row pending identity, confirmation before deactivation/direct activation. Single(:933) tier/duration/type/max uses/notes and batch(:949) count/type/notes; success invalidates/reset/closes/toasts using returned count fallback; update(:976) invalidates/toasts. Root single/batch pending close locks preserved. **REPAIR** creation-date parser only; other contracts **KEEP/WATCH**. |
| QuotaManagement:10–72,75–330 | Stats GET(:1768); entered exact username/email/user ID activates keyed GET(:1779) only after nonempty trimmed Search/Enter. Separate keys isolate async user data. Stats/user loading/error/retry/empty, user/plan and conversation/decomposition/skill/gated inspiration quotas; -1 unlimited hides meter,0 limit100%, color thresholds70/90 and width cap100. No override/write/checkout. **KEEP**, product baseline exact; real-query delayed A after B preserves B, existing search/error/flag controls pass. |
| PaymentOrderManagement:23–88,91–218 | Query includes page/payment status/explicit trimmed search/fulfillment/attention,20/page; filter/search reset page, Refresh/pagination disabled while fetching. GET(:1099), attention count, localized cents/UTC dates, stored-status fallback and detail fields. Sync POST(:1118) for unfulfilled order, current result/error reason and broad list invalidation. **REPAIR** stale detail callbacks only; requests/current outcomes/pending/error-retry **KEEP**. These pages do not execute browser checkout/refund/deletion/provider flows. |

Architecture/performance KEEP: existing Query ownership and AdminPageState/Select/Modal/ConfirmDialog suffice. Reuse UTC helper; one details generation fences presentation while keeping broad invalidation. Code/subscription render both responsive representations (up to40 for20 items); plans render returned catalog and parse/stringify feature JSON on edits. No per-row requests in these leaves. Large catalog/JSON costs need measurement before optimization; no measured bottleneck or SQL/production latency claim. No shared framework/cache/retention redesign.

## Proven defects and minimum repairs

**Dates, P2/high confidence (4 RED):** given backend-naive UTC contract, `2026-10-06T00:32:00` must equal the explicit-Z instant. In test-owned Pacific/Honolulu it formerly displayed October6 00:32 instead of October5 14:32, ten hours late. Actual en/zh code table and subscription list/details datetime fail (both list+datetime assertions recorded). `CodeManagement.tsx:239` and `SubscriptionManagement.tsx:349,365` now reuse existing `parseUTCDate`; original locale/options/invalid/null fallthrough retained. Z/+08:00 controls preserve aware instants. Long-term2120 classification is unchanged, pending calendar/sentinel policy evidence.

**Order sync, P2/high confidence (7 RED):** actual page+real QueryClient/Mutation+real Modal: pending A→Close then success reopened A; Close→open B then A success replaced B; A error appeared in B. Default and one root Strict replay each reproduce these three. Same-ID Close/reopen also receives obsolete success. `PaymentOrderManagement.tsx:34,47,79,84,199`: capture details generation in mutation variables; advance on Open/Close/unmount; only matching local success/error may update detail state/message. Valid server POST is allowed to finish; broad successful invalidation remains outside the fence. B stays pending-disabled until old A settles, preserving current pending lock. No write cancellation/global auth200/financial policy.

Only existing PaymentOrderManagement test's internal mutation adapter changed: pass captured variables into callbacks. API/output/error assertions unchanged. New M16.managementLifecycle test uses actual five pages, real QueryClient/local I18nextProvider/Modal/DOM and native Storage. Only admin API responses/peripheral toasts mocked, no query/generation model mocks. TZ is explicitly vi.stubEnv/asserted/restored, not ambient CI dependent. Query retry=false/gcTime=Infinity/staleTime=Infinity are deterministic fixture defaults, not application cache policy.

## Exact validation and attribution

Node25.9.0/installed dependencies; CI20 gap remains. NODE_OPTIONS=`--no-experimental-webstorage --max-old-space-size=8192`. Evidence-native config mirrors repo Vitest options and **actual repo setup**, with envDir:false/evidence cache. Native Storage explicitly installed per test after normal setup. No separate unmodified-config invocation. Source/QA forced tsc-b use actual imports/noEmit and evidence-only build-info; QA includes all six related tests. Full argv/cwd/inner exits/timings in `commands.json` and individual receipts (runner wrapper exits0 even when inner command fails).

| Receipt | Inner exit | Result |
| --- | ---: | --- |
| red-initial | 1 |11 genuine failures plus1 invalid duplicate quota-heading selector and its2 unused gates; retained, not product RED. |
| red-valid | 1 |Corrected test-owned heading selector:19 ordinary=11 RED/8 PASS/0 skipped;19 clean observations; exact frozen red-test bytes. |
| green-related | 0 |19 new GREEN. Overly broad include accidentally ran17 admin suites/120 PASS, of which60 affected. Incidental broader run is disclosed; scope corrected before final coverage. |
| coverage | 0 |Exactly6 intended suites60 PASS: new19,Code10,Subscription9,Plan8,Quota4,Payment10. |
| source-types / qa-types | 0 /0 |Fresh forced app source and six-test QA compile. |
| affected-lint | 0 |Three changed products+new/modified tests; max-warnings0. |

Five-page aggregate **79.08% lines /79.91% branches /67.46% functions /77.75% statements**, unchanged **68/55/61/66** thresholds. Per-file L/B/F/S: Code63.82/77.02/42/61.85; Payment96.77/95.58/93.33/97.29; Quota96.29/86.02/88.88/93.54; Subscription76.07/71.42/70.58/73.96; Plan84.28/67.85/73.07/81.08. Code42% functions and untested inputs/copy/filter paths disclosed; no per-file/module closure claim, threshold lowering or unrelated coverage padding.

From child apps/web with E as above and recorded NODE_OPTIONS:

```sh
node node_modules/vitest/vitest.mjs run --config "$E/vitest.coverage.config.mjs" --configLoader native --coverage
node node_modules/typescript/bin/tsc -b "$E/tsconfig.source.json" --force
node node_modules/typescript/bin/tsc -b "$E/tsconfig.qa.json" --force
node node_modules/eslint/bin/eslint.js src/pages/admin/CodeManagement.tsx src/pages/admin/SubscriptionManagement.tsx src/pages/admin/PaymentOrderManagement.tsx src/pages/admin/__tests__/M16.managementLifecycle.test.tsx src/pages/admin/__tests__/PaymentOrderManagement.test.tsx --max-warnings 0
```

API-boundary counts: obsolete/current success each1 sync+1 retained list refresh (initial1+refresh1); failure no refresh. Current failure/retry2 sync+1 success refresh. Quota A/B2 requests, exact B after A completes. These are controlled frontend counts, not backend SQL/provider execution. Valid RED/final scoped GREEN each19 clean observations:0 pending gates/portals/Query entries, no unexpected fetch, restored body overflow. Components unmount; deferreds settle; clients cancel/clear; Storage/env/TZ/fetch/mocks/local i18n listeners restore. No fake timers/services created.

## WATCHs, preservation and stop boundary

- Code clipboard calls writeText then immediate success toast(:226); no permission/rejection/physical clipboard execution. Smallest next proof: owned rejected clipboard Promise+actual click/toast observation, then notification-policy design.
- Plan validates JSON syntax rather than schema shape; numeric min attributes are not full submit validation. Backend/financial policy is root-owned, no speculative frontend redesign.
- Long-term2120 calendar interpretation, page totals shrinking, session/admin transitions and real cache/focus policy need separate evidence. No universal operation cancellation.
- Physical responsive/focus geometry, full App/admin auth and backend quota/checkout/fulfillment effects unclaimed. App build deferred root consolidation. No secrets/envfile reads, services/DB/browser/providers/network/install/Git/Actions. MainCI history remains deferred.

`source-phase.diff`: three product deltas against synced root baseline, never whole HEAD. `owned-phase.diff`: adds one existing-test adapter and two new files. Exact six candidate paths in final-source/final-manifest. Other six checked/synced existing paths remain phase-baseline exact; all ten relevant root files still match baseline at readback. Read-only API/client/dateUtils/Modal/setup/config and frozen M08 six-path hashes verified unchanged (preservation-readback); scoped check, not whole-worktree claim. Root independently reviews/integrates. Bounded work complete.

Candidate bytes and SHA256:
- `apps/web/src/pages/admin/SubscriptionManagement.tsx`: 32613→32673 bytes; `4fa28ec6187c5cf9b2a732ba1c0d56ae530ce202c05cac949f1eb9e734decb4a`.
- `apps/web/src/pages/admin/CodeManagement.tsx`: 31386→31442 bytes; `65176d8252e1733079d8f6996872ba8f2c7e7d41382b993463efc36203563497`.
- `apps/web/src/pages/admin/PaymentOrderManagement.tsx`: 12180→12759 bytes; `e8eac884cb63a96679c3700f0cf15fe4c7f94f3f94608c6e72a76bbe8bc96450`.
- `apps/web/src/pages/admin/__tests__/PaymentOrderManagement.test.tsx`: 9281→9523 bytes; `564c9316acc22f0af4477494546ca057ce5386ba89a60745f80377acf0b43c4e`.
- `apps/web/src/pages/admin/__tests__/M16.managementLifecycle.test.tsx`: new→19249 bytes; `f5f9b5aad7dcbba83e551cedefea1f75179aa81cf1b44c104195b9957005db47`.
