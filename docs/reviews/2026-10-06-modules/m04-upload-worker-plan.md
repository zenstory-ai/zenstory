# M04 upload worker-boundary proof plan

The two multipart routes are async but execute synchronous ORM/history work in
their coroutine. The existing export route already has a worker-identity
regression. Prove the **actual HTTP upload version/persistence boundary** before
choosing a change: record the running request-loop thread and actual strict
initial-version thread for one material and one draft, retaining real uploads,
ownership, canonical folders, transactions and versions; fake only provider/cache
side effects. No sleep-only responsiveness or production latency claim.

This root-owned new test is independent of the native order-repair test/source
ownership. No upload product edit is authorized yet. If the worker-identity
expectation is RED, obtain separate minimum session/thread-boundary advice before
changing async reads or moving persistence. Existing multipart per-input versus
request-atomic history contracts and approved expiring-session DTO/query repairs
must remain intact. No new executor, async ORM, session splitting or dependency.

## Implemented bounded result, independent source review pending

Independent architecture advice CLEAR. Only the two route declarations and their
three reads changed: sync FastAPI handlers + `UploadFile.file.read()`; every other
function body is byte-identical to this slice's immutable baseline. Existing
four direct-call test files remove only await/asyncio.run upload adapters.

Actual HTTP thread RED: two200 responses/realv1 calls on the loop thread. Final
runtime controls GREEN:87 affected SQLite cases;18 actual PG upload cases plus
two material/rollback-gate cases; then four final cases covering the strengthened
two HTTP/v1 controls and the two draft/rollback-gate adapters. No source changed
after these runs. No claim of a single combined111-case run. Ruff passes; scoped
single-file mypy retains the same five baseline diagnostics, **not type GREEN**
and not a replacement for the earlier three-file10-error/global348-error scope.

The first PG creation command incorrectly used a transaction context for CREATE
DATABASE, then the shell continued to20 missing-database setup errors. Those are
**invalid fixture errors, not product REDs**; retained as
`m04-upload-worker/invalid-missing-database-fixture.txt`. A prerequisite-verified
absent-name UTF8/template0 lease was then created and the corrected20-case run
passed. Only this owned database may be dropped; final cleanup receipt required.

Source scope/commands/receipts under `m04-upload-worker/` in the audit evidence
directory. Native order repair owns separate create/update/reorder functions;
root will integrate its phase delta without overwriting this sync-upload slice.
Final combined source coverage and independent source review remain pending.

Nonclaims: FastAPI dependency enter/endpoint/exit need not be the same OS thread;
handler Session use is serial inside one worker invocation. Async auth dependency
can still do sync DB work on the loop; this proves only the upload-handler
boundary. No production p95/throughput gain or universal authenticated-request
off-loop guarantee; one existing AnyIO worker is occupied for bounded upload work.
