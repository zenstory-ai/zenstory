# M15 user editor pending-close parity

UserManagement edit footer already disables Save/Cancel while its update mutation
is pending, but the header X bypassed the same lock. A regression on the actual
page/DOM, with its query/mutation boundary controlled, failed for pending close
and passed for idle close: baseline **1 genuine RED /12 PASS**.

Minimal repair adds the existing `updateMutation.isPending` disabled condition
and translated close accessible name to that header button. No mutation policy,
server cancellation, generic lifetime layer, toast or cache change. Fresh
**13 PASS**, actual imported page/test TypeScript and ESLint **0**.
Evidence: `m15-user-modal-lock/` under the audit evidence root. This proves DOM
pending parity, not a real-network cross-user race. Consolidated exact-head Web
build remains a release gate. Independent source review pending.
