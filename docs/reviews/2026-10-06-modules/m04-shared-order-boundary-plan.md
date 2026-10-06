# M04/M07/M14 follow-up: shared persisted-order boundary

Static follow-up from the proven multipart INTEGER defect, **not yet a fresh
cross-writer RED**. `services/file_tree_rules.resolve_new_file_order` returns
title/metadata sequence or sibling max+1 without checking INTEGER storage bounds.
Web/Agent DTOs bound the supplied order, but a derived title can bypass that bound.
Tool creation and Web/Agent/tool update/move resolve persisted order through the
shared title utility. Web DTOs also permit negative legacy order values without a
storage lower bound; do not silently replace this with Agent's nonnegative policy.

Before declaring full M04/writer closure, map and prove the actual entrypoints on
an owned UTF-8 PostgreSQL fixture: oversized inferred title/metadata, unnumbered
append at MAX, supplied/resolved values below INTEGER MIN, and exact boundaries.
Lock valid explicit ordering/title inference, legacy negative values, mixed-type
sibling domain, project scope, unchanged content/token/history on rejection and
no scheduled indexing. Distinguish request-validation errors from unexpected
persistence faults and tool error contracts.

Get separate minimum-boundary advice after valid RED. Prefer existing persisted
order utilities/caller validation; no clamping, silent reordering, new schema,
global display-sort limit or speculative namespace/unique-order policy. Pure
validation must precede row mutation/staging; preserve caller transaction and
structural history semantics. Keep the already approved upload behavior intact.

This follow-up remains inside the current full-module task, before publication;
it is not the deferred remote main CI investigation. No new source edit/test or
cross-writer bug/coverage claim is made by this plan alone.

## Fresh regression evidence / bounded repair proposal

The same native backend lane has now exercised the actual writers with an owned
UTF-8 PostgreSQL14 database and ordinary expiring Sessions: **23 genuine
SQLSTATE22003 failures and 38 passing controls**, 61 final cases across the main
58-case run and three added move/reorder controls. This is not a single final
61-case GREEN run. Source hashes stayed identical. Rejected attempts rolled back
persisted content, parents, metadata, tokens and history, with no indexing,
activation or cache effects; the remaining defect is unexpected storage failure
instead of the caller's input-domain error. Database cleanup: zero sessions,
catalog count zero, shared health one. Raw receipts live under
`parallel-native-tmux/delete/order-boundary/` in the audit evidence directory.

Proposal, subject to independent boundary advice before product edits:

- Bound only the **resolved persisted** order to signed32. Keep display sort
  inference, exact MIN/MAX, Web/tool legacy negative values and Agent DTO's
  nonnegative contract. Do not clamp or reject an otherwise valid final title
  override merely because its preliminary append value overflowed.
- Reuse the existing persistence resolver and new-file allocator. Tool creation
  may delete duplicated allocation logic only after its existing inference,
  normalization and screenplay reuse controls stay GREEN.
- Resolve prospective update/move/reorder values before attached-row field or
  token mutations. Precompute the whole reorder batch before changing any row.
  Map only resolver input errors to existing API validation400; tools retain
  ValueError. Do not conceal history/ORM/commit failures as validation.
- Add in-session atomicity/error-mapping controls before changing source;
  persisted rollback equality alone does not prove mutation-free validation.
  Preserve locks, caller-owned transactions, strict initial versions and upload
  per-input versus whole-request failure boundaries.
- Run the affected PG matrix, pure title/order and writer/history tests, scoped
  coverage and static checks locally. Wire the new PG test into the existing
  serialized CI lane via a local contract test; do not run remote Actions.
