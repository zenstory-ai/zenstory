# M05 Editor lifecycle/token slice — integrated APPROVE / CLEAR

The second actual native tmux Codex completed the bounded C1–C5 repair in its
isolated worktree. Independent architect approved its finalized source hashes;
root integrated **nine owned paths, zero conflicts**, using the seven-path
immutable phase baseline plus new test/review paths, not the aggregate HEAD diff.
Every integrated path is byte-identical to the verified lane source.

## Repairs and measured evidence

- Owned delayed refresh timers and load generations reject obsolete invocation,
  response and failed-switch completion across selection/actual unmount.
- Review completion checks live project, selection, loaded file and exact review
  object; the old server write can complete without changing replacement review.
- Creation checks project/mount after folder lookup and before shared completion;
  completed-operation success/error notifications remain visible.
- Saving → dirty → saved status prevents earlier success masking unsaved edits.
- Immutable dirty text retains its baseline token across unrelated same-file
  refresh. The child's serialized queue advances only from the exact token of
  its own successful PUT; the parent does not promote an arbitrary stale draft
  from its current File cache. Structured internal success carries the token;
  outward flush outcomes, history reconciliation and final dirty save persist.

Genuine untouched-source baseline: **48 RED / 24 controls**. Targeted repaired
fixture **72 GREEN**. Final six-suite **122 GREEN**; **fresh root integration run
also 122 GREEN** (`m05-root-final-integration.txt`). One intermediate batch
121pass/1 old mock lacking token is **not GREEN**. Invalid unsubscribed/memo,
nested-Strict diagnostics and type-wrapper resolution receipts are separately
classified in the lane report, not product REDs.

Exact-source scoped coverage: L80.26/B75.37/F79.16/S78.75, above unchanged
L68/B55/F61/S66 gates. Affected ESLint max-warnings0, forced typecheck with
evidence-only build-info, and one Vite/org/docs build pass. Root does not
pointlessly repeat these unchanged exact-source checks. Local fixture old refresh
GETs, stale shared setters, obsolete POSTs and wrong token promotion disappear;
these are mocked-API/state counts, not production latency/data-loss claims.

Evidence: `parallel-native-tmux/frontend/m05-repair/REPORT.md`, finalized manifest,
root-integration receipt and baseline-only delta under the common audit evidence
root. Owned renderer/tests/review paths and detailed plan are listed there.

## Open boundaries

This is not full M05/all23/release. The same native Codex now performs a separate
offline real-browser/current-textarea performance review using fake auth/API
fixtures and an owned local preview, blocking external network and providers.
Current C1–C5 product source is read-only in that phase.

Independent nonblocking WATCH: ordinary final dirty save after actual Editor
unmount may still invoke captured rename or 409 shared callbacks. First prove a
reachable responsive/project-boundary case and distinguish legitimate same-
project rename reconciliation from stale selection/review mutation. Do not
blanket-cancel the write or change policy from a source hypothesis alone.

Dirty refresh still displays new server text while retaining the dirty draft and
old token; no durable-draft or redesigned conflict UX is claimed. Physical IME,
mobile device, offline/close restore, saved-only export and large-document costs
remain explicit contracts/gaps, not hidden by old VirtualizedEditor tests.
Node25 local versus CI20 parity and final aggregate/quality/exact-SHA release
remain. Deferred main CI audit is queued, unstarted; no push/Actions/deploy.
