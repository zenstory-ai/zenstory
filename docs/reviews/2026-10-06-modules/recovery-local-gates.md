# Fresh local recovery gates — 2026-10-07

Source checkpoint: `36b4fc5`, preserving audit checkpoint `2e5da1f` and
upstream `45112ce`. All 23 module source dispositions and the recovered final
product repairs are source-approved; this is not release approval.

## Fresh, completed evidence

- Full Web: 247 files; 3,640 passed, one unchanged SSR skip, zero failed.
  Coverage: lines 81.44%, branches 72.34%, functions 75.63%, statements
  78.66%; unchanged thresholds 68/55/61/66. The real 1,000-file responsive
  tree suite retains its original scale and ten-second timeout.
- Related Web fixtures: 102 passed. Source and affected-unit type checks,
  Web lint, CSS tokens, i18n checks and Vite/org/docs builds pass.
- Independent Node 20 Vercel producer graph: actual npm ci, npm audit with
  the existing high-severity gate, tsc -b and build:vercel all exit zero.
  Generated domain layout reports 977 organization URLs and two app URLs,
  without root filesystem shadows. One low-severity npm advisory is retained;
  no forced upgrade or lockfile change was made.
- Node 20 site generator tests: 56 passed; all three actual generators have
  positive LCOV source/line evidence.
- Backend suggest target: 64 passed; admin PostgreSQL target: three passed;
  exact 26-module serial PostgreSQL gate: 304 passed without skips.
- Corrected material/upload target: 107 passed. The original aggregate
  stopped with 234 passed, 68 skipped and one failure because the evidence
  harness used UPLOAD_FOLDER instead of MATERIAL_UPLOAD_FOLDER. The guard
  correctly prevented a worktree write. That failure is preserved separately;
  only the evidence env key was corrected, with no product/test/gate change.
- CLI under actual Node 20: 112 passed; types/build/help/release controls and
  non-publishing metadata pass. The final dry package contains 13 entries,
  including the real LICENSE; nothing was published.
- CI control tests: 29 passed; actionlint and the actual .yamllint config pass.
- Actual unchanged production Dockerfiles build successfully. API image
  imports and awaits real init_db using its owned SQLite database, then
  disposes/removes it. Worker image parses all 356 current non-test Python
  files under actual Python 3.11.17; all source hashes match.
- Real auth setup plus the material-library lifecycle prerequisite: two passed.
- Actual pinned production worker: all 183 flow tests pass without skips,
  using real conftest, plugins and current source/test inputs under Python
  3.11.17, Prefect 3.6.28 and FastAPI 0.123.7. Source/env writes and external
  network attempts are zero; owned temporary DBs/container are cleaned normally.
- Actual private pinned Prefect API: process-pool creation, deploy --all and
  the unchanged exact deployment contract pass. The four registered names are
  chapter_extraction, novel_ingestion_v3, relationship_extraction and
  story_aggregation. Zero flow runs and no worker are launched. Normal owned
  shutdown completes. An initial read-only installed UI cache startup failure
  is preserved; only the officially supported UI static-cache location is
  redirected to evidence, keeping the real UI/API enabled.
- Full backend CI-scope Ruff and both actual production-image pip check pass.

## Test-adapter source approval

Ten test-only paths were independently reviewed against `36b4fc5`, with zero
findings. Test counts, skips, timeouts and assertion categories are retained;
M16 adds two assertions. Adjustments use the real HTTP Request/router,
structured quota and subscription types, exact localized formatting/current
clipboard key, and dataclass evidence serialization. No product bypass.

## Remaining gates

The corrected full backend default aggregate stopped with 2,879 passed,
243 skipped and one failure (exit two). A two-original-test causal reproduction
proves that an earlier snapshot test leaves a bound method on a shared singleton,
so a later class-level observer misses version staging. Real SQL and an
independent reader show content and version persist correctly in one transaction;
no product atomicity defect was observed. The minimum prior test fixture repair
patches the class descriptor instead of the singleton. The causal pair and
affected atomicity modules pass with unchanged assertions; independent source
review is clear with zero findings (seven tests and 30 assertions preserved).
One new complete original aggregate is approved; its result is not yet known. Default Chromium enrollment is 750 cases
across 52 files; enrollment is not a pass count. Default execution and the
four prescribed release specs are unfinished. QA-wide types currently report
TS1484 for the existing Page/Route imports in the material spec; a type-only
import correction is pending after its immutable execution stage. Fresh
pinned-worker flow tests and private Prefect deployment registration have now
passed; their receipts are distinct from the pre-reboot historical proofs. Required CI, PR merge and exact merged-SHA production readback have
not occurred. Historical main CI investigation remains queued after delivery.

## Recovery and boundaries

Reboot erased temporary raw artifacts and killed prior processes, not the
committed source or surviving staged index. Fresh worktree/evidence are on
persistent disk, and both original Codex session IDs run in two standalone
native tmux sessions, not OMX Team. The protected original dirty checkout was
not overwritten. No remote push, workflow rerun/dispatch, production mutation,
payment, dependency upgrade or unrelated resource cleanup was performed.
