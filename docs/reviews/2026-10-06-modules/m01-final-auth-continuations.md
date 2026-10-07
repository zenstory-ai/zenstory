# M01 final auth continuations — C5/C7 bounded repair

The actual OAuth caller no longer navigates or exports its captured token after supersession or route departure. Login's project/deep/paid/SSO continuation belongs to its browser history attempt and captured session pair, including genuine refresh descendants. Initialization accepts successful /me across the existing shared-refresh lineage and limits destructive failures to the original pair.

Root approved DESIGN and partition; independent SOURCE review/integration remain root-owned. This is not M01/all23/release approval. Product scope is five files, including a tiny key/type in existing authFlow; three test files and this new review doc complete nine owned paths. API/client refresh internals, ProtectedProviders, query boundary, Register, VerifyEmail, other AuthContext methods, page JSX and unrelated product files are unchanged.

## Regression evidence and partition

Immutable previous proof: 68 cases = 31 PASS, 34 production continuation failures, three embedded co-mounted StrictMode WATCH failures. The three WATCH cases are precisely strict=true current embedded OAuth free-query/paid-hash/external-query. Their original harness/body/receipts remain in frontend/m01-final-continuation-proof/final-source and valid-final.json. They have no demonstrated actual production topology; they are not fixed, skipped, marked fails or counted GREEN. Structural separate default-only registration keeps all other embedded modes and all actual App cold root-Strict/lazy/provider controls.

Fresh pre-source expanded receipt: 86 cases = 39 PASS / 47 failures / 0 skips, with 0 unknown fixture requests. My first structural partition edit accidentally left the three WATCH registrations present; this receipt therefore contains original68 +18 new, not 65+18. Excluding the explicitly classified three WATCH results yields 39 PASS/44 production RED. Ten new genuine REDs cover rejected-list fallback and manual dashboard departure; eight new controls cover healthy descendants and obsolete cached-init failure. Other new failure-preservation/quiet-denial controls were added after repair and have no new pre-source RED claim.

Final ordinary continuation suites: **97 GREEN = 65 original gated +32 new production controls**, preserving all original65 cases and all34 prior production REDs now passing. Fresh related run: **383/383 GREEN across14 files, no skipped cases**. After the full run, final Router-state assertions were added to eight current saved/recent/paid/deep cases and separately passed 8/8; that focused command intentionally excludes56 other App cases and is not used as a whole-suite GREEN claim. No source changed after full coverage/types/build.

## Minimum implementation

- AuthContext.tsx:156 captures init access+refresh pair and uses existing resolveOwnedAuthSession plus the existing generation before/after success JSON. Init failure/null/clear requires the exact original pair, not a descendant. Existing loading/finally policy, cache/refresh helpers and generation/logout logic remain unchanged. OAuth method at414 throws exact DOMException AbortError for superseded non-OK/JSON completion instead of silent success.
- OAuthCallback.tsx:20 adds a page-local singleflight boolean alongside the existing ref. Valid owner captures/consumes plan intent before awaiting provider and alone resets the flag in finally. Exact AbortError is quiet. Browser AUTH_CALLBACK_PATH is checked before processing and after await. The pre-processing check was required by current paid control: Router could still mount the old callback after the owner synchronously wrote billing navigation; otherwise that mount scrubbed the new billing query and navigated dashboard. Current allowlist/token parameter/query/hash scrub policy is preserved.
- Login.tsx:71 reserves an opaque router-entry marker before awaiting login, preserving from. Completion captures committed token pair; each asynchronous continuation/fallback rechecks pair lineage and the live history marker. Inner list catch rethrows exact AbortError. Final navigation strips/omits the internal marker; existing per-user stored-project and sorting code is unchanged.
- App.tsx:225 changes only authenticated no-SSO PublicRoute default redirect. It forwards the marker from the live browser entry through identity remount and honors from path/search/hash/state before paid/default. Initial location.state can lag the submission entry; live history forwarding preserves valid saved/recent continuation. The asynchronous SSO effect is byte-identical.
- authFlow.ts:35 appends only the pure marker key/type. Existing plan normalization/consume functions are byte-identical.
- Login.test.tsx adapter uses actual BrowserRouter and call-through navigation observation in place of a MemoryRouter plus navigation stub. Its mock context success writes explicit fake tokens; existing failure/cancellation controls assert no final navigation while permitting required same-entry marker reservation. Storage/history isolation prevents a prior paid URL contaminating a later default-success control. No production contract was relaxed to accommodate missing mock tokens.

