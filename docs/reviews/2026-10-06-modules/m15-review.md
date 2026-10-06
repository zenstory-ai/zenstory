# M15 admin shell, users, dashboard and audit

Mapped all nine registered handlers: users list/detail/update/soft-delete;
dashboard stats/activation/upgrade conversion/upgrade funnel; audit list. Traced
superuser auth, App/AdminRoute, layout/header/sidebar/mobile focus/inert, page
states, query keys, normalizers, StatsCard/RecentActivity and desktop/mobile lists.

## Repairs

- Dashboard circulation incorrectly summed unexpired-flag grants and all spends.
  Actual wallet/HTTP proof: **150 instead of50**, and **40 instead of80** after a
  spent lot expires and a new grant arrives. Extracted the existing canonical
  FIFO calculator unchanged; one six-column ordered scan groups by user and sums
  per-user available balances. One clock, `yield_per=256` execution option, cursor
  closed in finally, no commit/flush/expiry mutation. Empty/overspent/zero/expiry,
  projected SQL, one-query and cursor failure controls included. This is a
  correctness repair, not a claimed speedup: replay is O(ledger) and inherits
  FIFO scan costs/current-user lot memory.
- Audit offset pages sorted only nonunique timestamps. Actual three-page tie
  regression failed; `(created_at DESC,id DESC)` returns six IDs exactly once.
  This does not create snapshot paging during concurrent inserts.
- User editor header X now shares the existing pending Save/Cancel lock and has
  a translated accessible name. DOM baseline1RED/12controls; fresh13PASS.
- Desktop user/audit and recent-activity timestamps treated backend naive UTC as
  browser-local. Portable Asia/Shanghai actual UI proof:3RED/6 Z/offset controls.
  Reused existing `parseUTCDate`, without changing locale/calendar/DTO policy.
  Mobile UserCard had the same defect; corrected label-prefixed regression
  proved1naiveRED/2aware controls and the same parser repair is source-clear.

## Validation and retained architecture

Fresh current-root backend **114PASS /93.24%** over exactly points service,
dashboard and audit service; Ruff0. Mypy19baseline→19final/zero-added is **not**
globally green. Final fresh Web30 across timestamp/user/audit/dashboard sources include both
desktop/mobile timestamp assertions; source/QA types and ESLint0. Initial mobile
cardinality fixture failures are retained and not product RED/GREEN. Earlier
unchanged shell/guard10 controls reused, not a fresh global aggregate. Evidence:
`m15-points-circulation/`, `m15-user-modal-lock/`, `m15-web/`, `m15-timestamps/`.

Retain atomic user+audit writes, email/username409, last-admin row locks, count
and total list order, feature flags, paid-now/free exclusion, Zpay-only paid
attribution, batched audit actor lookup, strict audit page DTO and window-keyed
queries. No speculative framework/cache/transaction rewrite.

Independent architect approved the six initial deltas, the minimal mobile
parser delta and all nine leaves: **M15 MODULE SOURCE_APPROVE/CLEAR**.
Accepted WATCHs: new-users UTC day versus Beijing check-in day remains product
policy; ledger replay cost and concurrent insert offset drift are disclosed.
Exact-head App build/global quality/all23/CI/release remain required separately.
