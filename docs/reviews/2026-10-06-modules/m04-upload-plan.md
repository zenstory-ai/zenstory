# M04 bounded multipart upload repair plan

Review the existing material and bulk-draft upload handlers without changing
encoding, caps, chapter splitting, response shape, strict initial versions or
post-commit indexing. Multipart input bypasses JSON DTO NUL validation; draft
title-derived and appended orders can exceed PostgreSQL INTEGER. These are
candidates until a real UTF-8 PostgreSQL regression proves the failure.

1. Lock material validation rejection and draft per-input partial success with
   real PostgreSQL tests, including invalid later chapters, exact INTEGER upper
   boundary and valid neighbours. Invalid input must stage no partial chapters.
2. Reuse the existing text validator; validate all chapters of one input before
   adding that input's rows. Preserve strict baseline failure as a whole-request
   rollback, not a per-input validation error. Do not globally change title
   inference or clamp/reorder a user's chapters.
3. Review the current sibling-order query and per-row refresh/async ORM costs.
   Measure before optimizing; retain transaction ownership and production
   expiration semantics. Get separate read-only architecture advice before edits.
4. Run affected upload/creation/canonical tests, actual PostgreSQL cases, scoped
   coverage/lint/type comparisons and review. No installs, provider calls,
   production mutations, Actions runs, commits or pushes in this slice.

This does not close M04, all modules, or the release gate.
