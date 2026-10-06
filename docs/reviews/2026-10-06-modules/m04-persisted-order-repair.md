# Persisted order repair plan

Bound the final persisted integer through one signed32 validator; leave display sorting unchanged. Reuse the new-file allocator in tools. Validate prospective updates and the complete reorder batch before attached-row assignments. Translate only numeric resolver ValueError to API400; tools retain ValueError. Preserve Project locks, canonical repair transactions, history, approved upload/tree/delete functions and Agent nonnegative request policy.

Regression first: retain 61 PG cases; add in-session multi-field rejection, later reorder failure, canonical recovery caller rollback, and screenplay reuse rejection. Then add pure boundary tests and local serialized-CI required-path contract. Test with production expiring Sessions in a newly leased UTF8 template0 database. Run relevant title/writer/history and upload PG controls, scoped coverage at unchanged80, lint, targeted mypy baseline comparison and function/text diffs. Drop only the owned lease and prove shared health. Leave uncommitted changes and evidence for independent root source review.

Baseline: five product files matched root. CI-only sync authorized separately and re-frozen; new PG file absent root is lane-owned, not drift. No product edits before regression receipt. No full-module or publication claim.

## Verified bounded handoff


- apps/server/utils/title_sequence.py:19,266 — canonical MIN_FILE_ORDER/MAX_FILE_ORDER, tiny validate_persisted_file_order, validation after persisted sequence normalization. build_sequence_sort_key and every other existing utility function are byte-identical.
- apps/server/services/file_tree_rules.py:24,211 — canonical MAX alias reexport; inferred sequence and sibling append returns validated. All other functions remain byte-identical.
- apps/server/api/files.py:770,860,1349 — create resolver error mapping; prospective update fields with parent validation before order; complete reorder resolution before mutation. All other functions and outside-writer module AST are unchanged, including DTO lower bounds, upload, tree, delete and canonical helpers.
- apps/server/api/agent_api.py:660,882,1032 — create numeric validation; prospective title/body/order update; prospective parent/order move. Other functions/module AST unchanged; request nonnegative policy unchanged.
- apps/server/agent/tools/file_ops/crud.py:119,598 — remove duplicated allocation in favor of resolve_new_file_order after actual parent/type/title normalization; preserve warning and episode inference. Prospective update title/body/parent/metadata/order before assignments. Other methods and nested canonical repair helper byte-identical. No new commit/rollback inside validation; existing valid-write commit policy retained.
- apps/server/tests/test_services/test_persisted_order_bounds_postgres.py — existing61 cases retained; seven added rejection/atomicity cases, final68. Real PG, expiring Sessions, locks/ownership/history, fake actors and local external-effect captures.
- apps/server/tests/test_utils/test_persisted_order_bounds.py —11 pure signed-bound/final-override/display-separation cases.
- .github/workflows/ci.yml and scripts/ci/workflows.test.mjs — exactly one required serialized PG test path and its local assertion.
- docs/reviews/2026-10-06-modules/m04-persisted-order-repair.md — plan/results/handoff.

No other source/test/config/dependency files are owned or intentionally changed. source-baseline.diff is against the frozen phase, never the entire HEAD. The PG test must be installed as a new complete file in root; its phase snapshot already contains the earlier61 cases.

## RED receipts and harness distinctions

regression-red.txt (exit1): initial68-case run30FAILED/38PASSED on fresh root-matching five-file source. The existing23 cases again reach actual SQLSTATE22003 rather than domain errors; six added multi-field/late-reorder/canonical rollback cases also fail. The seventh screenplay case used an ordinary parent and reproduced invalid creation; it did not exercise reuse and is not cited as reuse evidence.

corrected-reuse-red.txt (exit1,1FAILED/67deselected): corrected actual canonical script-folder fixture and existing legacy draft. Fresh process with all five immutable phase source files temporarily replayed, then final bytes restored in try/finally. The actual reuse path attempts UPDATE file_type='script', order=2147483648 and raises DataError. No resolver/validator/ownership/lock was mocked away. temporary-baseline-replay.json records restoration hashes.

ci-contract-red-verified.txt (exit1): current contract against exact synchronized CI baseline fails on missing required new PG path. CI final bytes restored, then nine-check contract GREEN exit0. Initial CI shell wrapper lost child exit status; verified receipt supersedes that exit claim.

isolated-api-coverage.txt (exit1,175PASS/1FAIL): evidence plugin lambda name broke a legacy task.func.__name__ assertion. This is harness failure, not product RED. Named scheduling stubs preserve that contract; final batch below passes.

Earlier affected-writers/utility/api receipts are functional observations with incomplete uniform external scheduling isolation. Final proofs use local_index_boundary.py to stub real scheduling entrypoints; writer wrappers/resolvers/validators/history stay real. Bare-snapshot mypy13 diagnostics were an invalid package-identity comparison; package-preserving baseline is used below. Full distinctions are retained in harness-history.json; no xfail, relaxed assertion, skip or lowered coverage gate was introduced.

