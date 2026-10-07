# M01 shared access dependency worker repair

The shared `get_current_user` dependency issued synchronous User SQL on the request event-loop thread. It now uses a synchronous declaration so FastAPI dispatches the same body through its existing worker pool. Only `async def`→`def` changed; signature, Depends, JWT validation, errors, returned User, Session lifecycle and every other function remain byte-preserved. Four direct service-test calls remove their obsolete awaits. Pure active/superuser gates, unused optional dependency and higher auth/OAuth handlers are outside this slice.

## Plan and authority

Before edits, freeze root-matching core/service-test hashes and the phase1 HTTP regression; retain the first valid1FAILED/5PASSED receipt read-only. Independent architect m06_undo_design DESIGN CLEAR and explicit root phase2 authorization allow this declaration-only repair and four test adapters. Apply the exact text changes, then fresh-process actual HTTP proof/service/control coverage, scoped Ruff, baseline-aware types availability, exact AST/text preservation and owned resource cleanup. No helpers/executors/locks/global config/session policy or provider changes.

## Evidence

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/m01-auth-worker-repair/REPORT.md`; immutable RED: sibling `m01-auth-worker-proof/red1.txt`.

First proof: HTTP200/exact public DTO/one User SELECT, same thread as event loop; one genuine failed placement assertion and five passing controls. New HTTP test retained byte-identical. After declaration change, fresh batch **66 PASSED**: six actual ASGI worker/auth controls, 50 core service tests, ten selected actual me/login/refresh controls. The one SELECT runs on a worker; cold default-expiring request Sessions close; missing JWT/bearer issue zero User SQL and missing/inactive actors preserve errors/headers. Core-only statement coverage **89.36% (126/141), >= unchanged80**. No whole API/full auth coverage claim.

Scoped Ruff passes. Static checks prove exact one declaration change, exact four await removals, identical body/args/return AST, all other source text unchanged. Type baseline attempt cannot execute because the existing venv has no mypy module; no install or type GREEN claim. First coverage command used three wrong node names (exit4, no execution); saved as invalid harness history, not product RED; corrected selectors AST-verified before valid run.

Each probe finally removes listeners/overrides and disposes/removes owned SQLite DB; fresh process conftest DB also disposed/unlinked. No shared PG/SQLite cleanup, app startup/lifespan, providers, mail, OAuth, server/browser, secrets/envfiles, dependencies or remote/Actions operation. Root's independent auth.py/oauth.py bootstrap/SSO drift is excluded from baseline and delta.

## Limits and integration

Framework worker dispatch does not promise one fixed OS thread for yielded Session enter/dependency/exit; use remains serial. This removes only the shared dependency's loop-thread SQL, not higher async handler DB work/manual Redis/races. No timing/p95, global types, full M01/all23 closure or independent source approval. Leave bounded source/tests/doc uncommitted for root review/narrow integration. Deferred main-CI remains queued/unstarted.

## Root narrow integration and final bounded combined receipt

Independent source APPROVE/CLEAR followed four-ownedpath phasebaseline merge
0conflicts; onlysharedcore declaration/4testawaits/newHTTPtest/doc, no stale
childauth/OAuthfilecopy. Parentfilled unavailablevenvtypegap with existingHomebrew
mypy: corebaseline1→1, NOTGREEN; freshrootaftertypecomparison also1unchanged.
Fresh **102PASS/15.08s**, combinedcore+OAuth **87.76%** >unchanged80; core89.36%,
OAuth86.99%. Includes prior66core/HTTP/authcontrols andcurrentRoot SSO7, SQL
bootstrap3, providererrors8, OAuth13/helpers5. Newshareddependency warrants this
consolidatedcoverage/rootintegrationrun; unchangedpassed checksnotreflexively
repeated. FinalaffectedRuff0. OwnedrootconftestDB disposed/removed/probeDBs0.
Root evidence m01-root-combined/result.txt/coverage.json/green2-command.json/
process-cleanup-green2.json/mypy-root-comparison.json; nativephase manifests
unchanged, Rootdoconlythis attributedadditionalsection. NotfullM01/globaltype
/aggregate/CI/releaseclearance. HigherasyncDB, Redis/provider/verification/races
remainexplicitnextproofs; native R3actualPGregression-only and C1sourcephaseactive.
