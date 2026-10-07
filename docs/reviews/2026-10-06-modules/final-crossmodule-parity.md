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
identical. A new complete coverage receipt is still required; previous partial
results are not combined into a claimed successful aggregate.

Current actual Dockerfile API/worker builds pass with byte-verified application
contexts. Actual worker Python3.11 parses all 356 non-test application files,
with zero image/source hash mismatches. The original complete flow collection
plus the new UTC cases passes187 with two legitimate real-PG-only skips; those
PG cases have separate real-database proof. Actual API Python3.13 imports and
awaited `init_db()` pass under the production non-root image and owned SQLite.
The independent unchanged npm graph at Node20 passes source types and the
actual Vercel build (977 organization URLs, two app URLs, no root shadows).
No dependency install, audit or unchanged generator tests were repeated.

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
protected original dirty worktree was not overwritten. Fresh source review,
related gates, final required CI and exact merged-SHA production readback are
still pending. Historical mainCI investigation remains queued after delivery.
