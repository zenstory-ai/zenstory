# M04 canonical-folder recovery — bounded regression-first plan

Root owns this slice in the coordinator audit worktree; the explicitly requested
native tmux lanes separately own recursive deletion and production tree UI.
All source remains uncommitted. Starting snapshot retained for narrow three-way
integration; no Team mode, Actions runs, dependency/provider/production changes.

Evidence from current source: material/draft recovery assigns raw wall clocks;
script fallback only clears `is_deleted`, leaving `deleted_at` and token stale.
Tool canonical recovery similarly uses raw clock and repairs without a fresh row
lock. These are source observations until the regressions below execute.

Independent architect has approved the minimal boundary: creators retain Project
SHARE; existing-row recovery alone uses File NO KEY UPDATE + populate_existing,
revalidates project/type/state after waiting, and advances the freshly loaded
row's token with `advance_timestamp`. All undeletes clear `deleted_at`; tool root
repair retains canonical parent normalization. No SHARE-to-exclusive upgrade,
new lock subsystem, schema changes or project-wide ordinary-content serialization.

Web `commit=False` keeps caller ownership (flush only, no commit/rollback); strict
child/baseline failure must roll the folder recovery back together. Existing valid
live folder no-op remains unlocked and token-preserving. Standalone `commit=True`
owns its transaction on mutation/missing-row paths: take Project SHARE before
File lock/insert; commit even if a locked reread finds another recovery finished,
so locks are released. Caller must not stage unrelated writes in helper-owned mode.
Unique-conflict reread validates/rechecks and fresh-locks before any needed repair.

Validation BEFORE edits: real SQLite past/equal/future clock matrices for Web
material/draft/script and tool canonical roots; deleted flags/body/title/order,
parent recovery, no-op token preservation, caller rollback and strict upload
baseline fault. Actual isolated UTF8 PostgreSQL proves same-root waiter freshness,
no double bump, unrelated content progress, same-row content freshness, helper-owned
commit lock release and caller-owned rollback. Observe real PG lock waits with
strong ORM references, not sleep-only assertions. Run focused compat/coverage,
Ruff/compile/diff and independent final review. Local PG14/CI15 remains a gap.
Full M04/all23/aggregate and release remain open; deferred main-CI audit stays queued.
