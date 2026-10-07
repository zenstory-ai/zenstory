# M04 multipart uploads — bounded source APPROVE / CLEAR

Independent architect reviewed the current source and receipts (did not rerun
them), finding no blocking upload defect. This closes the material/draft upload
storage and postcommit-read slice, not full M04/all23/release.

## Architecture and bugs

`api/files.py` reuses `_reject_embedded_nul` for persisted multipart title/body.
Material validates all derived snippets before staging its canonical folder.
Drafts prepare an entire input's chapters without attaching them to the Session;
only a fully valid input stages rows and consumes append slots. Final inferred
order is checked against INTEGER's upper bound, without clamping or rejecting a
legal title-derived MAX merely because its preliminary append candidate exceeds
MAX. The sibling aggregate keeps all child types and filters project, parent and
active state. This repairs a real foreign-project dirty-parent order influence.

Strict initial versions remain outside per-input validation catches; unexpected
ORM/baseline/commit errors abort the accepted batch and folder together. No new
transaction layer, savepoints, commits, quotas or dependencies were introduced.
Existing encoding, byte/character/file caps, chapter splitting and errors remain.

## Performance

SQL `max` returns **1 row instead of 500 sibling-order rows**, not fewer queries
or constant database work. Precommit flush plus response DTO/user-scalar capture
removes expired ORM reads after the sole outer commit. Actual PostgreSQL with
production-default `expire_on_commit=True`: material 1/3 snippets **1/3 → 0**
postcommit File/User SELECTs; draft 1/50 chapters **2/51 → 0**. Response DTOs match
fresh persisted rows including timestamps. Index/cache effects remain after
commit and best-effort; a failed commit emits neither. Material deliberately
retains its existing no-index/no-cache behavior. This is a query-count result,
not production latency, total-query constancy, or memory/throughput certification.

## Validation receipts

Evidence root: `/private/tmp/zenstory-module-audit-evidence-20261006`.

- `m04-upload-storage-red.txt`: **10 genuine actual-PG failures**, no fixture
  errors (six NUL storage, three derived/append INTEGER overflow, one foreign-edge
  overflow). `m04-upload-order-query-red.txt`: one genuine 500-row query RED.
- Storage repair: **115 passed / five files**, including all 11 PG cases.
- `m04-upload-postcommit-invalid-savepoint-observer.txt`: four invalid observer
  failures, three compatibility passes. SQLAlchemy emits `after_commit` for
  SAVEPOINT release too; these are **not product REDs**.
- Corrected outer-commit observer: **four genuine expired-row REDs**.
- `m04-upload-storage-performance-green.txt`: **122 passed / five files**,
  including 18 actual-PG storage/performance/fault/empty-response cases.
- Final `m04-upload-final-coverage.txt/json`: **264 passed / 11 files**;
  `api.files` **86.44%** (767 statements, 104 missed), unchanged gate80.
- Ruff, AST compile and diff check pass. Scoped mypy has the **same 10 existing
  diagnostics**, normalized against the previous slice; **not typecheck GREEN**.
  An initial wrong-cwd mypy wrapper is explicitly named invalid, not validation.
- New PG module is added to the existing serialized UTF-8 regression step;
  local workflow-contract one missing-wiring RED → **nine GREEN**. No remote
  workflow inspection/trigger/rerun or gate changes.
- Owned-function delta/hash receipts preserve every non-upload function from
  the phase1 snapshot, including `get_file_tree`.

## Remaining boundaries

Project SHARE does not guarantee unique sibling orders among concurrent creators.
The async upload handlers still perform synchronous ORM work; no measured event
loop bottleneck justified changing handler/caller semantics. Material missing
indexing is preserved, not silently expanded. Deep tree HTTP serialization is a
separate genuine regression now being reviewed; frontend Editor lifecycle/token
work is a separate native-tmux lane. Full M04/all23, aggregate/physical-browser
checks, global type diagnostics and exact-SHA release remain open. Deferred main
CI audit remains queued, unstarted; no commit/push/deploy/provider mutation.
