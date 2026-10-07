# M18 bounded admin UI repair

Native frontend lane, 2026-10-06. Current root baselines for three pages and three existing tests matched this child exactly; no synchronization was needed. Root/backend, API/client, shared dialogs, translations and prior frozen candidates remain outside this change.

The untouched-source actual-component matrix reproduced **13 RED / 8 passing controls**. After narrow repairs, **39 affected cases pass**: 21 new and 18 existing. These are offline happy-dom receipts, not physical-browser, production-latency, backend-transaction or whole-module approval.

| Proven issue | Repair and retained behavior |
|---|---|
| SkillReviewPage: old pending-status success/error replaced approved-status rows or error state; completed pending approval invoked its captured old-status refresh. Default and one root Strict replay reproduced both. | Local object owner per mounted status plus latest list sequence fence success/error/loading. Decisions capture owner before POST and fence local completion/refresh/finally. Status selection clears old rows/dialog presentation. Issued POST still finishes; existing decision error logging remains. Ordinary owned approval/refresh, current error/retry, review history and unpublish controls pass. |
| PromptEditor: existing -> new reused one actual Router component instance, retained dirty text and expected_version7. | Route-identity effect resets form, selector and confirmation. Existing config still initializes fields/token; direct-new and ordinary existing trimmed saves pass. No dirty-refetch policy change. |
| PromptManagement/skill card: naive UTC timestamp differed from equivalent Z/offset values in owned America/Los_Angeles timezone. | Reuse existing parseUTCDate at three label sites. en/zh locale, aware offsets and prompt missing/invalid fallback controls pass. No backend/timezone/translation changes. |
| Completed prompt save/delete: previously scheduled500ms navigation undid manual departure. | Store/clear navigation timer on route identity cleanup/unmount; live delayed return, success toast and issued mutation remain. This proves completed-operation timer cleanup; mutation still pending at departure is a separate WATCH. |
| Expanded review materials: failed resource request then actual locale-change retry succeeded, but prior resourcesError hid recovered raw text. | Clear error when the owned effect starts. Real local i18n change issues exactly two resource requests; recovered raw content and original raw/rendered controls pass. Existing effect cancellation remains. |
| Reason dialog: cancel while A rejection was pending allowed B dialog/reason, then A completion erased B. | Disable pending cancel and guard backdrop close, matching existing pending confirmation behavior. One A POST/reason is preserved. No server cancellation or new modal framework. |

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m18-ui-repair/`. `baseline/`, `baseline.json`, `red-test/`, final snapshots/manifests and phase-only patches distinguish this change from the dirty HEAD and preserve previous phases.

Validation uses existing installed tools, native Storage installed by the new test before its guard, real React/Router/QueryClient/local I18nextProvider/default repo setup, and fake API/toast/network boundaries. Initial three missing-Router date probes were **invalid fixtures**, retained in `red.*`; corrected `red-valid.*` records13 genuine failures. Initial QA fixture lacked required API metadata and used null outside the declared optional-date type; adapters add primary_content_type and use undefined for the existing missing-date branch. Source type-config's relative vite/client lookup and one redundant cleanup-ref lint warning were corrected locally/evidence-only; no threshold/config/dependency changes.

Fresh final receipts:

- `coverage-final`: Vitest native-config run of new plus three existing suites, **39PASS**, exit0. Exactly three products scoped: **84.13L /78.77B /72.04F /80.85S**, unchanged68/55/61/66 aggregate gates. PromptManagement individually has66.66L/50F; aggregate scope passes, no per-file100% claim.
- `types-final`: installed `tsc -b` forced source and QA evidence configs, exit0; products are explicitly included, not merely transpiled by Vitest. Build-info stays in lane evidence.
- `lint-final`: three pages plus new test, `--max-warnings0`, exit0.
- Full App build deferred to root consolidation as requested. Node25.9.0 local differs from CI20. No browser, backend, provider, real network or Actions checks performed.

Cleanup unmounts portals/components, disposes routers, settles owned gates, cancels/clears QueryClients, restores timezone/env/Storage/fetch/mocks/local i18n listeners; unexpected fetch and remaining query counts are zero. Existing three tests are unchanged. Final five owned paths/hashes and exact command arrays/exits are in the evidence manifest/receipts; no whole-HEAD patch is supplied.

Remaining WATCHs: dirty refetch recovery policy; prompt server operations still pending during departure; same-status overlapping decision policy; template-placeholder validation, cross-process reload and backend performance from the read-only map. No new policy or generic framework was introduced. Root owns independent SOURCE review, narrow integration, all23 gates and release; historical mainCI stays deferred.
