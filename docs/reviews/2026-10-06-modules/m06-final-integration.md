# M06 final all-leaf source integration

Independent native architect verdict: **APPROVE / Architectural Status: CLEAR**.
No remaining HIGH/MEDIUM source finding in current versions/snapshots/diff/
rollback inventory. This closes module source review, not all23, aggregate
quality gates, CI, merge or release.

## Architecture / performance / bug inventory

P = proven repair; A = reviewed accepted contract; B = nonblocking watch/gap.
Exact experiments and historical candidate accounting remain in linked reports.

| Leaf | Disposition and evidence |
| --- | --- |
| Snapshot transaction/timestamps/topology | P: safety snapshot, restore and histories retain one commit/rollback, fresh Project/File locks and independently monotone stamps. [Protocol](m06-snapshot-protocol.md); `snapshot_service.py:214-263`, `file_tree_rules.py:58-73` |
| Hard-missing hierarchy | P: materialize scoped rows, conditional flush, then assign parents from loaded/recreated same-project map; no new parent policy/intermediate commit. [Hierarchy](m06-hierarchy-restore-plan.md); `snapshot_service.py:735-858` |
| Creation baseline/history failures | P/A: server-owned strict quota-free populated structural baselines; quota-aware Agent API and explicitly best-effort FileCRUD AI remain distinct. Legacy empty history supported. [Baselines](m06-creation-baselines.md), [contracts](m06-creation-comparison-review.md) |
| Public checkpoint API | A/P: supplied checkpoint content remains intentional, server enforces user provenance/quota; removed Editor mutation-on-read GET→POST bootstrap. [Contracts](m06-creation-comparison-review.md); `api/versions.py:219-297`, `Editor.tsx:167-236` |
| Immutable Chat Undo | P: exact reconstructed before anchor, own token captured under lock before commit, fresh conditional restore, malformed cards fail closed. No click-time latest fallback. [Undo](m06-immutable-undo-plan.md) |
| Delta/FK cleanup | P/A: unused unsafe chain deletion removed; snapshot deletion atomically detaches nullable links. Reverse snapshot link stays optional/non-authoritative, no retention/migration activation. [Cleanup](m06-ux-cleanup-repair.md) |
| Replay/query/comparison/404 | P: loaded-target replay, 200-file chunks without cap, target bounds, bulk content reuse, reused compare rows and typed missing-version404. [Paging](m06-snapshot-query-paging.md), [comparison](m06-creation-comparison-review.md) |
| History UI/snapshot paging | P: reachable pages, preview/omission/quota feedback, stable tied sort, raw offsets, dedupe/retry/context fences and submitted-action close guards. [UX](m06-ux-cleanup-repair.md), [paging](m06-snapshot-query-paging.md) |
| Historical comparison | P: historical metadata rendered without live File GETs and stale-result fences. [UX](m06-ux-cleanup-repair.md); `SnapshotComparisonDialog.tsx:36-65,180-299` |
| Diff performance/layout | P/B: target SQL/matcher reuse, inline Hide unchanged and whitespace/newlines fixed. Full default5k-line DOM cost remains; no virtualization/speedup claim. [Comparison](m06-creation-comparison-review.md) |
| Automatic snapshot trigger | P/B: committed-mutation boolean survives parallel/truncation/history persistence; Web requires literal true/nonpartial/project. Later POST captures then-current project, not immutable AI-turn write set. [Trigger](m06-snapshot-trigger-inventory.md) |
| Locale identity races | P: translation refs decouple presentation from pending history rollback and dirty Editor load identity; true context/unmount fences retained. [Repairs](m06-hierarchy-restore-plan.md) |
| Other unchanged surfaces | A/B: naming/schema/client surfaces and arbitrary snapshot type kept after caller/security inventory; foreign-resource response inconsistency is not demonstrated auth bypass. [Original review](m06-review.md) |

## Latest exact validation

- Hierarchy valid SQLite RED8fail/2compat →10pass; affected six-file **76pass/
  6optional skips**, SnapshotService **95.07%** > unchanged80. RealPG4 RED;
  corrected UTF8 isolated final **14pass/10deselect**: ten failed concurrency
  nodes plus four hierarchy cases. Earlier31-case candidate21pass/10fail
  inherited local template1 SQL_ASCII, NOT GREEN or remote-CI evidence.
  Seventeen unchanged passes not repeated; not a single31-green receipt.
- FileVersionHistory translator1 genuine RED/13pass → **14pass**; scoped
  S82.63/B72.72/F76.66/L85.54 > unchanged66/55/61/68.
- Editor translator1 genuine RED/21pass → affected **43pass**; scoped
  S71.55/B61.17/F62.85/L73.23 above unchanged gates. New parent fixture proves
  pre-debounce preservation; real SimpleEditor dependent tests also pass,
  not a newly claimed physical-browser autosave test.
- Fresh shared Web build `m06-integration-current-web-build.log`: Vite13.46s
  and org475×2/docs24. Fresh scoped types/lint/backend compile/diff pass.
- Prior unchanged receipts keep exact boundaries: mutation565/85.38% plus
  integrated2/PG2 separately and Web210; Undo229/80.95% plus separate PG race409;
  comparison100/93.63% plus reversed2 separately; protocol23+4PG and later27PG.
  These are not one aggregate run.

Evidence root `/private/tmp/zenstory-module-audit-evidence-20261006`. Owned test
DBs removed, shared cluster preserved. No remote Actions/push/PR/merge/deploy,
provider configuration, schema or retention changes.

## Accepted boundaries / remaining aggregate gates

- No SQLite cross-process or all-file point-in-time snapshot guarantee.
- Offset pages are not immutable under external concurrent inserts.
- Browser POST is best-effort then-current project state, not per-turn writes.
- Full5k-line default diff ~25,035 DOM nodes/~260.9ms remains a WATCH.
- Rare broad rollback ValueError can label concurrent File deletion as
  VERSION_NOT_FOUND but stays404/no write. Future throwing async parent callback
  remains LOW; current consumer is synchronous/nonthrowing. Comparison locale
  change may refetch read-only data, also LOW.
- Whole-backend mypy retains documented SQLModel gap, not GREEN. Local PG14/
  Node25 differ from CI PG15/Node20. Opt-in history browser E2E and final all23
  aggregate gates remain open.
- Deferred main-CI/E2E audit remains queued after promised review/repairs/delivery;
  no remote runs/logs/artifacts inspected for it.

Next: M04 tree writers, including reused screenplay promotion/order freshness and
monotone timestamps. Preserve subset-reorder compatibility; concurrent-cycle
findings require real PG regression evidence.
