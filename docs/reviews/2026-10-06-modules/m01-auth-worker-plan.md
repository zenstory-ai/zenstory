# M01 shared access dependency worker — regression-first bounded plan

R1fullbackendmap confirms no-await async get_current_user performs sync User
lookup on request loop. Already-offloaded bcrypt/mail/Redis and M14Agentauth
are separate, no blanket regression or entirehandler conversion claimed.

Phase1native ownership: new tests/test_api/test_auth_dependency_worker.py only
and evidence delete/m01-auth-worker-proof. Freeze current core/auth_service.py
and test_services/test_auth_service.py (root/child same81de165.../4a544928...);
no product edits until independent measured-source design approval. Testactual
ASGI /api/auth/me with real auth/JWT/User lookup, override get_session only for
owned testfixture, cold Session/productionexpiry. SQLAlchemy SELECT thread+count
versus requestloopthread: expectedoutside loop exactlyoneUserlookup. Preserve
active/unknown/inactive controls, do not fake authentication/ORM/worker invocation.
No timing sleep or productionlatencyclaim. Own listeners unhooked aftertest.

Minimum phase2proposal aftergenuineRED: asyncdef get_current_user→def unchanged
body/signature, let FastAPI existing run_in_threadpool own single dependency.
Adapt four directservice test awaits; get_current_active_user/superuser puregates,
unused optionalgetuser and registeredasync/auth/providerhandlers byte-unchanged.
No newexecutors/threads/sessionhelper/ORMframework/cache/locks/install/config.
All attachedSessionuses serialnotarbitraryparallel; generatorenter/exitmapdifferent
threads remainsframework-owned. Root reviews advice beforephase2authorization.

Localtargeted worker+service/currentMe/authcontrols, scopedcoverage/staticchecks
and independentreview before narrowintegration. No remoteActions/provider/email/
OAuth/sharedDBmutation. Broad M01handlers/races/verification/providercallback
trust/error cases remain separate nextproofs; all23/release stillrequired.

## Phase2 design approval and source ownership

ActualfirstHTTP1RED5compat controls, oneUserSELECT loopthread; childownedDB/
listeners/overridecleanup verified. Independentarchitect m06_undo_design DESIGN
CLEAR authorizes only sharedget_current_user syncdeclaration, fourdirecttest
await adapters; otherasync/puregates/unusedoptional leftunchanged. Framework
serialSessionphases mayusedifferentworkers, not onefixedOSthread claim. Native
sourcephase task queued same%253PID22879, evidence m01-auth-worker-repair.
Root auth.py/oauth.py separatebootstraprollback drift is attributed/notcopied.
