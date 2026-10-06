# M04 tree HTTP serialization repair

## Plan recorded before edits

Scope: only `api/files.py` serialization imports, one private helper and the final `get_file_tree` return; the existing query/build/sort code is preserved. Tests are confined to `tests/test_api/test_file_tree_response.py`. Evidence is under `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/tree-repair`. Preserve canonical/delete/upload fixes, including root's final upload DTO/performance code. No dependencies, global recursion changes, streaming, new depth/visibility policy, provider/PG/remote operations or nested agents.

1. Remove both strict known-failure marks and reproduce the actual server RED against the supplied phase baseline before product edits. Keep middleware exception observation separate from client decoding.
2. Add shallow exact-byte JSON parity/headers/escaping/metadata tests and retain NaN rejection. Keep all previous 29 response/query/legacy cases.
3. Implement a private explicit iterator-stack serializer: dump only each node's shallow fields with Starlette-equivalent stdlib JSON options; append child arrays with iterator frames and sibling commas; fully join before a base application/json Response. Do not dump a nested node/root list or invoke recursive FastAPI encoding.
4. Only after each ASGI response arrives, temporarily accommodate the test client's JSON decoder depth in try/finally, restoring the exact prior recursion limit. Prove original recursion setting is unchanged during requests and assert iterative IDs/count/body checks.
5. Verify focused HTTP cases plus existing file/tree API tests, real scoped `api.files` coverage with unchanged 80 threshold, Ruff, AST/text preservation and targeted mypy comparison to baseline. Do not rerun unmodified upload PG gates. Record source-relative delta, commands/exits/cleanup/hashes, valid RED vs old invalid diagnostic harness vs GREEN, remaining limits and coordinator handoff.

Design advice was APPROVE/CLEAR; source approval, root integration and aggregate/full-M04/all23/release remain coordinator-owned.

## Result and evidence

Legal 1200-folder HTTP trees now return 200 in both content modes at the original process recursion limit of 1000. Before the change, both returned 500 with a genuine `RecursionError` in FastAPI's recursive `jsonable_encoder`; the fresh baseline run had 27 passes and those two failures, with xfail marks removed. The test client's higher decoder limit is applied only after ASGI returns, then restored in `finally`. Node counting and traversal remain iterative.

`_serialize_file_tree_json` uses an iterator stack to emit the existing sorted node shape. It dumps shallow node fields with stdlib JSON options matching the previous response, appends child arrays with explicit frames, and joins the complete string before constructing a base application/json Response. This bypasses FastAPI's recursive response encoder without streaming or changing query/build/sort logic. Shallow metadata still uses stdlib JSON encoding; no universal metadata/client/UI-depth claim is made. Memory remains proportional to the tree and complete output; latency was not benchmarked.

Validation:

- Focused HTTP suite: **38 passed**, no skips/xfails. The prior 29 cases remain, plus eight exact raw-JSON/header parity cases and one NaN-rejection case. The parity/rejection cases also passed before product edits.
- Existing file/tree API coverage batch: **146 passed**, real `--cov=api.files --cov-fail-under=80`; **83.35% (656/787 statements)**. New helper: **100% (19/19 measured statements)**. No branch-coverage or whole-backend claim.
- One File SELECT per authorized request remains unchanged, including depth1200. Default/false SQL excludes content; true includes it. Foreign-project denial still occurs before any File SELECT. Cyclic component omission, orphan promotion, privacy, sorting/ties and metadata scalar/malformed behavior are preserved.
- Exact shallow output matches stdlib JSON for Chinese, quotes, backslashes, control characters, dictionary/list/scalar/null metadata and sibling/child commas. Empty output is exactly `{"tree":[]}`. Content-Type is exactly `application/json`; Content-Length matches UTF8 bytes. NaN metadata still yields ValueError/HTTP500, now during helper evaluation before Response construction.
- Ruff passes. AST/text comparison proves all 29 other existing functions unchanged, the tree function prefix byte-identical, and the entire material/draft upload suffix byte-identical. Only the private helper, relevant imports and terminal tree return change in product source.
- Targeted mypy retains the **same ten pre-existing diagnostics** across api/files.py, agent/tools/file_ops/crud.py and services/file_tree_rules.py; both baseline/current exit1. This is no new diagnostic, **not typecheck GREEN**.

Test-owned SQLite files were disposed/removed for every case; no shared PG or provider activity. No dependencies/config/other writers/global recursion settings changed. Root's independently validated final upload source is retained byte-for-byte; its unmodified PG/upload gates were not rerun. The local API coverage batch included ordinary existing upload tests only as part of the required affected-module coverage gate.

Full receipts, command exits, exact baseline-relative patch, scope proof and known gaps are in `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/tree-repair/REPORT.md`. The earlier probe's initial failure to observe a middleware-swallowed exception remains an invalid diagnostic-harness receipt, distinct from the fresh valid RED and current GREEN. No product source approval or full-M04 closure is claimed; source remains uncommitted for coordinator review/integration.
