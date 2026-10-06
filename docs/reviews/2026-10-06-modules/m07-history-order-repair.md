# M07 R4 deterministic history ties — native source candidate

Attributed native backend Codex lane, 2026-10-06. Architect DESIGN_CLEAR and explicit root authorization selected the existing SessionLoader ID tie convention. Root source stayed read-only; this bounded candidate is uncommitted and awaiting independent SOURCE review/integration. M07/all23 closure and publication are not claimed.

The candidate adds `desc(ChatMessage.id)` after the existing timestamp DESC expression in exactly three queries: `api/chat.py:293` and358, and `agent/suggest_service.py:382`. A UUID ID is a deterministic secondary key, not a statement about message age. Latest LIMIT remains before reversal. All other product bytes, imports, worker declarations/bodies, projection/DTO, bounds, permissions, metadata, roles, quota, schema/index and SessionLoader/service/api.agent code remain unchanged against the current-root phase baseline.

Only current root `api/chat.py` and `agent/suggest_service.py` were copied into the authorized child counterparts before editing; old child copies and hashes are retained. This preserves root's approved R2 worker bodies instead of integrating an old-child whole-file diff. Root integrates the three-line phase delta independently.

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/m07-history-order-repair/`. `REPORT.md`, `product-preservation.json`, exact phase baseline/final source, import/SQL receipts, command/XML/coverage/type outputs and cleanup provide the handoff. The prior119-file R4 proof and all earlier frozen handoffs are immutable.

## Validation

- Original frozen proof:5 runtime consistency REDs/6 controls. The final actual PostgreSQL14.22 matrix passes all11 cases using cold default-expiring Sessions, real registered HTTP/JWT/User/permission/ORM/serializer and the actual Suggest prompt. Only its chosen LLM completion is substituted. Both candidate modules execute with current root dependencies; imported paths/digests and SQL source frames prove that the root's unrepaired queries were not used.
- Fresh configured-PG run uses exact current root shared conftest bytes in a disposable evidence test view. Optional lane provenance variables are removed after the bootstrap verifies candidate imports; the ordinary fixture still works. SQL observers retain19 history SELECTs across the cases, all with `created_at DESC, id DESC`, and unchanged limit bindings1/3/5/20/50/100/200 (200 is the unchanged SessionLoader control). Five formerly failing windows and their repeated cold reads now match the existing tuple-key convention.
- Current affected root Chat, feedback, worker, Suggest genre/service/parsing selectors:98 PASS. Scoped coverage of only the two real candidate modules is317/356 = **89.04%**, unchanged80 threshold. Chat140/165 =84.85%; Suggest177/191 =92.67%.
- Default no-PG discovery:11 module skips, exit0. Configured actual-PG run has zero skips. Fixture-only adaptations make provenance flags optional, remove/restore Redis/retrieval/provider settings locally, and restore singleton caches after constructing real services. Test-case bodies/assertions/decorators are exact; no authentication/ORM/history mocks were added.
- Ruff and in-memory compile/AST pass. Fresh root-baseline/candidate scoped mypy runs each report22 existing diagnostics; normalized Counter comparison has zero added. Whole-backend/global mypy is not GREEN.

The accepted fresh runs use a preloaded hash-verified local tokenizer asset and an evidence-only nonlocal socket guard; zero nonlocal attempts were recorded. A prior runner missed this cache prerequisite: a fresh owned TMPDIR gained the tiktoken vocabulary, whose installed cache-miss loader invokes `requests.get`. Earlier functional outputs are retained but do not establish the offline constraint. The fresh required gates above replace them as accepted offline evidence; no product repair or dependency installation was involved.

Another invalid harness receipt mixed root and child test trees, causing duplicate conftest registration before tests executed; its partial import-only31% coverage is not a product failure/gate result. Root selectors are now run separately. An initial asynchronously sampled mypy-before run overlapped later edits and is diagnostic-only; the accepted before/after comparison was sequential and source-hash verified. All corrections and raw receipts remain in the new evidence directory.

## Scope, preservation and cleanup

Owned source-tree paths are the two product files, the existing new `tests/test_api/test_chat_history_order_postgres.py` fixture portability changes, and this new doc. A reverse byte substitution of only the three added arguments reconstructs each current-root product baseline exactly; removing those AST arguments also reconstructs the full original AST. No imports/signatures/decorators/other helper or handler text changed. The frozen proof doc/report/test snapshots are not rewritten; the writable test's new-phase baseline and fixture-only delta are separate.

Positively leased databases used UTF8/template0/C on the existing localhost55439 PG14.22 service. Normal DROP only after zero sessions; catalog0/shared-health1, no force/reset. Request Sessions/listeners/overrides close, sync/async engines dispose, and current conftest SQLite files and phase-owned temporary/cache areas are cleaned by exact receipts. Earlier unknown tempfile attribution gaps remain untouched. No root, CI, dependency, schema outside owned test databases, provider, Git/Actions or publication writes.

Root owns final SOURCE approval, narrow integration, CI enrollment and final affected/aggregate gates. This slice ends at the frozen candidate handoff.
