# M19 C4/C5 feedback query and worker bounds

Bounded root-owned repair, independent designs CLEAR; not whole-M19/release.

## C4 actual query work
Real authenticated cold expiring ORM requests against32/256 rows passed DTO,
first/middle/last/out-of-range and combined-filter controls before the initial
load budget failed. Before: every unfiltered page materialized32/256 feedback
rows, including empty out-of-range pages; combined filter materialized6/43 for5
returned. Now no screenshot-presence filter uses one SQLCOUNT and one bounded
ordered offset/limit SELECT, materializing exactly5/5/3/0 and filtered5 rows.
Explicit true/false retains filesystem-backed presence filtering before Python
paging; present/missing/legacy/outside-root controls are retained.
Tradeoff: one extra count query for bounded transfer/DTO/filesystem work, not a
production latency claim; explicit presence-filter work is still O(matches).

## C5 actual worker/read work
Actual multipart/text HTTP observed real feedback INSERT and exclusive file-write
on the event-loop thread. Oversized screenshot read5243904 bytes before rejection;
global25MiB request-body limit was already present (not unbounded input).
Only handler async→sync and read→file.read(5MiB+1) changed. FastAPI runs its ordinary
worker with sequential request Session use; no executor/helper/parallel session.
Real post-fix observations show all read/INSERT/write off-loop. Near5MiB stores
all5242880 bytes unchanged; oversized read returns5242881 then400/no row/file;
controlled actual INSERT failure500 rolls back and removes its owned screenshot.
Text response/trim/status/auth, image validation, collision safety and persistence
cleanup preserved. Direct-call tests merely no longer await the sync function.
Multipart parsing is not claimed to be capped at5MiB, nor is retention changed.

## Verification and scope
Fresh combined40PASS/93.31% (>unchanged80) before SQL typing repair. Duplicating the
existing untyped sort revealed15→16 scoped mypy diagnostics; fixed expression
annotations with the existing identity-returning SQLModel col() utility, not a
suppression. Fresh final affected admin24PASS/98.59% plus unchanged16 submission
passes reused; not a fresh final40 aggregate. Final scoped two-product mypy0 and
Ruff0. AST only two authorized functions plus imports changed; all other defs
retained. No schema/index/sharedconfig/dependency/API contract changes.
Own SQLite/runtime/conftest DBs removed, SQL/load listeners, file/read observers,
env/dependency overrides restored and engines disposed. No provider/production/
Actions/push. Independent SOURCE_APPROVE/CLEAR. Adjacent rawlogs/XML carry exact
measurements; C4 baseline receipts under ../m19-feedback-query-bounds/.
