# Final cross-module policy parity — minimal repairs before release

All 23 leaf module dispositions exist, but final independent clean-context
architecture review found three policy omissions across execution surfaces.
Independent code/security review revalidated them and superseded its earlier
CLEAR with REQUEST CHANGES (one high, two medium). This is not release approval.

## Source gaps and minimum plan

- **HIGH, owned authenticated retries:** at the prior source epoch, agent
  stream/suggestions/steering and export/material-file/draft upload paths refresh ambient
  credentials after an old request gets 401. They can replay A's operation under
  replacement B or clear B. Canonical JSON, feedback, multipart, screenshot and
  skill-download clients already use exact or known-rotated owned lineage.
  Reuse exported resolveOwnedAuthSession, preserve public interfaces/body/error/
  abort behavior, and add account-switch/logout/pending-refresh regression RED
  plus same-owner rotation controls before minimal source changes.
- **MEDIUM, database UTC parity:** at the prior epoch, the Prefect engine lacks
  the API engine's UTC session policy. The source omission is direct evidence;
  production timezone manifestation is unverified inference. Preserve existing
  libpq options and add UTC, retaining pool/SQLite behavior. Constructor and
  real owned non-UTC PostgreSQL regression RED must precede parity repair.
- **MEDIUM, total pagination ordering:** prior feedback and inspiration paths
  order timestamps/ranks without immutable-ID ties. Append final ID ordering,
  retaining filter/count/projection/limit contracts; test equal-sort-key pages
  for exact ordering, repeatability, union and absence of duplicates/omissions.
  No keyset/query framework or schema redesign.

Backend owns five listed product files; frontend owns two raw-client product
files. Both lanes own only their directly related tests/artifacts. No new
dependency, auth/database framework, unrelated refactor, gate reduction, skip,
assertion or timeout weakening. Targeted RED/GREEN and independent diff review
precede one fresh affected aggregate and producer/source-image verification.
Unchanged CLI/site/dependency graph proofs are not repeated without relevance.

## Current repair evidence

The five backend and two frontend product changes are now frozen candidates.
Backend regression RED was 10 failures with eight controls; two real owned
PostgreSQL sessions independently proved the UTC omission. After the minimal
changes, 72 affected tests pass and both real PostgreSQL tests pass, preserving
existing libpq application-name/timeout options. Ruff passes. Equal-sort-key
registered HTTP tests cover all listed ordering branches and featured cutoffs.

Frontend valid pre-product RED was 16 failures with 50 controls. The final
73 real-client ownership tests and four related existing suites pass: 277 tests,
no skips or failures. Scoped coverage exceeds the unchanged thresholds; source
types, new/agent test types and affected lint pass. Expanded API-test typing
exposed eight diagnostics reproduced from its original fixture. Its bounded
typing/current-payload repair now passes expanded affected QA, lint and 135
runtime controls, preserving all 62 original tests and 225 assertion nodes.
Its separate independent one-file fixture review is SOURCE CLEAR.

Independent architect and code/regression re-reviews are **SOURCE CLEAR**:
the three policy contracts are repaired without a new engine, auth, retry or
pagination framework. Fresh aggregate release gates remain separate
requirements. Three E2E helper/type adapters are
independently SOURCE CLEAR with unchanged assertions, skips, timeouts and gates.
Latest dirty source/test/doc bytes also have a hashed persistent safety copy.

## CI-runtime aggregate and remaining fixture parity

At frozen product checkpoint `51f2870`, the complete Node20.20.2 Web run
collected 3,714 cases in 248 files: 3,711 passed, one original SSR skip and two
failures, with no source drift. The upload-draft legacy mock omitted the captured
refresh pair and real ownership resolver, so its expected retry correctly did
not happen; its unconsumed queued success then affected the next error case.
This is a failed aggregate, not an all-green result or coverage approval.

The one-file fixture repair reproduces both failures before editing, then passes
all eight original cases plus the 73 real-client ownership controls on Node20.
Affected QA and lint pass; all eight registrations and 38 assertion nodes are
unchanged. Independent one-file review is SOURCE CLEAR. Product hashes remain
identical. The new complete Node20 run at `3b97021` passes **3,713 cases**
with one original SSR skip, zero failures and identical 3,714-case/248-file
enrollment. Coverage is 81.56% lines, 72.61% branches, 75.63% functions and
78.77% statements, above every unchanged threshold. The failed receipt is
retained separately; no partial results were combined.

