# Final integration repairs and recovery

All 23 module source dispositions are committed in audit checkpoint `2e5da1f`.
Upstream `45112ce` (#143 metering, #144 admin/calendar/quota/redemption) was
merged through 10 backend and 17 frontend conflict resolutions, retaining both
branches' bounded projections, worker/session lifetimes and upstream contracts.

Minimum final product repairs: require HTTPS before browser SSO bearer transport;
validate OAuth URL ports and allow HTTP only for exact already-configured
localhost/127.0.0.1 hosts; use equivalent Python3.11-compatible ordinary tuple
alias syntax. No new URL framework, dependency or production interface.
Regression tests preserve auth, domain allowlists and session ownership.

A machine reboot erased temporary worktrees and raw gate artifacts. Recovery
uses the surviving resolved index tree `8a41a9bf` and rebuilt exact minimal source
repairs. Fresh regression observation reproduced four actual URL validator
failures and the production-worker grammar failure before repair. Historical
source approvals/counts are not substitutes for new unfinished aggregate gates.

Lost test adapters are restored only to match real current contracts: serialized
metering dataclass for evidence without changing raw assertions; real required
HTTP Request in concurrent admin audit tests; router/structured quota/clipboard/
result-dialog/date fixtures; typed paid material subscription/quota mocks.
Assertions, skips, timeouts and mandatory gates remain unchanged.

New worktree and evidence live in persistent `.omx/worktrees` and `.omx/artifacts`
under `/Users/pite/makemoney`, not `/tmp`. Two original native Codex session IDs
resume in actual standalone tmux. Full local quality and E2E, one batch PR,
required CI, merge and authenticated exact-SHA production readback are still
required. No release approval is asserted here. Follow-up historical main CI
investigation remains queued after delivery.
