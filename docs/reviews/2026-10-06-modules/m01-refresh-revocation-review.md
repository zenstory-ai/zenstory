# M01 refresh continuation revocation

Actual baseline owned-PG HTTP proof: one genuine RED, three controls. Old-token
lock alone let logout SELECT old records before rotation inserted a descendant;
logout finished200 while that child refreshed200. No replay of old credentials
was used to accidentally revoke the family and hide the defect.

Independent DESIGN CLEAR authorized two existing-path User locks. Refresh now
loads current User under FOR UPDATE/populate_existing before JTI lock. Shared
logout/password helper locks scalar User.id before enumerating active records;
no reloading of pending password changes. The revocation timestamp is captured
AFTER the gate. No schema, dependency, helper framework, access-JWT blacklist,
error/response/rate/cleanup/legacy policy change. Same-user operations serialize;
other users and ordinary access auth do not use this gate. Explicit new
login/OAuth authentication after logout is not a continuation of old refresh.

Final actual PostgreSQL six tests GREEN: blocked UserSELECT logout, blocked dirty
UserUPDATE password, durable new password/descendant revocation, both sequential
orders, another user's refresh plus stateless access-JWT contract, empty-token
logout followed by real NOWAIT User lock. Cold default-expiring sessions, real
handlers/JWT/ORM/locks, only owned session dependency overridden. Both concurrent
cases revoke the newly inserted child, leave old record rotated, and child HTTP
refresh returns existing401. `revoked_at >= issued_at` checks chronology.

Invalid candidate receipts are retained: first green wrapper wrong cwd executed
zero tests; next5PASS/1 instrumentation failure treated UserUPDATE as TokenUPDATE.
Corrected observer explicitly distinguishes tables, final6PASS/2.49s. These are
not extra product failures. Original genuine RED remains immutable.

Four owned uniquely leased UTF8/template0/C DBs (baseline/invalid/final fixtures)
all cleaned: zero sessions/catalog, shared health1, no forced termination. The
owned conftest SQLite files were disposed/unlinked. Shared PG was not restarted.
New regression is included in the existing serialized PostgreSQL CI lane and its
local workflow contract (nine tests GREEN); no Actions run was triggered. Fresh affected SQLite/auth batch **104 passed**, auth/core scoped coverage
**87.29% ≥80**; Ruff0 and local CI contract9GREEN. Scoped auth mypy retains one
pre-existing line216 bool no-any-return diagnostic; no new diagnostics, not GREEN.

Evidence `m01-refresh-revocation-root/`: pre-repair bytes, red/final logs/XML,
commands/lease/cleanup, source-proof (only two functions changed), final-manifest,
CI contract. Independent final **SOURCE APPROVE / CLEAR** confirms exact four-path hashes,
lock order, timestamp ordering, password autoflush and empty-token release.
SQLite cross-process serialization, instantaneous universal JWT logout, new-auth
intent ordering, all M01/all23/full type/CI/release are not newly promised.
