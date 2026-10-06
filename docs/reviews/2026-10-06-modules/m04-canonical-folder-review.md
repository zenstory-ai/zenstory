# M04 canonical-folder recovery — bounded closure

Independent architect **APPROVE / Architectural CLEAR** for this source slice.
No full-M04/all23/release approval. Plan: `m04-canonical-folder-plan.md`.

## A / P / B result

- Architecture: Web material/draft/script fallback share a small recovery helper;
  retain existing creator Project SHARE and fresh File NO KEY UPDATE patterns.
  Lock only a row that needs recovery, Project before File, revalidate after wait.
  Tool uses its existing creator SHARE and fresh repair-row query. No upgrade,
  new lock layer, dependency, schema or global content-writer serialization.
- Bugs: equal/future clocks now strictly advance from the freshly locked token;
  script fallback clears `deleted_at` as well as `is_deleted`. Tool retains
  canonical parent recovery without replacing body/title/order. A second creator
  waits/rereads without double bump; concurrent same-row writer fields/tokens
  survive and unrelated same-project content continues.
- Transaction contracts: `commit=False` is caller-owned, flush-only; strict
  child/baseline failure rolls folder recovery and child back together.
  Helper-owned mutation/missing paths acquire SHARE before File lock/insert and
  commit before return, including a waiter finding a restored row or unique
  winner. The session may remain open without holding those locks. Valid live
  no-ops remain unlocked, uncommitted and token-preserving. Helper-owned callers
  must not stage unrelated pending writes; production uploads use caller ownership.
- Performance: no new lock/query cost on valid live no-op paths. No production
  latency or SQLite cross-process recovery guarantee is claimed.

## Exact evidence

Evidence root `/private/tmp/zenstory-module-audit-evidence-20261006`:

- `m04-canonical-folder-pre-red.txt`: first48-case candidate27fail/21pass =
  **26 genuine token/flag RED** and one INVALID material-test function name.
  A wrong-cwd wrapper then failed to apply that fixture correction and repeated
  the same candidate (`pre-red-corrected.txt`); it is not a corrected receipt.
  Actual corrected material strict-fault contract: `m04-canonical-folder-baseline-material-compat.txt`
  **1pass/47deselect**. No product change based on the invalid harness failure.
- `m04-canonical-folder-pg-pre-red.txt`: **15 genuine actual-PG RED**: six
  same-root lock bypasses, six fresh-token/flag failures after same-row writer,
  three helper-owned token/flag failures. No fixture failure in this receipt.
- `m04-canonical-folder-post-green.txt`: **63pass**, 48SQLite +15actualPG.
- Separate added ownership matrices: `m04-canonical-folder-pg-owned-extra.txt`
  **6pass/15deselect**; missing unique-collision helper-owned ordering/release:
  `m04-canonical-folder-pg-missing-collision.txt` **2pass/21deselect**.
- Fresh26-file integration `m04-canonical-delete-integrated-coverage.txt`:
  **571pass/3existing AgentAPI skips**, four-module real statement coverage
  **86.28%**, unchanged80. Includes all23 canonical-PG cases and the existing
  three-entrypoint `test_concurrent_canonical_folder_duplicate_preserves_creation_gate`
  required by the independent review, plus hierarchy/snapshot/tree compatibility.
  This receipt also includes the first recursive-delete integration; its
  nonexpiring fixture query budget is NOT production-shaped indexing evidence.
- Integrated Ruff seven paths, AST/compile seven paths and `git diff --check`
  pass. Backend global mypy baseline is not GREEN; no equivalence PG14→CI15.
- Existing serialized CI step includes the two new PG modules; local workflow
  required-wiring1RED→9GREEN (`m04-canonical-delete-ci-contract-green-corrected.txt`).
  First GREEN wrapper used the wrong cwd and did not run the contract; invalid,
  not a pass. No extra job, changed threshold, remote workflow or main-CI diagnosis.

Native-tmux delete/frontend lanes use isolated worktrees and local-first gates;
source integration is narrow three-way against saved starting source, not their
aggregate HEAD diffs. Parent canonical/other functions were AST-preserved during
recursive-delete integration. No source commit/push/PR/merge/deploy/Actions or
provider calls. Owned root PG DB was dropped after final integration: sessions0/catalog0/
sharedhealth1 (`m04-canonical-pg-cleanup.log`).

## Remaining

Full M04 tree/upload/export leaves and other23 modules/aggregate/release remain.
Independent deletion rereview found default `expire_on_commit=True` postcommit
per-File indexing reloads; descriptors-before-commit repair is now APPROVE/CLEAR and integrated; production-
like measured53→3, zero postcommit reads. Frontend final150tests are integrated
and independently APPROVE/CLEAR, including actual-unmount fence. Final current
backend591pass/3existing skips scoped86.73: `m04-native-tmux-integration.md`.
Main-CI run/log/artifact audit remains queued after current promised work/delivery.
