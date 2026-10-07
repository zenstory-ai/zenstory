# M09 — materials and ingestion module

Independent whole-module verdict: **MODULE SOURCE_APPROVE / CLEAR**. All 25 registered materials leaves, ingestion pipeline/task/subflow call paths and wired frontend were reviewed. This closes source review, not consolidated quality or deployment.

## Repairs and contracts

Monthly reservations and job creation share the caller-owned transaction. Refund settlement reuses durable charged-job state, atomic quota decrement and exact-snapshot compare-and-set; reconciliation retries failed charged jobs without decrementing a different month's quota. No ledger, scheduler or schema was added. Download repair uses an owned temporary file and replaces the destination only after a successful complete read; interrupted downloads do not leave a seemingly complete target.

Upload refresh belongs to the originating authenticated session; imports refresh the current project tree only after successful quick/full/partial changes. Late responses cannot refresh a replacement session or project. Existing progress, error and import behavior remain.

Chapter consumers use scalar metadata projections rather than incomplete ORM objects. Four metadata-only consumers no longer materialize measured 2/16 MiB bodies at 32/256 × 64 KiB. Meta extraction still reads its required 20 bodies (1.25 MiB); it is not a zero-body claim. SQL ordering, project ownership, stage policy and query counts remain unchanged. Existing helpers and pipeline architecture were retained.

## Evidence

Under `/private/tmp/zenstory-module-audit-evidence-20261006`: interrupted-download root integration 8 PASS; upload/import exact seven-path integration 99 PASS; projection exact eight-path integration zero conflicts. Final fresh projection/related batch **92 PASS /35 deselected, combined scoped coverage 80.89% >= unchanged 80** after adding genuine empty/meta persistence compatibility cases. Earlier 88-pass receipts failed coverage (52.90% invalid late instrumentation, then 74.52% genuine shortfall); neither is green. Existing stage assertions were preserved. Native scoped Ruff/compile pass; mypy 69→67 legacy diagnostics, zero added, **not global green**.

## Limits

Batch-future/task timestamp/late-worker and unmeasured production-scale concerns remain documented watches, not new queue or retry policies. Actual financial/PostgreSQL receipts remain separate. Final default/global coverage, image, necessary E2E, CI and exact-SHA release gates remain.
