# M09 bounded upload and sidebar import repair

Native frontend lane, 2026-10-06; root sources/tests matched the four owned baseline paths exactly, so no synchronization was needed. Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m09-upload-import-repair/`.

Two production changes, grounded in actual API/component regressions:

- `apps/web/src/lib/materialsApi.ts:498`: capture entry access+refresh before await; fetch with an explicit access token. On401, use the existing readonly `resolveOwnedAuthSession` from **apiClient.ts** (this root has no authSession.ts), matching the approved package/custom-fetch pattern. Exact owner may refresh once; confirmed descendant replays once directly; missing/unrelated owner returns the original response to unchanged ApiError parsing. No successful200 cancellation policy. Every byte outside upload and its added import checked identical to baseline.
- `apps/web/src/components/sidebar/MaterialsPane.tsx:73`, `:159`, `:255`: mounted/project object owner for import completions only. Successful quick import signals real ProjectContext refresh once; batches signal once when results contain successful files, including partial batches, zero for all-failed/thrown cases. Same-component A->B invalidates A selection/pending writes and refresh; actual pane unmount also invalidates refresh. New project releases departed pending presentation. Global completion toasts/logging and issued server operations remain; partial selections stay checked and full live batches clear as before. Search/entity/cache/attachment behavior unchanged.

Proof uses native Storage and actual upload/apiClient refresh ledger with HTTP interception only; actor replacement there is a controlled Storage command, not a full AuthProvider identity proof. Pane fixture uses actual ProjectProvider, MaterialLibraryProvider/useMaterialLibrary, MaterialAttachmentProvider, real QueryClient and local i18n; fake Auth/API/analytics/toast boundaries. `ProjectContext.tsx:152` increments the real fileTreeVersion; it is not a mocked refresh setter. Actual root/child client/provider/hook dependencies checked exact.

Before/after observations:

| Scenario | Baseline -> repaired |
|---|---|
| A401 after B replacement | B refresh and B-authenticated replay delivered success -> one captured-A upload, zero B refresh/replay, exact original typed401, B storage unchanged. |
| Confirmed A0->A1->A2 before old upload401 | Five requests/extra third refresh -> exactly four: original upload, two real rotations, A2 replay. |
| Missing entry access | Refresh/replay accepted upload -> original401 without refresh/replay. Missing refresh/logout and replacement during pending real refresh retain compatible denial. |
| Current quick/full/partial import, default/rootStrict | Real refresh version remained0 -> exactly1 per operation. All-failed/thrown remain0; expected success/partial/error toasts and selection behavior retained. |
| Pending A batch, same pane switches B and selects anew | A success erased B selection -> B selection retained, no B refresh from A; subsequent owned B import increments once. A POST/completion notification retained. |
| Pending quick import then switch/unmount | Baseline already had no refresh; retained as positive ownership controls for the newly added signal. No pre-existing quick-unmount corruption claim. |

Regression accounting: first27-case run had16PASS/11failures, but two partial probes were invalid because one selected item returned one success plus one failure. **Nine genuine initial REDs** remain. Corrected two-item partial probes reproduced two genuine REDs against the exact original pane restored temporarily, then the saved candidate was restored in finally; focused filtering excluded14 other cases and is not an all-suite pass claim. Final27 new cases plus72 existing cases = **99PASS, zero failures/skips**. Original receipts retained. First coverage run had two bootstrap scheduling failures because a fixed15ms wait preceded the library query completion; fixture now awaits actual rendered project/library/entity state, with no retries or threshold changes.

Fresh final gates (exact arrays/inner exits in evidence receipts):

- `coverage-corrected`: native-config Vitest, four suites,99PASS, exit0; exactly materialsApi+MaterialsPane scoped, unchanged68/55/61/66 aggregate thresholds. **82.71L/80.51B/83.33F/80.42S**. materialsApi100% in this scope; pane78.23L/76.37B/76.08F/75.78S. No complete UI coverage claim.
- `types-final`: installed `tsc -b` forced source and new-QA configs, exit0. Production paths explicitly included; all cache/build-info evidence-owned.
- `lint-final`: two products and four affected tests, max-warnings0, exit0. Earlier unused-disable warning removed; no lint rule/config changes.
- App build deferred as requested; no browser/backend/real network/provider/Actions execution. Node25.9.0 differs from CI20.

Existing test adapters only add the new context refresh member and resolver/token-pair fixtures; their old retry/error/payload assertions remain. Actual ownership assertions use the separate real-client suite. Cleanup settles owned deferred/observed promises, unmounts providers, cancels/clears QueryClients, restores native Storage/fetch/mocks/local i18n listeners and clears actual refresh lineage; unexpected fetch/query leftovers asserted zero.

Final source/test/doc snapshots, seven-path hashes, baseline-only patch, all commands and preservation checks accompany REPORT. No API/client/context/shared renderer/schema/config/dependency/backend changes. Frozen M18 and preceding candidates remain protected. Broader pane async-state, import retention/idempotency and physical-browser behavior are outside this repair. Root owns independent SOURCE review/integration and all23/release; mainCI remains deferred.
