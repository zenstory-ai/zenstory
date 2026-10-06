# Follow-up tasks

## Recent main CI/E2E failures — queued, not started

User requested this on 2026-10-06, explicitly **after the current module review,
repairs and already-promised delivery**. Do not interrupt current work or assume
that a PR/main discrepancy has a particular cause.

- Read existing GitHub workflow runs, failed logs and artifacts first.
- Correlate runs with source commits and failed steps; distinguish still-current
  defects, historically fixed failures, cancellations and expired runs/artifacts.
- Identify why PR checks pass while relevant main checks fail from evidence.
- Reproduce under CI conditions locally where possible; fix root causes and run
  focused verification before consolidated pushes.
- Preserve all quality gates. No repeated pushes, blind reruns or Actions-as-debug
  environment; CI-only hypotheses get the minimum justified remote verification.
- Final report: relevant runs/commits, root causes, changes, local verification and
  actual final main check results. Current status: queued; no workflow inspected,
  triggered or rerun for this follow-up task.
