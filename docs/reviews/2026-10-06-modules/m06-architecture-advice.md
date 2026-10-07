# M06 bounded architecture handoff

Independent architect read-only advice, 2026-10-06. Four initial local designs
approved; the then-required actual PostgreSQL RED→GREEN and constructor protocol
repair now has a bounded code-review **APPROVE** (`m06-snapshot-protocol.md`). This
does not close complete M06 or the wider module goal. No source/env/lock/remote
changes by this architect lane. Existing all-leaf code-review facts are in
`m06-review.md`.

## Timestamp / transaction boundary

- Existing metadata/undelete, content restore and extra-file soft-delete rows must
  use existing `advance_timestamp(previous, now=now)`. Do not invent a schema
  counter. Restored physically missing rows have no prior timestamp in snapshot
  format and cannot promise monotonicity relative to a hard-deleted historical row.
- Preserve one transaction for safety snapshot, all file mutations and restore
  versions. Current second-file injected failure regression already tests this.
- The original Project FOR UPDATE proposal is superseded by the architect's
  writer-first FK lock-cycle refinement: rollback takes Project FOR NO KEY
  UPDATE, then refreshed File FOR NO KEY UPDATE in ID order **before safety**;
  existing-project constructors and standalone snapshots take FOR SHARE before
  fresh validation/gather. Ordinary one-file writers retain their File-first
  lock, whose version FK KEY SHARE remains compatible with the rollback Project
  lock. Do not serialize unrelated scoped edits or compatible creators. Actual
  writer-first, stale-writer, constructors, duplicate recovery and scoped-writer
  evidence is in `m06-snapshot-protocol.md`; wider M06 approval remains open.
- SQLite file stripes are process-local only; no cross-process project-safe claim.
- Real PG tests: rollback-held Project lock blocks create until commit, after
  which new file remains active; rollback-held File locks make a stale-token writer
  reject 409 after commit; safety snapshot and restoration use fresh ORM rows.
  Root must inventory all insert paths before calling this a protocol repair.

## Structural creation baseline

Proven missing baseline paths: Web ordinary create, material/draft upload, material
import, inspiration copy. Agent/Tool have existing quota/best-effort variants.
Recommend non-folder, nonempty initial content gets a structural v1 in the same
transaction: add/flush File; create_version(change_type=create,
change_source=system, force_base=True, skip_quota=True, commit=False); outer commit.
Existing snapshot backfill is the system/quota-free precedent. Unexpected v1
failure rolls back both; no savepoint needed for a strict structural invariant.
Empty streaming files keep first-content-write behavior. New inspiration project
is invisible until its original transaction commits, so no additional lock on it.

This is a product-contract choice: quota-metered user v1 may be omitted when quota
is full, incompatible with always restoring creation content. Do not accidentally
convert explicitly best-effort subsequent history saves into strict writes, or
delete public arbitrary-content version creation from a baseline-only inference.
Regression matrix must cover all create paths, exactly one baseline/content,
failure atomicity and quota semantics before removal of Editor repair.

## Cleanup, without enabling retention

- Prefer removal of dead `cleanup_old_versions`: a correct compactor needs locked
  reconstruction before delete, rebasing surviving descendants and preserving
  every version referenced in snapshot JSON. It is not a small patch. Verify no
  production callers before removal and preserve useful replay regression coverage.
- Keep snapshot cleanup, but within one transaction bulk detach nullable
  FileVersion.snapshot_id for deleted snapshot IDs, delete snapshots, commit once;
  rollback on failure. No migration or new relationship layer required to fix FK
  deletion. Single-value link still does not represent many referencing snapshots.
- SQLite FK-enabled and real PG regressions: deleted Snapshot, surviving Version,
  null link, unchanged replay; injected failure restores both detach and delete.
  No new retention job or actual production cleanup authorized/performed.
