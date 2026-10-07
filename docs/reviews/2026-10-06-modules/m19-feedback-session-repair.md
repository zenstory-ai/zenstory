# M19 feedback retry session repair

Attributed to the standalone native frontend lane, solo/direct. Root independent architect DESIGN_CLEAR authorized this source phase after the immutable six-RED proof. This is a bounded source candidate ready for independent SOURCE review and narrow root integration, not M19/all23/release approval. No shared API client, auth/provider, refresh ledger or framework changes.

## Result and contract

`feedbackApi.submit` and `adminApi.getFeedbackScreenshotBlob` now bind a 401 retry to the access/refresh pair captured before the first await. Each uses `fetchOnce(explicitAccessToken)` rather than recursively re-reading the current access token.

On the first 401, absent entry credentials or an unowned pair retain the original response and existing ApiError401 decoder; they neither refresh nor replay. An already-confirmed refresh descendant replays once under that descendant without another refresh. An exact original pair invokes the existing refresh once, then resolves the ORIGINAL pair again and replays only if a nonempty pair is still confirmed owned. Resolver→refresh and final resolver→fetch have no intervening await. A replay401 reaches the existing decoder, without another refresh or clear. Existing multipart data/file, endpoint, method, language, error handling and JSON/blob decoding remain intact. Successful200 delivery and already-issued server writes are unchanged by this task.

## Grounded before/after evidence

Frozen proof: `../m19-feedback-session-proof` in this lane's evidence root retains **6 genuine runtime RED +20 PASS**, original source/test snapshots and receipts unchanged. Source-phase tests strengthen obsolete rejection to exact `ApiError` with status401 and healthy two-generation request count to exactly4/A2 (no A3). All original26 named cases pass; eight approved branch controls were added (missing access/refresh/both +replay401, for each wrapper), yielding **34/34** actual-wrapper tests. The existing feedback/admin API and FeedbackDialog/FeedbackManagement fixtures contribute **79/79**; the combined five-suite scoped run is **113/113**, zero skipped/todo/invalid fixtures.

Each row below runs for both real wrappers. Counts are local intercepted requests, not production latency claims.

| Trigger | Before | After |
| --- | --- | --- |
| Hold A0 response, establish B0, release A401 | 3 requests: A0 → refresh B0 → replay B1; B storage changes; old promise fulfills | 1 request: A0; no B refresh/replay; B storage unchanged; ApiError401 |
| Same, with actual `clearAuthStorage` logout before establishing B | same wrong-session chain/effects | same 1-request rejection and unchanged B |
| Another real refresh awaiter runs first, wrapper joins, A0→A1 refresh finishes, first awaiter establishes B before wrapper resumes | 3 requests: A0 → refresh A0 → replay B0; old promise fulfills | 2 requests: A0 → foreground refresh A0; no B replay, unchanged B; ApiError401 |
| Two real rotations A0→A1→A2 before delayed A401 | 5 requests including extra A2→A3 refresh and A3 replay | 4 requests: A0 + two foreground rotations + replay A2; exact A result and real lineage |

Multipart requests retain original A issue text, route/project/trace fields, File name/type/size and exact `fake-local-png` bytes on legitimate own replay. Screenshot requests retain endpoint/headers and decode exact fake A blob. Full URLs, headers, request bodies/file bytes, outcomes, storage/cache timestamps, logout effects and real lineage are in `observations-final.json`, raw output and `before-after-measurements.json`. Fake B JSON/blob responses in the prior proof are Authorization tracers, not evidence of a real backend authorization bypass or disclosure.

All prior controls remain: ordinary A200, own A401→A1 replay, real healthy multigeneration, replacement while actual refresh JSON is pending, logout/no replacement, transient503/network retention, definitive401/403 own clear, and two real native-Storage C6 standard-client comparators. Added missing-credential cases preserve original401/storage without refresh or clear; replay401 preserves A1/no second refresh/no extra clear.

The suite uses actual wrappers/client/read-only resolver/singleflight refresh/ledger and happy-dom native Storage. Only fetch/Response parsing at the network boundary is faked. A/B establishment is explicitly a controlled native Storage command; the logout branch uses actual `clearAuthStorage`. It is not an AuthProvider/App login-flow proof. The original helper/refresh/storage are never replaced with modeled ownership logic. Existing API unit fixtures already mock apiClient; their minimal adapters now expose the resolver and seed actual entry/rotated refresh pairs. These adapters are compatibility checks, not the ownership proof. No existing assertions, retries or error expectations were weakened.

## Exact source boundaries

Final child paths/line anchors:

- `apps/web/src/lib/feedbackApi.ts:27`: submit captures the entry pair at :28–29; explicit fetch at :48; first-response fence at :61; refresh/re-resolution at :65–67; explicit replay at :69. FormData construction and error/JSON decoder are unchanged.
- `apps/web/src/lib/adminApi.ts:1534`: screenshot function captures pair, preserves endpoint/locale, explicit fetch and equivalent first401 fence. Only this function and the required import change.
- Shared `apps/web/src/lib/apiClient.ts:64` resolver and existing refresh internals remain byte-identical; no auth/provider/session registry is added.

