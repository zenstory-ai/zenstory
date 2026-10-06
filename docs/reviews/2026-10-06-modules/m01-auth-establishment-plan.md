# M01 auth establishment — regression-first lifecycle plan

Frontend map source candidates: login/verifyEmail increment auth generation only
AFTER awaited API success; unlike callback/refresh/init, no request-affinity guard.
A pending login/verification could restore tokens/user after logout or replace a
newer successful account. Actualproviderunmount could mutate localstorage too.
This is source mechanism, not yet a dynamic incident/lostdata/providerclaim.

Native phase1ownership: newcontexts/__tests__/AuthContext.lifecycle.test.tsx only,
evidence frontend/m01-auth-establishment-proof. Root/child AuthContext source
hash equal at start. Freeze source+ownedtest baselines, default+rootStrictMode
real AuthProvider; mock only authApi/fetch/analytics network boundaries, real
localStorage. Defer both APIs across explicitlogout, auth:logout, neweridentity
establishment, actualproviderunmount; positive successfulsame-lifecycle and
requestfailurecontrols. Assert user/cache/access/refresh identity and analytics
side effects. No productsource edit before genuineRED/advice/root phase2approval.

Potential minimum phase2: reserve existinggeneration before eachawait and only
complete currentgeneration; lifecyclecleanup must revoke oldinstance authority
without breakingStrictModeinit. Caller Promise<void>/navigation semantics and
stale error behavior require review, not newcancelledoperationframework.
OAuthCallbackpage SSOcontinuation/registrationpolicy races separate ownedproofs.
Do notblanketabort server-account operations or introduce store/query abstractions.

Keep all prior cacheinit/refresh/OAuth success/identitycachepurge/analytics/guards
controls. Fresh affectedtests/scopedcoverage/types/lint/build/localfake browser
if materiallyrequired and independentreview before narrowintegration. No real
login/OAuth/email/credentials/provider/remoteActions/install/config or secret use.
All23/release and mainCIdeferred remain rootowned.

## Phase2 independent DESIGN CLEAR — exact boundaries

m06_undo_design approved: only AuthContext reserveexistinggeneration beforelogin/
verifyEmailawait, onlateSUCCESS throw DOMException('Auth establishment superseded',
'AbortError') before allstorage/user/cache/analytics; don'tsilentreturnPromisevoid.
Initializationeffectcleanup only++generation, neverclearsstorage, leavesloading
finallypolicy/refresh/OAuth/initrules otherwiseunchanged. Actual Login/VerifyEmail
catchonlyDOMException+AbortError returnsbeforeerrorUI/navigation/codeclear; existing
finally clearsbuttonloading. OrdinaryApiError/TypeError sameerrorUI. Lateststarted
intentwins evenBstartsandfailsbeforeAsucceeds. Realproviderproof16genuineRED+18
compat controls; sourcephase convertscancelledsuccessfulresponses toexactAbort
rejection withoutweakening side-effect assertions, addsBfailedordering/pagecontrols.
Sevenownedphase paths include threeproduct, contextlifecycle/pages2tests/newdoc;
evidence m01-auth-establishment-repair. OAuthCallback/PublicRoute/ssohelperC2 and
register/VerifystatusC3-C4 remainseparate, noscopeabsorption.