## Local measurements

Measured actual App/child code with fake HTTP and happy-dom, not a physical browser or production latency benchmark. Raw before/after observations and request/state counts are saved separately.

- Obsolete OAuth after real B establishment: captured-A href writes 1 ->0 in both modes; actual request counts remain7 and stored token remains B.
- Obsolete Login list after logout: obsolete project push1 ->0; same final Login route alone previously hid the intermediate stale navigation. Request counts default8->7, Strict10->9.
- Obsolete Login list after B: old A project push1->0, final B dashboard retained. Request counts default14->13, Strict19->17.
- Uncached init with eager subscription refresh: before tokenA1 survived but route became Login/no identified user. After route stays dashboard/projects and identifies A with tokenA1. Extra requests (default5->8, Strict6->10) are legitimate authenticated-page/provider work after restoration, not a latency improvement.

## Fresh gates

All commands use existing installed tools, Node v25.9.0, NODE_OPTIONS=--no-experimental-webstorage for Vitest, native happy-dom Storage, envDir=false and evidence-only caches/build-info. No installs/network/providers/browser/DB/Actions.

- Fourteen affected suites + exact five-product scoped coverage: exit0,383 PASS/0 FAIL/0 SKIP. Coverage lines91.81%, branches85.12%, functions71.55%, statements87.87%, against unchanged68/55/61/66 thresholds. Full App scope includes unmodified branches; it is not disguised as function-only coverage. authFlow100%.
- Forced actual App/node source typecheck: exit0. Final new QA plus Login adapter typecheck: exit0. Affected product/test ESLint max-warnings0: exit0; final new state assertions also lint/types GREEN.
- Exactly one Vite App build: exit0, correct explicit Tailwind base apps/web with envDir=false/evidence cwd/cache/outDir. CSS174745 bytes contains .hidden, .md\:flex, .w-80, .h-12, .z-50; hash3ffae0ef4afde1aadabf780434b08c7d5e0b4c4111cffc951760e7958f3dc6aa. Existing outdated Browserslist-data notice retained; no update/install. No marketing/site post-build or physical-browser claim.

Exact commands, outputs, phase baseline/final snapshots/hashes, phase-only patch, case accounting, before-after observations, semantic guards and cleanup are under frontend/m01-final-continuation-repair. No whole-HEAD diff or source synchronization.

## Invalid and intermediate receipts

Wrong-cwd tool invocation (twice) and omitted native configLoader startup failed before tests; no product RED claim. Expanded86 receipt's partition error is explicitly accounted above. First repaired83 run75 PASS/8 failures identified current-flow races and was corrected within the minimum path/marker contracts; preserve receipt. Initial related383 run382 PASS/1 failure was a paid-URL unit fixture leak, not product RED. Evidence-only preservation script initially guessed ProtectedProviders path and then matched an inner refreshToken constant; those static-script errors were corrected, and all exact guards pass. No failed receipt was deleted or relabeled all-GREEN.

## Preservation and gaps

All504 pre-existing unowned child source/review files retain read-start bytes. Exact guards preserve other AuthContext commands, AuthIdentityQueryBoundary, App outside PublicRoute and its async SSO effect, page JSX and existing authFlow functions. apiClient C6 prerequisite stays root-exact; ProtectedProviders and SSO helper remain root-exact. Known rootC4 VerifyEmail difference remains attributed/no-sync. Child's13 VerifyEmail tests are historical compatibility controls; they do not validate root's newer C4 product bytes. Prior proof final snapshots/manifests/receipts remain immutable.

Owned fixture promises are settled during cleanup, real QueryClients cleared, mounted DOM/listeners disposed, fake Storage cleared, href/history/fetch/analytics mocks restored. No server/browser/DB/tmux resources were started. Shared dependencies/cache/original/root worktrees were not edited. Node25 is not CI20. Three embedded Strict WATCH cases remain open; physical-browser/provider integration, global gates and other M01 leaves remain coordinator-owned. Existing init loading/finally and completed Register/Verify timer policies are preserved. Server writes are not blanket-aborted. Source review/approval and narrow integration are separate root steps.
