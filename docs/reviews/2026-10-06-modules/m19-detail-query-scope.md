# M19 C8 detail-only frontend queries

Actual Page/hook/QueryClient/Router/Dialog/network-boundary regressions obtained
two genuine REDs (default and root StrictMode): detail rendered correctly but
the page also fetched unused list and featured data. Change only the existing
call to useInspirations({ enabled: false }); the option already disables both
automatic queries, leaving imperative detail/copy/reset operations unchanged.

Fresh 55 affected cases passed across Page and actual Dialog lifetime suites;
separate corrected existing hook selector passed 36. Page coverage 89.47% lines,
77.77% branches,66.66% functions,85% statements exceeds unchanged68/55/61/66.
QA source/imported Page/hook/Dialog types and ESLint passed. Network assertions
now observe detail without list/featured requests; default/Strict compatibility
retained. Browser globals/query/portal/body overflow cleaned. App-wide build
consolidated after native ChatPanel integration; no current full-build claim.
Independent SOURCE_APPROVE/CLEAR; only this one product callsite plus new
actual-caller test changed. No cache/API/feature/auth policy, dependencies,
Actions or provider changes. Evidence ../m19-detail-query-scope/.
