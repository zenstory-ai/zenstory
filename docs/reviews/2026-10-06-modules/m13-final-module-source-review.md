# M13 — points, check-in and referral module

Independent whole-module verdict: **MODULE SOURCE_APPROVE / CLEAR**. Backend all-leaf approval and final wired-UI approval are retained separately; this is not aggregate/release approval.

## Repairs and preserved contracts

SQL-only points/referral/admin handlers now run as ordinary sync FastAPI worker handlers. Four no-await referral service helpers use sync calls at every actual caller; financial completion/auth composition remains async. Handler bodies, authentication, user locks, FIFO/expiry, atomic Beijing check-in, referral capacity/reservation and idempotent reward contracts remain unchanged.

PointsHistory, ReferralStats and InviteCodeCard reuse parseUTCDate for naive UTC timestamps, preserving offsets, display/status precedence and current financial policy. ReferralStats now distinguishes failed rewards queries from genuine empty results using the existing translation; loading has priority, cached nonempty records remain visible after background errors, and retry/cache/API behavior is unchanged. SettingsDialog and InviteCodeList wiring were reviewed.

## Validation

Evidence: m13-ledger-worker, m13-referral-worker, m13-dates-root-integration and m13-rewards-error-root-integration under /private/tmp/zenstory-module-audit-evidence-20261006. Worker checks: separate 86 PASS / 98.48% and 100 PASS / 82.73% scoped backend batches; interface guard 1 PASS; real PostgreSQL invite-capacity 1 PASS with owned database removed. Ruff 0; equal-context mypy 13→13 legacy diagnostics, **not** a green typecheck.

Dates: exact five-path root integration, fresh 27 PASS; native related 135 PASS/scoped 96.51L/94.62B/77.77F/93.61S and source/QA types/lint 0 retained. Rewards-error candidate: 2 genuine pre-fix failures/1 compatibility control; native 60 PASS, scoped 90.90L/86.20B/100F/90.90S, source/QA types/lint 0. Exact three-path root integration validates every baseline and candidate before writes, zero conflicts; fresh actual-root three-suite **60 PASS**. Intermediate timing-fixture failures are not product regressions. No redundant unchanged financial/PG reruns were needed for the UI branch.

## Accepted limits / final gates

Clipboard/share silent-failure UX, configured-vs-literal invite-cap defaults, equal-time unpaginated reward lists, and auth composition boundaries remain documented nonblocking watches. Expired wallet reads are already correct independent of unused maintenance markers. Physical-browser/global build and exact-head aggregate gates remain. No dependency, locale, schema, provider, Actions or production policy change.
