# M01 R4 verification integrity — regression first

Read-only mapping exposed ignored code-deletion failure, non-atomic code consumption,
disabled unverified accounts issuing credentials, and verification/referral state
committed before refresh-record persistence. Prove distinct reachable defects
before source changes; current successful/invalid code/API paths must stay covered.

Root owns new tests/test_api/test_verification_integrity.py and evidence
m01-verification-integrity. API tests use real ASGI/JWT/SQL with only correct-code
and external mail/provider boundaries local. Real unique refresh JTI violation
proves transaction failure; fresh reader must not see partial verification.
Disabled account test must reject before code consumption; active control returns
persisted tokens. No real user, mail, credentials, prod mutation.

Service consumption proof uses existing installed redis-server in uniquely owned
short /private/tmp temporary directory, Unix socket only (TCP port0), persistence
disabled. Actual Redis reads/deletes; controlled two-reader barrier proves two
successful consumers of one code. Transport failure injection at client's command
boundary must not return successful verification. Native/shared services are not
reset or stopped. Own client/server/socket/PID exited/cleaned in finally. No install,
new dependency, schema, shared Redis, provider, Actions or remote Git.

After genuine RED, bounded design proposal: strict inactive-account guard before
code use; one existing SQL transaction for User/referral + refresh record; atomic
Redis compare-and-delete helper consumed in verify_code, preserving attempts/error
policy and retry cleanup helpers. No distributed SQL/Redis atomicity promise, Redis
framework, cooldown/rate rewrite or restore-on-error invention. Independent advice
before implementation; existing service/API compatibility tests and scoped static
checks after repair. R3 auth/CI four reviewed paths remain immutable.

## Genuine pre-repair result and primary reference

Fresh six-case run: **three genuine RED +one incorrect collision-status assertion +two controls GREEN /no skip**. Actual API
inactive account issued credentials; real UNIQUE refresh JTI violation returned existing409 (not assumed500);
its fresh-state assertion was initially unreachable; real owned Unix Redis two consumers both succeeded; injected
consumption RPC failure still succeeded and retained code. Own server exited0,
request/conftest resources cleaned; baseline bytes now frozen in evidence/before.

Atomic single-key GET/conditionalDEL design follows Redis's documented Lua execution
semantics; key is passed in KEYS, comparison string in ARGV, static script (no
user-derived script source). [Redis scripting documentation](https://redis.io/docs/latest/develop/programmability/eval-intro/).
One bounded script, not a new cache/function framework or Lua migration. Original
attempt/cooldown/resend policies remain separate. Fixture portability: use /tmp
(short Unix path on macOS and Linux); explicit CI test Redis URL may lease only
unique own keys on loopback6380/1 with no flush/server shutdown, avoiding binary
availability-based skips in CI. Local default still owns Unix-only Redis process.

Independent DESIGN CLEAR requires collision409/RESOURCE_CONFLICT and correct
`attempts:` key cleanup; both tests fixed before product changes. Corrected
collision-only untouched-source rerun must establish the partial-state RED.
The earlier four failures are not mislabeled as four genuine product defects.