Current actual Dockerfile API/worker builds pass with byte-verified application
contexts. Actual worker Python3.11 parses all 356 non-test application files,
with zero image/source hash mismatches. The original complete flow collection
plus the new UTC cases passes187 with two legitimate real-PG-only skips; those
PG cases have separate real-database proof. Actual API Python3.13 imports and
awaited `init_db()` pass under the production non-root image and owned SQLite.
The independent unchanged npm graph at Node20 passes source types and the
actual Vercel build (977 organization URLs, two app URLs, no root shadows).
The retained npm dependency graph, audit and unchanged generator tests were
not repeated.

## Exact backend dependencies and final fixture isolation

The completed host backend run (4,662 passed, 285 skipped, coverage90.95%)
used FastAPI0.136.3/Prefect3.7.2 rather than the declared exact pins. It remains
valid host evidence, not exact-CI dependency parity. An evidence-owned isolated
Python3.12.13 virtualenv now satisfies all41 declared requirements, four exact
pins including FastAPI0.123.7/Prefect3.6.28, and `pip check`, without changing
repository requirements or the original environment. One complete run under
that environment passes **4,662 cases / 285 skips**, with identical 4,947-case
worker collections and coverage **90.94%**, above the unchanged80% gate.

The changed-runtime serialized PostgreSQL gate exposed an explicit fixture
ordering defect:81passed then a module setup failed because the module-autouse
schema isolation dropped tables created by the generation-history fixture.
The ordered two-module target reproduces it (1passed/1setup error). The single
explicit dependency on existing `isolated_serial_postgres_schema` makes schema
isolation precede creation; the fixture body and all six tests/69 assertions are
unchanged. Ordered GREEN2, affected module15, fresh full serialized PostgreSQL304,
UTC2 and registered tie-list7 all pass without skips. Ruff and independent source
review pass. No production code or exclusion/skip/assertion policy changed.

Final default Chromium shards are independent private jobs. Shard2's original
onboarding Skip case failed after25passes (349unrun): the fixture accepted a
transient dashboard URL while login was still restoring a project created by
an earlier spec. The form-only empty-project GET fixture fixes that precondition,
not production navigation. Its exact Skip+auth target passes2; all six test bodies
and42 assertion AST entries remain unchanged, and guarded project-return tests
remain outside the fixture scope. Independent review is SOURCE CLEAR. The failed full receipt is retained. Terminal browser results and bounded
repairs are recorded below; no failed complete run is relabeled green.

## Frozen prior-epoch evidence

At source checkpoint9bbb66c, fresh backend completed with 4,644 passed,
283 original skips, no failures (4,927 collected), coverage90.93% above80%.
The independent serial PostgreSQL gate passed304 without skips. Fresh Web
passed3,640 with one unchanged SSR skip and all V8 thresholds. These receipts
remain valid for that source epoch, not for newly changed product surfaces.

Fresh current Prefect producer protocol passed under both actual production
images: Python3.11.17/3.13.12, Prefect3.6.28/FastAPI0.123.7; original task/flow
contract returns42 against real private API. API image retained non-root UID1000.
Private own runs/containers cleaned normally; this is not old3.4.19 schema
migration parity. Requirements/script hashes remain unchanged. Separate actual
183flow and four-deployment metadata proofs are similarly epoch-bound.

The first default browser run stopped on helper hardcoded8000 while the
owned API used another port:272passed/116policy skips/1failed/361unrun.
A minimal test helper fix reuses existing configured URLs/auth file/origin and
per-test screenshot output. The exact natural-polish precondition now passes;
new complete default CI shards and original four release specs remain required
after source parity freezes. No old partial results are stitched into green.

## Delivery status

No PR push, Actions rerun/dispatch, merge, provider mutation or live deployment
has occurred. Required ci-summary remains strict with enforce-admins. The
protected original dirty worktree was not overwritten. Module and policy source reviews are complete at the audited checkpoint.
Final affected browser validation, latest-main integration, required CI and
exact merged-SHA production readback remain pending. Historical mainCI investigation remains queued after delivery.


## Bounded browser closeout

The complete default Chromium shard1 passes **260 / 116 original policy skips**
with zero failures. The final complete shard2 failure collection processes all
375 cases: **252 pass / 120 original policy skips / 3 fail / 0 unrun**, with no
flaky or outside errors. Their raw union is 750 original unique cases. This
failed complete run is not stitched with later targets into a green aggregate.

