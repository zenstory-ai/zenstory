# M06 versions / snapshots / diff / rollback

Independent code-reviewer all-leaf read-only handoff, 2026-10-06.
Verdict: **REQUEST CHANGES**. No M06 source edits by reviewer. Root must lock
proven defects with checked-in regressions before changing source; proposed
contract/architecture changes are not automatically approved implementation.

## Coverage and existing evidence

- Seven web file-version routes, six snapshot routes, three Agent version routes.
- FileVersionService create/read/reconstruct/compare/rollback/quota/cleanup/diff;
  VersionService snapshot create/list/compare/rollback/cleanup/gather/link/restore.
- FileVersion / Snapshot models, Web/Agent/tool writers, Editor/SimpleEditor,
  Chat/tool-result/stream integrations, API DTOs/clients, all history/diff UIs.
- Targeted frontend history/comparison/panel: 25/25 pass, but encode old behavior.
- Scoped Ruff/diff pass. Reviewer mypy unavailable. Earlier 331 backend / actual
  PostgreSQL writer evidence reused, not repeated. Version-history browser suite
  is opt-in (`E2E_ENABLE_VERSION_HISTORY_E2E=true`), not claimed run.
- Local probes: delta cleanup changes replay from A/B2/C3 to A/B/C3; foreign-key
  enabled snapshot cleanup raises IntegrityError; snapshot rollback lowers a
  future timestamp; a 20-file snapshot performs 26 SQL statements. These probes
  still need durable root-owned regression tests before repair claims.

## Actionable findings / RED plans

| Priority | Leaf / evidence | Finding and smallest next proof |
| --- | --- | --- |
| HIGH | `services/features/snapshot_service.py` rollback | Raw shared `utcnow()` can regress each File.updated_at. Freeze/backdate clock and require restored/undeleted/recreated/deleted files to advance independently using existing helper. Project-wide concurrency is separate. |
| HIGH | `api/files.py` create; `Editor.tsx` baseline repair | Nonempty Web-created files lack v1 until Editor opens. POST then non-Editor edit must retain creation content as v1; make backend transaction sole baseline owner after checking all create paths. |
| HIGH | `ToolResultCard.tsx`, `ChatPanel.tsx` undo | Undo guesses current second-latest version from fileId, not the original edit. v1→AI v2→user v3 must not let an old v2 card overwrite v3 or infer a different target. Need immutable before-version and conditional write design. |
| HIGH / contract review | Web/tool/FileVersion rollback snapshot catches | Unexpected create_version failures are swallowed. Existing best-effort save behavior is explicit, so review quota vs unexpected failure contracts before narrowing catches; inject unexpected failures and preserve documented quota fallback. No unconditional strict-history claim. |
| MEDIUM | FileVersionService.cleanup_old_versions | Deletes intermediate delta nodes without rebasing descendants, corrupting reconstruction. No production caller found. Prefer removal if truly dead, or transactional compaction; three-version replay RED required. |
| MEDIUM | Snapshot cleanup + FileVersion.snapshot_id | Direct snapshot delete violates restrictive FK; one version FK cannot represent multiple referencing snapshots and is overwritten. No production reader found. Test FK cleanup; evaluate nullable detachment vs relationship removal/migration, not an unreviewed schema drop. |
| MEDIUM | SnapshotComparisonDialog / types | Ignores historical titles/types/folders/metadata_changes, renders undefined versions, fetches 1+N live files. Contract test one compare request/zero file GETs, removed title, folder version omission and rename display. |
| MEDIUM | Snapshot gather / useChatStreaming | Twenty-file snapshot 26 queries; read-only tools/AI messages also create full JSON snapshot. Measure relevant query path; actual-mutation trigger/dedup/retention proposal must preserve lifecycle contracts. |
| MEDIUM | FileVersionHistory / Chat undo | Discards rollback version_quota_exceeded / snapshot_created flags. Preserve content restore, show history omission explicitly with existing upgrade surface. |
| MEDIUM | History UIs / snapshots list route | Only first 50 entries accessible; snapshot limit/offset unbounded and ordering lacks ID tiebreak. Test bounded API and load-more; preserve endpoint compatibility. |
| MEDIUM | SimpleEditor / FileVersionHistory view | Always fetches content but SimpleEditor supplies no-op callback. Implement visible preview or hide nonfunctional action; surface errors. |
| MEDIUM | File history / snapshot compare effects | Earlier pending request can overwrite new IDs. Out-of-order A/B regression, generation or supported AbortSignal fencing. |
| Conditional performance | DiffViewer / backend diff | Full unchanged-line DOM and repeated diff representations can amplify large files. Need representative measurement/contract proof before limits/virtualization/response changes. |
| Contract concern | Public version create | Arbitrary history content can differ from live file. Do not remove public mutation solely from an inferred baseline-only use; inventory callers, lock/precondition/equality semantics first. |
| LOW | Web rollback errors | Missing target version 400 differs from detail/content 404; establish consistent typed not-found regression. |
| LOW | Snapshot naming/schema/client dead surfaces | VersionService is snapshots, format docs v2 vs writer v3, unused client wrappers. Deletion only after caller inventory; no naming refactor required to fix bugs. |

## Additional architecture / security boundaries

- Project/file authorization exists across Web and Agent routes; comparison checks
  ownership of both snapshots; public create fixes `change_source=user`; no
  evident React XSS path. Foreign existing-resource 403 vs absent 404 is a privacy
  consistency concern, not a proven authorization bypass. snapshot_type remains
  arbitrary text; enum restriction requires compatibility review.
- Snapshot rollback has no proven project-wide locking/barrier contract yet.
  Reproduce actual PostgreSQL two-file interleavings before changing lock topology.
- Missing parent/child recreation iterates an unordered set; test ordered FK-safe
  restore before adopting two-pass insertion/parent assignment.
- Aggregate approval, module repair completion, full local release gates and
  exact-SHA production deployment remain pending. No Actions/push/rerun used.