## Fresh GREEN and measured behavior

- final-pg-matrix.txt/XML: exit0,68PASS, zero skips/xfails. Final test bytes include explicit attached FileVersion model equality before rollback.23 invalid boundary cases return16 APIException400 VALIDATION_ERROR and7 Tool ValueError; zero SQL error events, complete fresh-reader File/history snapshots unchanged, zero index/cache/activation/background effects. green-observations.json retains53 recorded case facts, including all23 rejections.
- Added seven cases prove attached multi-field/token/history preservation for Web/Agent/Tool updates and Agent move; later bad reorder item leaves earlier attached sibling unchanged; canonical creation repair may legitimately flush its folder, then caller rollback removes it while rejected requested row is never added; invalid screenplay reuse never promotes the attached candidate.
- The retained38 controls cover exact MIN/MAX and normal negatives where allowed, Agent nonnegative policy, supplied schema out-of-range rejection versus derived runtime rejection, title/metadata inference, title MAX with peer MAX, active same-parent/project mixed-type sibling domain, real version1/content baseline, version/token advancement, valid move/reorder and unchanged Web move non-inference behavior.
- final-affected-coverage.txt/XML: exit0,301PASS. Includes corrected68-case PG matrix, pure/title tests, existing files/helpers/review/draft APIs, tool regressions, real history, Agent structure/version, writer timestamp/query, realPG tree-writer/precondition and snapshot controls. Every external index schedule entrypoint is stubbed by named local functions; tasks are not sent to a provider.
- Scoped coverage (--cov=api.files --cov=utils.title_sequence, -o addopts=, unchanged --cov-fail-under=80):812/945 statements,85.93% total; api.files689/803=85.80%, utility123/142=86.62%. Earlier standalone utility gate also passed83.80%/130cases. Final history-equality assertions were strengthened during the301 batch and then the exact final68-case file was freshly run; no product source changed between those checks.
- upload-pg.txt: exit0,18PASS from read-only /private/tmp/zenstory-module-audit-evidence-20261006/m04-upload-worker/starting-source/apps/server/tests/test_services/test_file_upload_postgres.py. Matching child async upload baseline, including MAX append error followed by legal title MAX override, strict initial history and DTO/query controls. Borrowed baseline hash/path retained in borrowed-upload-receipt.json. Root's current sync-handler test adaptations were not copied.
- Ruff final product suite and final test lint: exit0. AST parsing, permitted function/text delta, outside-writer AST and whitespace checks PASS. No compilecache generation requested.
- Package-preserving scoped mypy: baseline10 errors/current9, exit1 both, no new normalized diagnostic. One legacy Any-return disappears because allocator now returns typed validated local. This is NOT mypy GREEN. Expanded follow-imports=silent scan was57baseline/56current, also not whole-backend GREEN.
- Local CI contract: nine checks PASS; no Actions/remote mainCI inspection or dispatch.

Exact commands, durations and exits are in commands.jsonl; run.py records argv and sets testing SQLite environment, owned PG URL, memory rate limits, dummy JWT/embedding sentinel, PYTHONDONTWRITEBYTECODE and evidence-local coverage. Final validation uses PYTEST_PLUGINS=local_index_boundary and evidence/child PYTHONPATH. Runtime is Python3.12.13, PostgreSQL14.22 and Node25.9.0; no CI15/Node20 equivalence claim.

## Scope preservation and integration

function-delta.json permits only the approved functions and new validator. canonical-helper-preservation.json confirms nested repair helper exact text. root-upload-separation.json confirms child API delta is only create/update/reorder; authoritative root's concurrent delta is only upload_material/upload_drafts, and remaining functions match byte-for-byte. Root should merge these separated changes from the saved phase baseline, preserving its upload worker fix; do not replace the entire API file or apply a whole-HEAD diff.

All old pure display sorting, Project lock semantics, schema bounds, canonical/delete/tree code and other writers remain unchanged. No new order clamping, schema constraint/migration, global display policy, depth feature or framework was added. Type baselines and scoped coverage remain limited to measured paths; central order validation is not a full backend audit.

## Owned database cleanup and handoff

Owned lease zenstory_m04_order_repair_20261006 was created only after absence check, UTF8 TEMPLATEtemplate0/C locale; all PG fixtures bound Sessions to that engine and closed/disposed sessions. pg-cleanup.json proves0 sessions before drop, only leased DB dropped, catalog0 afterwards and shared SELECT1 health1. No shared cluster reset/config, provider/dependency/remote mutation, commit/push or publication was performed.

Leave the ten owned paths uncommitted. Final manifest and baseline-relative patch accompany this report. Coordinator owns independent source review, three-way integration, combined final gates, fullM04/all23 closure and publication; deferred mainCI audit remains unstarted. This lane stops here.
