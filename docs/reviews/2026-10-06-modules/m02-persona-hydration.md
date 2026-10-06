# M02 persona form late hydration

The actual form no longer overwrites fields edited during this page visit when a
server profile arrives. One page-local ref records three independent dirty bits;
only accepted persona add/remove, goal toggles and explicit experience selection
mark a bit. A rejected fourth persona does not dirty the field. Untouched fields
still restore normalized server values, including cached-but-not-edited initial
values. Profile presence and complete normalized server-truth local persistence
remain unconditional; unsaved mixed drafts are not falsely stored as server truth.
Dirty bits survive later refetches. Existing identity query boundary remounts this
page for identity changes; no new auth/storage/query framework or server policy.
Submit continues to send the visible draft with the unchanged skip/error contract.

## Proof and local checks

Actual page, AuthProvider/identity boundary, BrowserRouter, QueryClient, persona
API/apiClient and native happy-dom Storage are used. Only network/analytics SDK
boundaries are fake. Held GET resolves after editing a field in default and root
StrictMode: valid baseline10 = six runtime REDs and four pristine/cached controls.
An added combined cached-then-edited control is a separate genuine RED: server
truth reached local storage while the author serial choice was visibly lost.
The corrected source passes this control across two real GET/refetches, retains
all three edits, persists each complete normalized server profile and sends exact
PUT draft. Eleven new cases plus existing page/helper suites: **30 PASS**, no skip
or unknown requests, affected coverage statements96.87%,branches90.62%,functions
96.96%,lines99.13%, against unchanged66/55/61/68 gates. QA types and affected lint
pass using existing tools. Build will be one combined root App build after the
independent pending auth integration, not an unnecessary duplicate build here.
Independent final SOURCE review and root combined gates remain pending.

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/m02-persona-hydration-proof`.
Initial i18n bootstrap fixture had6 translation-hook timeouts plus runtime failures;
its10-case8FAIL2PASS receipt is NOT canonical product RED. The native setup locale
fetch bootstrap was corrected without mocking real production i18n. An inherited
wrong testNamePattern filtered all11 cases: zero runtime cases, not GREEN. Corrected
selector only reran the added combined case before source edits. Initial outside-root
type config could not locate vite/client, then lacked DOM matcher declarations;
these are preserved fixture diagnostics, not product type GREEN. Final evidence
config includes exact installed Vite and jest-dom type files, no assertion/source
weakening. No transient setup, held promise, QueryClient, mounted DOM, fake storage
or history leaks remain. No real provider/server/browser/DB/network/Actions/install,
commit/push/PR/merge/deploy. This is a bounded source repair, not M02/all23/release.
