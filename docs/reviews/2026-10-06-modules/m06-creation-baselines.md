# M06 creation baseline repair — bounded slice

Historical slice receipt: the 248-case and two-service coverage below precede
the later snapshot-gate repair. That follow-up deliberately removes independent
canonical upload-folder commits on production creation paths, preserving default
standalone-helper compatibility; see `m06-snapshot-protocol.md` for current-source
atomicity/concurrency evidence. Do not treat this earlier slice as final aggregate
validation after those source changes.

## Inventory and contract boundaries

Read-only native `explore` inventory, 2026-10-06, classified production File
constructors, transaction boundaries and the Editor repair caller. The proven
missing paths are:

| Path | Existing transaction | Repair |
| --- | --- | --- |
| `api/files.py:create_file` | Single new File commit | Capture populated nonfolder v1 before that commit |
| `api/files.py:upload_material` | One content-batch commit, canonical folder helper separately commits | Capture each populated snippet before content-batch commit |
| `api/files.py:upload_drafts` | Per-input parsing/validation catch, one successful-content-batch commit, folder helper separately commits | Capture each populated draft outside parsing catch and before batch commit |
| `api/materials/import_.py:import_material` | Optional folder plus populated file, single commit | Include initial history in the existing folder/file transaction |
| `services/inspiration_service.py:copy_inspiration_to_project` | New Project plus copied files and parent mapping, optional caller-owned commit=False | Include initial history without adding a commit |

Project templates create root folders only. Agent API creates populated content
with user attribution and quota enforcement; Tool CRUD captures it with an AI
best-effort variant. Empty streaming files intentionally get no creation version
and use first-content-write behavior. Snapshot restoration recreates an empty row
then writes restored content with its existing strict quota-free restore version.
The snapshot baseline-repair bulk insertion remains separate.

The existing FileVersionService now owns one small `create_initial_version`
method for **newly added** populated nonfolder files: flush File, then delegate to
create_version(system/create/force_base/skip_quota/commit=False). The method does
not commit or swallow unexpected errors. Caller commit/rollback continues to own
File plus initial history. No new abstraction layer, dependency or schema.

Agent/Tool variants, later user/AI saves, public arbitrary-content version creation
and Editor's legacy fallback are intentionally unchanged. This slice therefore
does **not** claim a universal creation-baseline invariant or safe removal of the
Editor fallback. Existing legacy files and those quota variants still need a
separate reviewed contract. No retention or production-data changes.

## Local regression evidence

Evidence directory: `/private/tmp/zenstory-module-audit-evidence-20261006/`.

- `m06-web-upload-baseline-red.log`: before source changes, 16 new cases:
  **13 fail / 3 pass**. Missing initial versions and unreached failure injections
  reproduce ordinary Web creation, split material/draft uploads and quota gaps;
  empty/folder behavior was already correct.
- `m06-import-copy-baseline-red.log`: before source changes, **4 fail** against
  authentic seeded import/copy functions; absent v1 and unreached failure hooks.
- First combined 20-case post-edit candidate (`m06-creation-baseline-green.log`)
  is **not a GREEN receipt**: 11 pass / 9 fail due to test harness assumptions.
  LoggingMiddleware returns HTTP 500 rather than raising the injected exception.
  Restoring an instance-level method monkeypatch leaves a bound-method shadow,
  hiding subsequent class mocks. Tests now patch the class and assert actual HTTP
  500. Two exact keyword expectations also needed the optional initial summary.
  No product source failure is inferred from that candidate run.
- `m06-import-copy-baseline-green.log`: corrected isolated **4 pass**, including
  parent mapping, empty/folder exclusion, external invisibility with commit=False,
  and failure rollback of import folder/file or copied Project/files/copy_count.

Focused Web regressions additionally cover untouched initial content after a
non-Editor skip-version edit, zero user-version quota, no user-quota consumption,
each split child's v1, and second-child failure rolling back all content rows and
versions. Canonical upload-folder commits are deliberately not described as
content-batch atomicity.

## Final bounded verification

- `m06-creation-baseline-affected-green.log`: **248 pass / 9 files**, 124.95s.
  Includes all 20 new cases and existing Web CRUD, draft validation/splitting,
  material import, inspiration copy, reconstruction/quota and subscription-expiry
  transaction suites. Source unchanged during the run.
- First two-service scoped coverage run: **81 pass**, but **78.75%** combined
  coverage failed the unchanged 80% gate. New initial-version method has no
  missing lines; inspiration's new copy call executed. The gap included the
  existing user-quota branch, so only the relevant existing quota suite was
  appended to that coverage data; no source omission or threshold relaxation.
- `m06-creation-quota-coverage.log` / `m06-creation-services-coverage.json`:
  quota suite **7 pass**; combined coverage **81.20%**, unchanged 80% gate passes.
  FileVersionService **90.39%**; InspirationService **65.94%** (unreviewed upstream
  helpers remain uncovered, not hidden). The coverage is a combined two-run
  receipt, not one 88-case invocation or whole-server coverage.
- Scoped six-file Ruff, py_compile and aggregate `git diff --check`: pass.
- Independent native code-reviewer: **APPROVE**, zero issues; read the 248-case
  receipt and independently ran the 20 new cases. This is only creation-baseline
  approval, not complete M06/M04/M19 or aggregate release approval.

No frontend source change; previous unchanged app build/typed evidence is reused,
not rerun. Backend full typecheck remains the previously documented SQLModel
typing gap; no new full typecheck-GREEN or production-E2E claim. Snapshot
Project/File locking, immutable Undo, snapshot pagination/performance and the
other module leaves remain required before final aggregate checks and release.

No remote workflow inspection/trigger/rerun, push, merge, deployment or provider
charges. User's main CI/E2E investigation remains queued after current work and
promised delivery in `follow-up-tasks.md`.
