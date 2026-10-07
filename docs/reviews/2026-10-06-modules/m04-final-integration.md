# M04 files/tree/upload/export — final all-leaf disposition

**Independent all-leaf/source APPROVE / Architectural CLEAR** (m06_undo_design,
2026-10-06). No remaining mapped source blocker. Fresh integrated validation
below is established; this does not assert aggregate/type/CI/release approval.

## Leaf coverage and A/P/B results

| Leaf / current source | Architecture / performance / bugs disposition |
|---|---|
| Flat list and single File DTOs, `api/files.py:get_files/get_file` | KEEP direct scoped queries and required full bodies/raw metadata. Flat stored-order ASC/created DESC differs intentionally from tree/export. Full list has no active Web runtime caller found; no pagination/projection redesign. Missing/deleted/foreign/project filtering and JSON NUL controls in current `test_files.py` are included in fresh269. |
| Create/update/move/reorder across Web, Agent and tools | Existing Project→File locking/caller transaction utilities reused, no new global gate or schema. Bulk reorder50→1File SELECT in controlled fixture. Final signed32 validation covers explicit/title/metadata/append and prospective update/batch before requested mutation; 23 genuine PG range failures→final68PGGREEN. Display/sort remain unbounded; no clamp. See tree-writer and persisted-order reviews. |
| Canonical folder lookup/recovery and reused episode/script | Existing helpers keep scoped active identity, fresh flags/parents and monotone token. Whole operation rollback ownership preserved; repair may stage before later validation and caller must rollback. SQLite+actualPG canonical and reuse receipts in approved canonical review/current269. Phantom first-create uniqueness and theoretical duplicate-lock deadlock not promised/fabricated. |
| Delete/recursive descendants/index side effects | One scoped recursive CTE UNION distinct, iterative postorder and fresh descendant locks; legacy deep/cyclic deletes bounded. Production default-expiring postcommit descriptor capture avoids per-row fetches:1/50/1200 delete fixture constant3File SELECT/0postcommit. Nonrecursive Web child behavior preserved. Source-approved deletion reports, no global recursion policy. |
| GET tree/default SQL projection/content/order/historical topology | One scoped File SELECT, default omits SQL body and emits empty content, opt-in includes body; iterative exact JSON serializer replaces depth1200HTTP500 with200 without global recursion change. Cycle omission, owned orphan promotion and foreign/deleted parent isolation are characterized, not invented visibility. Root38GREEN plus exact-source146scoped83.35% in tree-response review, unaffected by later three order writers/two uploads. |
| Tree legacy metadata and ties | KEEP current absentNone/malformed{} and valid scalar/list read compatibility. Shared sort guards Mapping; current production tree callers do not dereference metadata as dict. TS record-or-null mismatch remains low WATCH, not proven runtime crash or permission to normalize stored legacy data. No implicit ID tie/order change without a contract. |
| Material/draft extension/byte/character/count/decode/chapter/split input | Existing limits/parser and established fallback/BOM/UTF16/GB18030 policies retained. Multipart NUL/finalorder constraints reuse existing validation; per-source draft errors remain partial while strict v1 failure rolls back all. Foreign sibling MAX scope corrected;500returned scalars→one SQL aggregate row. Parser/helper/encoding/request controls included269; whole-request25MiB cap belongs M23, no increase. |
| Upload baseline/history/commit/DTO/index/cache | Strict structural v1 for each createdFile, one commit, no optional quota bypass of baseline. Precommit immutable DTOs preserve default expiry and expected sideeffects;50draft postcommit51File/UserSELECT→0. Actual18PG upload matrix and material/draft rollback controls in fresh269. Material automatic-index policy and fault-injected partial assembly remain existing-contract WATCHs, no actual triggering input/provider outage established. |
| Upload event-loop boundary | Two endpoints become sync, use documented raw UploadFile.file for three bounded reads; cohesive ORM/history/parser/session work in one FastAPI worker invocation. Two actualHTTP thread REDs→GREEN with one persisted matching v1 each. Four direct-call adapters updated, no arbitrary executor/per-row Session sharing. Auth dependencies remain M01 ownership and generator phases may use different threads; no production latency/throughput promise. |
| Desktop/mobile real file panes/tree interactions | Existing actual renderer remains; project/unmount mutation and StrictMode draft fences repaired and reviewed, final150four-suite scoped gates. Measured actual1000node local RTL ~16k/15kDOM and2.4/1.97s withcoverage are synthetic observations, not production p95. Unused virtualization/drag helpers are not wired as unrequested features. Physical/verydeep UI remains explicit device gap. |
| Export route/service/worker and Web Header download | KEEP one active owned draft/script query/shared sort/linear join, saved-only TXT/BOM/RFC5987/plan semantics. Synchronous worker identity and auth/error/format/empty/type/order/content contracts **fresh50PASS**. Tested private parser is unused by runtime; no worthwhile dependency/behavior cleanup needed. Hook timer is local-state-only WATCH. |
| Agent list/get projections and CLI tree | Preserve already-approved scoped fields/body/privacy/paging/merge semantics. Web tree fix does not claim CLI tree/global depth guarantee; own M14/M22 review completes shared leaf closure. |
| Vector rebuild adjunct | Existing ownership/rate limit/project single-flight/own Session/finally-release source and test definitions mapped. No provider calls or new policy here; embedding/index lifecycle remains M11 module responsibility, not an unmapped M04 route. |

## Latest integration evidence

[m04-order-worker-integration.md](m04-order-worker-integration.md) records narrow
ninecode/test/CI path merge0conflicts plus finalizeddoc, exact child non-files
bytes and root sync-upload preservation. **Fresh root269PASS, scoped86.56%**
against unchanged80, affected Ruff0/local serialized-PG workflow contract9PASS.
All owned root/native PG databases cleaned0sessions/0catalog/sharedhealth1;
no forced termination/shared resets/providers/Actions/installs/schema changes.

Other independently reviewed source slices are anchored in:
`m04-tree-writer-review.md`, `m04-canonical-folder-review.md`,
`m04-delete-traversal-review.md`, `m04-native-tmux-integration.md`,
`m04-upload-review.md`, `m04-tree-response-integration.md`,
`m04-export-leaf-review.md`. Historical passes are not added together into an
invented fresh aggregate invocation. The read-only all-leaf map is evidence
`parallel-native-tmux/delete/remaining-leaves/REPORT.md`, not a test pass receipt.

## Boundary / reopen conditions

SQLite cross-process/multirow structural atomicity is not PG row-lock equivalence.
Local PG14/Node25 differ from CI15/Node20. Scoped/global mypy remain baseline
NOTGREEN. Authentication dependencies, vector provider behavior, billing plans,
Agent key/search API and CLI have their own module owners; shared fixes can reopen
this review. Physical IME/device/ultradeep tree/browser durable-offline state,
large-project export peak memory/p95 are not dynamically certified. These gaps
are not known failing inputs hidden as approvals. No dependency/framework rewrite
or feature was introduced to cure a speculative concern.

All23 review/fixes, aggregate local checks, necessary finalCI, one PR/merge and
exact-SHA authenticated production delivery remain required. Deferred main-CI
existing-runs/logs/artifacts task stays queued after current promised work/delivery.
