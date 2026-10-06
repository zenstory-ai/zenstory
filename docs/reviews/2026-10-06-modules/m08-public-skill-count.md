# M08 C7 public-skill collection count

Attributed to the standalone native backend lane, 2026-10-06. Candidate for coordinator source review; this does not close M08.

Actual PostgreSQL transactions reproduced six counter failures: distinct adds, removes, batch deletes, add/remove, add/batch-delete, and overlapping same-actor batch deletion. The baseline had six passing controls. Current semantics count inactive collection links too; re-enable/disable do not change the count.

The three count writers now use SQL arithmetic within their existing link transactions. Add flushes the unique link before incrementing, retaining the existing duplicate IntegrityError recovery. Individual and batch removal decrement only for DELETE RETURNING rows. Batch explicitly returns primary/public IDs and selects the public ID by name, avoiding positional ambiguity with ORM delete synchronization. Synchronization preserves deleted-link identity-map behavior. Batch counter updates use sorted public IDs. Existing DTOs, ownership filters, re-enable behavior, commits and unrelated bodies remain unchanged. No schema or service layer was added.

Fresh final validation: 12 real-PG cases PASS; 80 affected HTTP cases PASS, with 12 optional PG cases skipped in that SQLite batch; actual two-module coverage 435/471 = 92.36%, unchanged 80 gate. Ruff and in-memory compilation PASS. Scoped mypy remains non-GREEN: 30 baseline diagnostics → 29, zero added.

The new PG module uses default-expiring cold Sessions, actual registered-handler methods, real link uniqueness/FKs, read barriers and observed pg_blocking_pids transaction waits. It checks distinct/mixed writers, duplicate/idempotent operations, inactive/foreign/nonnegative controls and rollback after an injected precommit failure. It does not claim HTTP concurrency, universal deadlock freedom or provider behavior. Existing HTTP tests exercise registered routes.

Evidence: `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/m08-skill-count-repair/REPORT.md`, phase-only.patch, commands/XML, SQL/source/PID receipts and cleanup. All eight positively owned DB leases are absent, with zero-session normal-drop receipts and shared health 1. Earlier frozen R7 paths and 1760 pre-existing unowned child files remain unchanged. Coordinator owns review, integration and PG CI enrollment.