The three failures are addressed separately: one real-login `Connection: close`
fixture header resolves the repeated pre-handler transport failure in the whole
**44-case points suite** (44 pass, no skips/retries/flaky results); the original
socket cause remains unknown. The two unchanged voice-status cases pass after
the private runtime supplies the existing `E2E_API_BASE_URL` alias for its owned
API port. No voice source, provider call or network protection changed. Points
lint passes; three baseline QA import diagnostics remain unchanged, not green.

Three original concurrent scenarios pass with actual 200/409 save/conflict
responses after canonical creation/editor locators, conditional folder expansion
and completed same-ID file creation/reselection. The original outside-dismiss
assertion passes with a real pointer relative to the actual backdrop; no forced
click, synthetic event, timeout or product-layout change was used.

The first complete opt-in release collection is retained as **10 pass / 6
policy skips / 29 fail**, all 45 enrolled cases processed. Its failures exposed
obsolete file creation/editor selectors, cross-test screenshot state, and
expectations for a VirtualizedEditor that the current product does not contain.
`Editor.tsx` explicitly uses SimpleEditor for all documents. Replacements must
verify current whole-content, separate-file switching, selection, save/reload and
scroll behavior, rather than asserting a nonexistent chunk label. All case
counts, substantive content/selection/performance budgets and original skips
remain; obsolete implementation-specific names/oracles are explicitly replaced.
Actual >100k data is supplied for the unchanged >100k content-length checks.

The chat benchmark now binds the real message-list scroll element and measures
real scroll-to-requestAnimationFrame elapsed time inside the browser, rather than
two runner IPC calls measured with Node Date.now. Ten physical sends and ten
scroll operations retain the 33.3ms/100ms limits; a separate target measures
7.99ms mean. Existing valid SSE fixtures replace only external generation, never
DOM, timers, rendering or measured values. Original failed measurements remain.

Clean screenshots use one real private-account Visual Test Project and genuinely
empty chat, rather than prior specs' projects/messages. Four initial-commit
Darwin images have been visually reviewed for current coherent layout; no pixel
tolerance change is authorized. Mobile `fullPage` captures the document while
content scrolls in a nested panel: this is a capture boundary, not whole-panel
content coverage. No Linux baseline is fabricated from Darwin. Final changed
fixture receipts/baseline readback remain pending at this checkpoint.

Latest-main integration preserves newly merged #145 (`66f8383`) rather than
overwriting it. Its one test-mock merge conflict and affected product checks are
handled in a separate worktree; previously green aggregates remain valid for
their audited source epoch, not a claim of an unrun combined whole-suite check.
Unchanged whole suites/builds are not repeated solely for fixture edits.


### Latest-main integration and producers

Exact #145 is retained in local merge `c2ea32f`; the sole ChatPanel test conflict
keeps both completion-metadata and new daily-cost upgrade coverage. Fresh
combined affected checks pass **87 frontend / 315 backend**. Source/config types,
affected lint and Vite→organization→docs build pass; nine optional mock-typing
diagnostics reproduce identically before the merge and are not called green.

Fresh actual API/worker Dockerfile builds pass with all **361** current non-test
Python modules byte-identical to the combined source. Actual non-root API import
and awaited SQLite initialization pass offline. Actual worker Python3.11 grammar
and image-source comparison pass all361. The independent unchanged npm graph
at Node20 passes source types and actual `npm run build:vercel`; no install or
repeat dependency audit was needed. These update the changed producer surfaces,
not a claim that unrelated whole suites were rerun.


## Publication scope decision

The existing required `ci-summary` and main push E2E workflows remain unchanged.
The historical opt-in release suite is not enabled by those push/PR gates and
contains initial-commit snapshots and nonexistent VirtualizedEditor expectations.
Its source/baseline repairs are now a separate follow-up branch, not a reason to
keep expanding this module release or to disable a gate. The 50px scroll case
still fails after its initial visible-caret fixture correction and remains an
explicit unresolved test/product-attribution item; it is not called green.
No confirmed product defect is dismissed or hidden. Its related real content,
file-switch, selection and save/reload checks pass in bounded targets.

This release includes the reviewed one-line points login fixture, canonical
concurrent/backdrop/onboarding fixtures already committed, all module/policy
repairs and exact #145 integration. It does **not** include the pending changes
to large-document/performance/visual specs or new screenshot baselines. Current
full default-shard failed receipts and affected fixes are disclosed above; final
required CI and merged-main E2E must supply their own actual results. One batch
push is used; no workflow dispatch, blind rerun, protection bypass or claim of
stitched green coverage. Required CI/merge/exact-SHA deployment remain pending.