`scope-verification.json` proves **83 other admin function declarations** have identical raw text and TypeScript AST-kind traversal. Stronger byte comparison removes only the owned screenshot declaration and required resolver import: all remaining admin bytes are identical. Feedback bytes outside the submit arrow and required import are identical as well. All **611 guarded unowned child paths** (source/reviews/E2E and key configs) retain their phase-start hashes, including prior frozen handoffs. All prior proof evidence-manifest entries retain their hashes. No whole-HEAD patch, source synchronization or root/sibling write was used.

Four product/existing-test paths were byte-equal to authoritative root before editing; five owned existing paths were frozen in this phase (two products, three tests), and the new repair doc was absent. Final patch is against this immutable phase baseline. Root source remains at its baseline for these products pending integration; final child/root differences are this authorized candidate only. Other known root/child auth differences are outside this slice and were not copied or altered.

## Owned changes

1. `apps/web/src/lib/feedbackApi.ts`: submit +required resolver import.
2. `apps/web/src/lib/adminApi.ts`: screenshot function +required resolver import.
3. `apps/web/src/lib/__tests__/feedbackApi.sessionOwnership.test.ts`: exact401/A2 assertions, file/URL checks, eight approved boundary controls; original cases retained.
4. `apps/web/src/lib/__tests__/feedbackApi.test.ts`: resolver mock/entry and rotated pair seeding, own storage cleanup.
5. `apps/web/src/lib/__tests__/adminApi.test.ts`: resolver mock/entry and rotated pair seeding only.
6. This new attributed review document.

No other source/test/config/lock/dependency edits. Source and test delta, before/after SHA256/byte manifests and exact snapshots are under `frontend/m19-feedback-session-repair` in `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux`.

## Fresh affected validation

Commands run from `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web`; `E` is `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m19-feedback-session-repair`. All tools were already installed.

- `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config E/vitest.evidence.config.mjs --reporter=verbose --reporter=json --outputFile.json=E/smallest-green.json`: **exit0, 34/34**.
- Same runner with `E/vitest.related.config.mjs` and output `related-green.json`: **exit0, 79/79**, four existing affected suites.
- Same runner with `E/vitest.scoped.config.mjs --coverage` and output `scoped-green.json`: **exit0, 113/113**, five suites. Exactly feedbackApi.ts/adminApi.ts included in V8 scope; thresholds unchanged.
- `node node_modules/typescript/bin/tsc -b E/typecheck/tsconfig.json --force`: **exit0**, fresh app source types, evidence-only build info.
- `node node_modules/typescript/bin/tsc -p E/tsconfig.qa.json --noEmit`: **exit0**, three affected API test files/actual imports, evidence-only build info.
- `node node_modules/eslint/bin/eslint.js src/lib/feedbackApi.ts src/lib/adminApi.ts src/lib/__tests__/feedbackApi.sessionOwnership.test.ts src/lib/__tests__/feedbackApi.test.ts src/lib/__tests__/adminApi.test.ts --max-warnings 0`: **exit0**.
- `node E/verify-scope.mjs`: **exit0**, TS AST/byte and unowned guard evidence.

Scoped coverage:

| Metric | Actual | Unchanged gate |
| --- | ---: | ---: |
| Lines | 89.94% (349/388) | 68% |
| Branches | 72.4% (244/337) | 55% |
| Functions | 87.36% (83/95) | 61% |
| Statements | 86.51% (372/430) | 66% |

`commands-and-exits.json` stores fully expanded commands, logs, cwd, child exit and durations. No invalid harness/startup or runtime failures occurred in this phase. The original RED run is historical proof, not a fresh failing source gate. Source/test types, scoped coverage and lint are fresh; unchanged unrelated suites were not repeated. App build is explicitly deferred to root consolidation by the source authorization, not claimed GREEN from an old receipt.

## Cleanup, limits and handoff

All34 current real-wrapper cases assert zero unexpected requests, zero unsettled network gates and zero pending outcomes. They await/assert File text reads, remove owned logout listeners, clear/assert native Storage and real lineage, restore fetch and reset actual module instances after promises settle. The existing UI fixtures use Testing Library cleanup and this phase's native Storage/fetch reset; no new UI/query fixture policy is introduced. The deny-by-default setup logged no unrecognized fetch. Evidence configs use envDirfalse/native configLoader and evidence-only cache/build info, leaving shared installed dependency contents untouched.

No browser, server, DB, real provider/auth credentials, network fallback, package install, build, Git remote or Actions resource was invoked. No unowned resource was deleted. Existing UI query/blob URL fixtures are mocked boundaries; neither physical-browser geometry nor real backend authorization/storage is claimed. Node25.9.0/happy-dom is not CI Node20 or a physical browser. No before/after production latency claim, successful200 post-switch delivery policy, undo/idempotency guarantee, session UX/toast redesign or module-wide SOURCE closure follows from these tests.

Bounded source handoff COMPLETE and uncommitted; root owns independent SOURCE review, narrow integration, consolidated build/final gates and all23/release. Same native process idle after handoff.
