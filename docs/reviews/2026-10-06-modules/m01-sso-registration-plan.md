# M01 C2/C3 — bounded design before source

Same native frontend proof completed24tests: nine genuine RED/fifteen controls,
zero skip. Helper late validation can return an old token after logout/replacement;
late denial borrows B refresh credentials; actual PublicRoute redirects after
logout or real route departure and redundantly clears actual Login-established B.
Register ordinary enabled double-click during submit-policy lookup produces two
identical POSTs in default/root StrictMode. No server/partner/provider incident or
backend duplication claim. Evidence frontend/m01-continuation-proof/REPORT.md.

Preserve URL allowlist, protocol/userinfo/deceptive-host rejection, network token
preservation, current definitive-denial cleanup, and healthy same-session refresh
rotation/API retry lineage. Existing apiClient refresh persistence/cleanup/lineage
checks are KEEP. C1 establishment and M05 paths remain frozen unless a separate
necessary caller line is explicitly authorized; no auth-global store/framework.

Proposed C2 shape for independent architecture refinement:
- Capture entry access/refresh identity before awaits; recognize tracked legitimate
  refresh descendants using the existing private lineage map, not JWT parsing,
  principal equality or a second generation registry. A small read-only ownership
  query export is justified only if existing lineage state cannot otherwise be reused.
- Check ownership after validation, before refresh, after refresh and before
  returning a redirect/clear intent. Never reread and borrow a newly logged-in B
  refresh credential. Superseded work is cancellation, not an instruction to clear
  all storage. Prefer existing AbortError cancellation semantics over a new result
  framework, but trace both actual callers and preserve own-denial UI semantics.
- PublicRoute requires an effect-lifetime/route/identity fence. Its current ssoState
  dependency self-reruns immediately after setValidating: do not introduce a cleanup
  that cancels its own request or leaves a permanent loader. Separate operation
  identity from state-only presentation and restart/reset only on actual relevant
  user/loading/route input changes. Check after dynamic import and helper await,
  before every navigation/clear/state continuation; cleanup invalidates old work.
- Login already catches exact DOMException/AbortError from C1; verify awaited SSO
  cancellation cannot proceed to fallback navigation. No OAuth callback helper
  caller exists in current production imports, so don't add unrelated guards.

Proposed C3: reserve a synchronous submit-in-flight fence and loading before the
submit policy await, wrap policy/required-invite/terms/POST in existing try/finally
so all early exits/failures release the fence/loading, preserve policy-failure
fallback and successful form lock/history-email/plan/invite semantics. No registration
backend idempotency/schema or timer/unmount-policy invention.

Independent DESIGN CLEAR before product edits. Child may implement only explicitly
owned helper/client/App/Register and corresponding existing/new regression tests
plus attributed review doc; root owns integration/review. New tests must show the
same actual caller effects repaired while fifteen controls remain valid; add
necessary default/StrictMode/no-self-cancel/current denial/latest-intent controls.
Types/lint/scoped unchanged coverage/build once after source changes, no repeated
M05/browser gates. Local-only intercepted fetch/analytics/navigation, no network,
provider, credentials, install/Actions/remote Git. M03 backend proof remains parallel.

## Independent DESIGN — APPROVE/CLEAR (source phase)

Reuse existing apiClient refresh lineage through one read-only export resolveOwnedAuthSession(entryAccess, entryRefresh). Return the current exact pair or a tracked descendant only when both current tokens match the existing lineage record. Null/no-refresh identity requires exact matching. Do not expose the map, parse JWT identity, add a generation registry, or change refresh persistence, retry, cleanup or lineage mutation.

The SSO helper captures both entry tokens before its first await and checks ownership after validation, immediately before synchronous invocation of refresh, after refresh, and before returning redirect/clear intent. Use the latest owned access token. Superseded work throws DOMException('SSO redirect superseded', 'AbortError'). Preserve own definitive-denial and transient-failure semantics: the real refresh primitive already conditionally clears its captured session; a nonempty unowned replacement is cancellation, and already-empty storage requires no additional global clear.

PublicRoute owns an active operation fence keyed by actual user identity, loading, and route key/path/search. Remove ssoState from both dependencies and start guard to avoid self-cancellation when presentation becomes validating. Current no-user/no-redirect inputs reset idle. Cleanup only invalidates prior work. Check after dynamic import, helper completion, and before every state update/navigation/clear. Recheck entry credential ownership when applying results; success must remain owned, clearAuth may clear only its owner, empty storage is presentation-only, and replacement credentials cancel. Exact AbortError is quiet. Finally updates presentation only for the active operation. Default and root StrictMode must produce one live redirect/clear without wedging a spinner.

Login product source remains unchanged: existing exact AbortError handling must suppress awaited SSO fallback navigation. There is no other production helper caller requiring an OAuthCallback change.

Register reserves one synchronous submitInFlightRef and loading after synchronous validation but before policy await. Put policy/fallback/invite/terms/POST/success/error branches inside existing try/finally, releasing the ref and loading on every exit. Keep existing successful-form lock, retry, policy fallback, invite/plan fields and email/two-second navigation semantics. No server idempotency, debounce, or unrelated timer/unmount changes.

Validation: repair nine genuine REDs and retain fifteen controls; add same-user replacement, healthy multigeneration refresh lineage, own denial/transient preservation, default/StrictMode no-self-cancel route effects, Login awaited-SSO cancellation, and Register policy/invite/terms release/retry controls. Narrow owned source and corresponding tests only; exact-source types/lint/scoped coverage/build once, no repeated unrelated gates. Final SOURCE review and root integration remain separate.
