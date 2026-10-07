# M03 W2: daily record, streak and cache transaction proof

## Contract and scope

`POST /projects/{id}/stats/record` returns one result for daily activity and its
streak update. A failed database transaction must not retain additive daily
activity or publish a new dashboard cache version. A successful retry after a
failed transaction records the activity once. Cache invalidation follows the
commit of both pieces, so a reader during the write cannot populate the final
version with an old streak.

This is transaction atomicity, not idempotency for a repeated successful HTTP
request or a network failure after a successful commit. Preserve existing
date/threshold/deletion-activity and standalone service-call contracts.

## Proof before source changes

- Reuse the positively owned FK-valid SQLite HTTP fixture with actual JWT,
  ownership, routes, cold/default-expiring Sessions and migration-equivalent
  unique daily/streak indexes. Do not replace ORM or service methods.
- Use a new actual `DashboardCache` facade and its real memory backend. Remove
  only the generated user's/project's exact cache keys in fixture teardown.
- Seed today's zero daily row and yesterday's one-day streak to isolate the
  transaction gap from first-row allocation.
- Inject one real Session `before_commit` exception when the streak advances;
  read durable rows/version in a fresh Session, then retry the actual HTTP POST.
- Hold the same commit with events; issue an actual dashboard GET while held,
  release in `finally`, then compare fresh durable state and the final cached GET.
- Keep normal, below-threshold and historical-record controls.
- Report collected observations separately from assertions. An instrumentation
  miss is invalid evidence, not a product failure. No sleeps as concurrency proof.

## Candidate repair and gates

After valid RED and independent design review, use a route-owned transaction,
explicit `commit=False` service paths and SAVEPOINT-scoped first-row collision
recovery; preserve default standalone commit behavior and prior atomic SQL daily
increments. Refresh and capture the response inside the transaction, commit
once, then best-effort bump the version without accessing expired ORM rows. Do not
reopen the approved AI summary loader or unrelated quota/native-lane source.

Run affected HTTP/service cases and real PostgreSQL first-row/concurrent-counter
proof where the changed transaction path requires it; keep coverage gates,
normalized baseline type diagnostics, lint and source-boundary checks. No Actions,
provider calls, schema/dependency/lock changes or publication in this lane.

Independent DESIGN_CLEAR requires precommit DTO capture, exact standalone
`commit=True` defaults, and a real SQLite outer BEGIN before the first SAVEPOINT.
Added proof cases cover cache failure, fallible postcommit reads, first-row
rollback, zero activity and separate PostgreSQL daily/streak absence races.
