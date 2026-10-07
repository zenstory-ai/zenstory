# M01 verification integrity — SOURCE APPROVE / CLEAR

Independent source approval covers inactive-user rejection before code use, one
SQL commit for User/referral/refresh-record, and static single-key Redis atomic
compare-delete. Success is logged only after SQL commit/refresh; Redis failure or
code replacement cannot verify or reset attempts. Existing delete/resend/attempt
policies and global IntegrityError409 mapping are preserved.

Regression accounting: initial six tests = three genuine failures, one wrong500
collision assertion, two controls. After fixing the expected409/RESOURCE_CONFLICT,
untouched-source collision test reached the genuine fresh-state partial-verification
failure. A separate untouched-source Redis replacement test proved old requests
also deleted newly sent codes. Five genuine regression cases are established by
these separate receipts, not a fabricated five-case baseline run.

Final **50 passed/no skip; all three affected modules scoped90.21% ≥80**. This includes
nine existing actual API identity/referral/token/privacy controls, 23 service cases,
new API and actual Redis consumption cases, plus existing storage return/failure
contracts. A first repaired30-case run already passed; final50 was justified by the
additional current storage and API compatibility/coverage scope, not Actions reruns.

Ruff0; scoped mypy three baseline errors→same three, no new diagnostic, not GREEN.
The two invalid before-type receipts are retained (incomplete snapshot package
namespaces; then wrong repository cwd/config). Corrected package/cwd baseline uses
unchanged compiler configuration. Source-proof confirms only verify_email and
verify_code existing bodies changed, plus new consume helper; all other functions
are identical. CI unit step explicitly leases loopback test Redis6380/1, UUID keys,
no flush/shutdown; serialized PG lane remains intact. Local CI contract9GREEN.

Owned local Redis used Unix socket only/TCP0/persistence off, was shut down only by
its owner, exited and directory removed. Clients, override fixtures and owned
conftest DBs cleaned. CI shared branch closes its client and deletes only own keys;
no shared service reset. No new dependency/schema/provider/credentials/Actions.
Evidence: `m01-verification-integrity/` logs/XML, baselines, source-proof, manifests,
coverage, type comparisons and fixture resource properties.

Redis consumption occurs before SQL commit: on a later database failure the account
stays unverified but must request a new code. No distributed SQL/Redis transaction
or restore-on-error guarantee. Cooldown/send and higher async SQL performance remain
separate inventory work; this is not full M01/all23/CI/release approval.
