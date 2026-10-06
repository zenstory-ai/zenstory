# M16 billing admin pending-close parity

Four header X buttons bypassed their already-disabled footer Cancel/Save controls:
plan edit, subscription modify, single-code create, batch-code create.
Actual-page DOM regressions first recorded **4genuineRED/23PASS**. Minimal repair
adds each existing mutation's isPending disabled state and translated close name.
Fresh **27PASS**. No server cancellation, mutation/cache/notification policy or
new generic lifetime layer. Evidence: `m16-modal-lock/`; remaining M16 review and
native timestamp/payment-details ownership repair are separate, not moduleCLEAR.
