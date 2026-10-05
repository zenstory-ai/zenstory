# PostHog error review — 2026-10-05

## Scope and evidence

Review production issue metadata and representative exception stacks, repair confirmed application defects, and avoid treating historical or unlocalized reports as proven current bugs.

- Project: `359720`; all **58 issue records** retrieved, `next: null`. These are persistent issue/fingerprint groups, **not 58 occurrences**, users, or recent failures.
- Grouping: 20 distinct descriptions; React lazy-module `undefined.default` accounts for 32 Chrome-style and 2 Safari-style issue records.
- Authenticated Chrome Beta was reused without restarting it, changing security settings, or reading/copying browser cookies. Rendered MHTML snapshots and raw API/log responses remain in private operator storage, not this repository.
- Only read-only navigation/API reads were used. No PostHog issue status, severity, assignee, settings, or event data was changed.
- The default seven-day issue-list query failed inside PostHog. The persistent issue GET endpoint and individual issue pages remained readable. Therefore this report does **not** invent an aggregate recent occurrence count or promise exhaustive per-event attribution. Individual detail panes may show a historical selected event outside the seven-day list.

## Confirmed defect and repair

**P1: stale dynamic-import recovery can itself crash React before the requested reload.**

Recent example: [Oct 5 lazy-module issue](https://us.posthog.com/project/359720/error_tracking/01a10be0-8d6e-7bc3-b273-21a94a8e4134), unhandled Chrome 153 / Windows 10, React lazy initializer `J` in `i18n-vendor-CdLTHxH5.js:1:3922`, then `index-CtCG3l27.js`. A historical iOS example has the same React `_result.default` failure shape.

Evidence chain:

1. Installed Vite **7.3.6** emits a cancelable `vite:preloadError`; its import `.catch(handlePreloadError)` resolves `undefined` when the event is canceled.
2. `src/lib/chunkRecovery.ts` previously called `preventDefault()` after requesting reload.
3. `lazyRoute` consequently returned an undefined module and cleared the persisted reload marker while navigation was still pending.
4. A regression emulating the actual Vite catch/event contract reproduced React 19's undefined-import result and the wrongly cleared marker: **1 failed / 4 passed** before the repair.
5. Separate review caught a nested-route guard gap: a successful parent could clear the failing child’s marker. A regression reproduced it (**1 failed / 7 passed**) before the source-scoped correction; the corrected suite passes **9/9**.

Repair reuses the existing recovery boundary:

- Preserve Vite's import rejection instead of canceling its event.
- Coordinate duplicate same-page signals behind one pending reload promise.
- Keep lazy components suspended while that reload is pending.
- Store the failed lazy source in the existing session guard. A generic Vite signal transfers to that source while the same reload is pending. Retain the guard on persistent post-reload failure; only that exact source succeeding on a fresh page clears it. Successful parent/sibling imports cannot restart the reload loop.
- Use the same existing `lazyRoute` utility for the remaining bare lazy `react-markdown` import.
- Unrelated failures still propagate. No new dependencies, broad error boundary, blanket exception ignore, DOM monkey patch, or retry loop.

The behavior matches [Vite's documented preload contract](https://vite.dev/guide/build.html#load-error-handling) and [React's lazy module contract](https://react.dev/reference/react/lazy). The installed Vite implementation, not a newer documentation version's browser baseline, was used to verify the emitted code.

## Triage by family

| Family | Evidence / disposition |
|---|---|
| `undefined.default` / `_result.default` | Confirmed recoverability defect above; repaired with regression-first proof. Group similarity alone does not prove every historical fingerprint has an identical cause. |
| Failed/error-loading dynamic import; JavaScript MIME mismatch | Old Login chunk examples, no seven-day events in the sampled issue pages. Same stale-asset recovery domain; built-browser fault injection deliberately returns 404 HTML for the first Login chunk. No host rewrite/configuration change was inferred from historical reports. |
| `ERR_AUTH_TOKEN_INVALID` | Provided issue: three seven-day occurrences / sessions / users; handled `OAuthCallback` provider redirect, not evidence of ordinary JWT refresh failure. See auth correlation below. No nonce weakening or blanket capture suppression. |
| `ERR_INTERNAL_SERVER_ERROR` | Historical handled OAuth callback report; sampled page has no seven-day events. No current upstream failure inferred from that old report. |
| `Missing tokens in callback` | Historical callback reports with no sampled recent events. Current callback distinguishes cached session, missing handshake, and provider error, and is guarded against StrictMode double processing. Existing auth regressions pass. |
| `uploadError is not defined` | Historical **development** stack `/src/components/sidebar/FileTreePane.tsx` / Vite deps. The current component has no such identifier reference; upload errors use existing handled paths. Not a current production source patch target. |
| `Failed to fetch` | Historical DashboardHome stack, no sampled recent events. Current loading/error paths are handled; no blanket network-error filter added. |
| `this.o.at is not a function` | Sample: Chrome **92**. Installed Vite 7 baseline targets Chrome **107** or newer, and this project does not override it. No unrequested legacy-browser polyfill/dependency added. |
| `removeChild` / `insertBefore` NotFoundError | Recent own React reconciliation stack, one user across the sampled issues (1 removal + 2 insertion events). External DOM mutation is a hypothesis, **not a confirmed extension attribution**. Missing route/DOM mutation context prevents a grounded source fix; no global DOM monkey patch or suppression. |
| ResizeObserver undelivered notifications | Three events / one user in sample, synthetic with no usable stack. Not sufficient to identify an application resize callback or claim the existing responsive release fixed it. Retained for attribution rather than globally ignored. |
| AbortError | Recent unhandled passive-effect cleanup stack mapped from the exact archived production bundle to FileTreePane request/unmount cancellation. Current caller and HTTP client handle tested cancellations correctly; the recorded unhandled failure was not reproduced. No global suppression or speculative client patch. See cancellation characterization below. |
| `LIDNotifyId`, `CONFIG`, JSON-RPC, generic `Script error`, maximum-call-stack | Historical/injected-looking or cross-origin/synthetic stacks, some without source frames. No corresponding app symbols for the named injected functions; this is evidence of missing attribution, not proof that every report is an extension. Generic `Script error` is still recent. No global exception filter or speculative recursion patch. |

## OAuth evidence and unchanged security boundary

[Provided OAuth issue](https://us.posthog.com/project/359720/error_tracking/01a0f280-36d0-7e13-bf1c-7837261e4737).

`OAuthCallback.tsx` reads backend/provider `error` / `error_code` and throws the exact supplied code. `api/oauth.py` deliberately uses `ERR_AUTH_TOKEN_INVALID` for invalid state, cookie mismatch, Google exchange failures, and invalid user-info responses. The six branches cannot be distinguished from the frontend message alone.

A narrow read-only query of the then-active Railway deployment around **Oct 3 00:50:30 UTC** found:

- `00:49:47.65`: Google callback received.
- `00:49:48.03`: creating a new Google user; HTTP redirect **307**.
- `00:50:23.20`: another callback request rejected **422**.
- `00:50:28.78`: another callback received, redirected **307**.
- No token-exchange / user-info upstream error log in that window.

This supports a repeated/stale callback after registration, rather than a demonstrated broken Google login. It does not identify every one of the three recorded sessions. Preserve the host-only HttpOnly `SameSite=Lax` state cookie, default 600-second lifetime, strict nonce matching, URL scrubbing, and single callback processing. Raw logs contain account information and remain private; only safe stage/timestamp/status summaries are recorded here.

## Cancellation characterization and remaining attribution gaps

[Recent AbortError issue](https://us.posthog.com/project/359720/error_tracking/01a10be0-9462-7531-a099-ffbc2a948f78), Oct 5 `11:43:44.485Z`, maps from the exact archived `index-CtCG3l27.js:16:158020/158513` to FileTreePane's previous-request and effect-cleanup `AbortController.abort()` calls. Reading the immutable deployment used an already-existing automation bypass through read-only requests; no protection setting or token was created, and the local credential-bearing response copy was removed.

The current component already catches AbortError by name, ignores superseded requests, and guards against stale responses. Its HTTP client awaits fetch directly rather than leaving an ignored rejection branch. Added tests cover actual abort-listener rejections on unmount, StrictMode cleanup and project changes, plus an abort-ignoring stale-response race. A client test verifies the original AbortError reaches the handled caller without refresh/replay/logout/token changes. **63/63** targeted tests pass, with no error log/unhandled rejection on the covered component paths.

This does not prove that the historical event was harmless or identify an external/runtime cause. AbortError, recent DOM reconciliation failures, synthetic ResizeObserver reports and cross-origin Script errors remain attribution gaps, not silently “resolved” incidents. No PostHog status changes, blanket ignore, or global DOM patch was applied.

## Validation on the repair

- Focused chunk recovery: **9/9**, including actual Vite-style event → React lazy integration, duplicate signals, unrelated failures, and nested parent/sibling success with persistent child failure and eventual same-child recovery.
- Full frontend: **195 suites, 2494 passed / 1 pre-existing skip**, existing global coverage thresholds pass.
- TypeScript build, full ESLint, CSS tokens and i18n default-value keys: pass.
- Full app/organization/docs build: pass; site tests **51/51**.
- Actual compiled app, desktop **1280×720** and mobile **390×844**: first Login chunk receives injected **404 text/html**, exactly two document loads / two chunk requests (one automatic reload), login inputs become visible, **zero page errors**, persisted guard clears after success. No account writes or model calls.
- Compiled nested `/dashboard` route, desktop: successful Dashboard parent loads on both pages while DashboardHome keeps returning 404 HTML; exactly two document loads / two child requests, `DashboardHome` guard retained, original persistent import error propagates without `undefined.default` or a reload loop. Local fixture data only.
- Independent source review: **approved, no findings** after correcting the nested-route regression.
- Separate release evidence is tracked by the PR; local results alone are not a production completion claim.
