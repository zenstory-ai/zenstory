# M23 — infrastructure and CI/CD source review

Independent whole-module verdict: **MODULE SOURCE_APPROVE / CLEAR**. This is source and bounded validation closure, not release approval or the deferred historical main-CI investigation.

## Covered leaves and repairs

Startup/database engines, optional index DDL, Redis pools and fail-closed verification, email dispatch, request/correlation/body-limit/rate middleware, dashboard fallback, liveness/readiness, production image/dependency contracts, compose/Railway configuration, and fail-closed CI/release producers were reviewed. Optional PostgreSQL indexes use separate savepoints without hiding fatal create_all failures. Both PostgreSQL engines pin UTC while retaining URL/libpq options. Readiness probes start concurrently with independent deadlines/results; /health remains static liveness. Memory rate fallback prunes expiry and bounds keys at 5,000; dashboard value/version updates use the existing lock. Request streaming caps and logging cleanup/redaction preserve existing contracts.

## Local evidence and its limits

Evidence root: /private/tmp/zenstory-module-audit-evidence-20261006. Database-core 5 PASS, readiness 16 PASS and memory targeted 38 PASS are separate receipts. Driver timezone behavior has separate actual PostgreSQL evidence. The current mandatory serialized-PG CI contract includes all 26 active optional-PG files, binds both database environment variables and uses -n 0; local workflow contracts 9 PASS. Deliberately skipped future tool-concurrency test is not represented as covered active behavior.

**Receipt correction:** m23-pg-regressions-local-batch-final.log is 21 PASS / 1 FAIL, not an all-green batch. The failed admin-skills fixture omitted points_transaction; current fixture includes it and separate m18-admin-skills-pg-fixture-green.log proves 1 PASS. The current 26-file serialized lane is now freshly validated below; this earlier receipt remains a failure, not retrospectively green. Default Web fixture portability fixes have fresh affected 241 PASS; unrelated prior 276 passes are not a fresh combined 517-PASS receipt.

## Accepted limits / remaining delivery gates

Redis fallback is process-local; default deployment is one worker, not a global distributed rate limiter. Fallback version cardinality and image optimization have no production-scale measurement. Liveness deployment policy is intentionally distinct from readiness. init/create_all is not migration authority. Local Node 25 differs from CI Node 20. Exact-head global coverage, types/lint/build, necessary E2E, production image/provider-source checks remain required; complete serialized PG is now freshly green below. No dependency installation, Actions rerun, provider configuration, credentials or production data mutation was used for this review.

## Consolidated gate history — test isolation

The exact current 26-file lane was run locally, not on Actions. Cached PostgreSQL15.16 produced 300 PASS/4 FAIL/12 teardown errors: test modules retained tables/foreign keys/rows. An explicit matching serial-URL-only, module-scoped drop boundary repairs schema isolation; its seven ordinary unit cases and ordered seven-module PostgreSQL chain85 PASS, independent SOURCE_CLEAR. No create-all expansion, CASCADE or production engine change.

A separate long valid multibyte TOAST substring failure is PostgreSQL15.16 bug19406, officially fixed in15.17; dedicated owned15.16 reproduction failed and same source on owned15.17 passed. No app workaround or stored-corruption claim. New official test image only; shared images/services untouched.

The first full26 on15.17 was **291 PASS/13 FAIL**, no teardown errors. Missing user_subscription errors were caused by an earlier singleton-instance monkeypatch undo leaving a bound instance method that shadowed later class stubs; subscription tables were intentionally absent. This receipt remains a failed gate. Existing invalid/failed receipts are preserved.

## Final local serialized-PG gate

Independent three-test-path SOURCE_APPROVE/CLEAR: the real prior fixture and the identical API test now patch the existing service class, not its process singleton instance. A new ordinary regression invokes the actual fixture, proves undo leaves no own method, and proves a subsequent class override resolves on that same singleton. Pre-fix one genuine failure; post-fix ordinary/API31 PASS/Ruff0, then real ordered four-module PG127 PASS. No subscription schema, global singleton reset, delattr workaround, product code or assertion weakening.

Fresh exact current26-file lane on owned official PostgreSQL15.17: **304 PASS /0 FAIL /0 ERROR**, no skips, 95.42s. Evidence `final-serial-pg-fixture-fixed/root-pg.log/xml` and `pg-final-cleanup.json`: zero connections before normal DROP, zero catalog rows after, shared health1; no FORCE/shared cleanup. Native full default backend imported older fixture bytes; its separate report must disclose that drift and be supplemented by the targeted31/127/current26 receipts, not claimed exact-final-test-tree.

Fresh full Web:3581 PASS/one unchanged SSR skip, all-source coverage81.01L/72.01B/74.79F/78.19S exceeds original thresholds; full source/node types/lint/tokens/i18n and ordinary Vite/org/docs build pass. Site56 PASS on cached Node20; primary Web runtime Node25 differs from CI. Generator coverage, browser, exact pinned production images, final default backend, CI and release are separate remaining gates. No Actions were used to debug these failures.
