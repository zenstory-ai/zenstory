# M04 export leaf review

Current authoritative source, not the child worktree's older source:
`apps/server/api/export.py`, `services/features/export_service.py`,
`apps/web/src/hooks/useExport.ts`, `lib/api.ts` and Header export actions.
This closes a bounded read/test review, not fullM04/all23 or release approval.

## Architecture — keep

One sync endpoint owns authorization, plan-format eligibility, TXT/BOM and
Content-Disposition; one service owns scoped file selection and shared
sequence-aware sorting. The compatibility export facade is still used; no new
layer needed. The service's old private Chinese/chapter parser is not called by
runtime sorting but is exercised by existing compatibility tests. It has no
per-request cost; optional removal would not improve this task's actual export
path, so do not broaden the diff just to delete a tested helper.

The only production `useExport` caller is Header; it consumes `exportDrafts`,
not the hook's loading/error state. Completed downloads remain bound to the
requested project; do not invent cancellation merely because the user changes
project. The hook's older error-timer/local-state behavior is a reuse WATCH, not
a demonstrated live UI failure requiring a new lifecycle abstraction.

## Performance — preserve existing repair

The endpoint is already synchronous so FastAPI owns its worker-thread boundary;
the actual worker-identity regression remains GREEN. The service makes one
project-scoped active draft/script query, shared sort and linear content join.
No per-row relationship/content queries are added by the source. All content is
necessarily loaded to produce the complete saved-only TXT; no streaming,
pagination or unsaved-content promise is introduced. No production latency or
new optimization gain claimed by this inspection.

## Bugs/contracts — bounded GREEN

Current ownership/superuser, missing/deleted project, export-format restriction,
no-draft error, active draft/script inclusion, cross-project/deleted exclusion,
chapter/title/metadata order, Unicode/BOM/filename and empty-content contracts
are covered by the existing affected suites. Browser client retries a401 once
through the existing refresh flow, throws typed errors and downloads the blob;
Header keeps quota modal/toast behavior. No new defect is asserted here.

Fresh local command (Python3.12, SQLite test runtime, no providers):

```
python -m pytest -o addopts= tests/test_api/test_export.py \
  tests/test_api/test_export_worker.py \
  tests/test_services/test_export_service.py \
  tests/test_services/test_subscription_export_formats.py -q
```

**50 passed**, exit0, 12.45s. Receipt:
`m04-export-final-leaf-check.txt` in the audit evidence directory. No source edit,
so unchanged static/build/coverage gates were not repeated. Remaining broader
M04 acceptance includes shared order validation and the separately mapped async
upload synchronous-persistence performance boundary. No Actions/remote git or
provider mutation.
