# Functional module review — 2026-10-06

## Target and boundaries

Review **every module** for (A) simpler architecture, (P) frontend/backend
performance, and (B) bugs. Fix evidence-backed findings, then create one aggregate
PR, merge after passing checks, and verify actual production delivery. Inventory,
partial fixes, a preview, or a merged PR alone do not satisfy this goal.

- Start: remote main `b88d9af1b90519b226b8200c82fcbc87d47621a1`.
- Worktree: `/private/tmp/zenstory-module-audit-20261006`;
  branch: `review/modules-architecture-performance-20261006`.
- Preserve the original dirty workspace, other worktrees, current product
  behavior, feature flags, payment/history contracts and deployment producers.
- Ordinary direct execution with bounded native advice/review; no Conductor
  authority, Team or Ralplan runtime is asserted.
- No new dependencies, speculative abstraction, visual redesign, npm publish,
  real purchases, data deletion or production/provider configuration changes.
- Latest priority: review monthly material-library quota as well as daily quota;
  PR #140 is already in this base, so verify it rather than duplicate it.

## Inventory evidence

Evidence directory: `/private/tmp/zenstory-module-audit-evidence-20261006`.
Current source inventory: **220 backend route registrations / 219 handlers**
(216 API registrations plus four main routes); **42 web Route nodes / 40 unique
full patterns**, including layouts and fallback; **22 CLI commands**.
See `backend-map.md`, `backend-endpoints.json`, `frontend-routes.json`, and
`cli-release-map.md`. Counts are coverage anchors, not review-completion claims.

## Module matrix

All three axes and leaf coverage must be evidenced before a row is complete.

| ID | Scope | State |
| --- | --- | --- |
| M23 | Startup, DB/Redis, middleware, health, dependencies/images, CI/CD/release | Reviewed/fixed: independent whole-module SOURCE_APPROVE/CLEAR; DDL savepoints/UTC/probe concurrency/bounded fallback/logging and required serial PG enrollment reviewed. Local evidence and corrected 21PASS1FAIL historical fixture receipt in m23-final-module-source-review.md; fresh exact26-file PG304PASS after test-only isolation repair; image/global/release gates pending |
| M01 | Auth, verification, password, SSO/OAuth, frontend identity/cache/guards | Reviewed/fixed: independent final all-leaf SOURCE_APPROVE_CLEAR. Backend worker/SSO/bootstrap/provider/revocation/verification and frontend C1-C7 fixes integrated; fresh439Web, final34OAuth/85.96% closes last no-awaitvalidate-token leaf. Mixed-await/cross-tab/timer/provider/type WATCHs disclosed in m01-final-module-source-review.md; not all23/release |
| M02 | Workbench shell/providers, settings, onboarding, persona, state/query cache | Reviewed/fixed: independent final all-leaf SOURCE_APPROVE_CLEAR. Provider scope, persona worker placement, dirty-field hydration, late-save lifetime, scroll namespace and visible mobile project restoration closed; fresh81Web/scoped94.26L/type/lint/properCSSbuild10.37s, backend26/94.80%, earlier189provider gates retained. First-profile409/tour/storage/policy WATCHs in m02-final-module-source-review.md; not all23/release |
| M03 | Projects/templates, dashboard lists, writing statistics/trends | Reviewed/fixed: independent all-leaf SOURCE_APPROVE_CLEAR; project capacity, atomic copy/quota, daily/streak/cache transaction and remaining UI/stat/SQL/date leaves covered. Fresh combined185SQLite84.69% sixmodules and24serialrealPG PASS; accepted WATCHs in m03-final-module-source-review.md; not all23/release |
| M04 | Files/directories/tree, upload/move/export | Reviewed/fixed: independent all-leaf APPROVE/CLEAR. Canonical, structural writers, delete/default-expiry/index, real panes, multipart validation/SQL/worker, deep HTTP tree and export covered. Fresh integrated269PASS/scoped86.56%, export50PASS/local CIcontract9; accepted legacy metadata/SQLite/device/type/aggregate limits in m04-final-integration.md |
| M05 | Editor, autosave, draft restore, natural-language polish | Reviewed/fixed: independent all-leaf APPROVE/CLEAR; C1-C5 plus final late-rename selection/footer delta narrowly integrated six paths/exact reviewed bytes, fresh root136PASS; exact-source native scopedcoverage/types/lint/build/complete-CSS browser19PASS reused. Invalid CSS menu inference withdrawn, physical/long-pinch/durable-state WATCHs in m05-final-integration.md |
| M06 | Versions, snapshots, diff, rollback | Reviewed/fixed: independent all-leaf source APPROVE / CLEAR; locked transactions/monotone tokens, creation baselines, immutable Undo, cleanup, query/diff/paging and committed-mutation snapshots. Final hierarchy/translator fixes have SQLite, actual PG and scoped Web/backend evidence. Accepted contracts, WATCHs and aggregate/browser/CI/environment gaps in m06-final-integration.md; not release approval |
| M07 | Chat/Suggest, history/SSE, Agent workflow/tools/run lifecycle | Reviewed/fixed: independent all-leaf SOURCE_APPROVE_CLEAR; R1-R7 integrated. Fresh root144SQLite PASS/scoped85.22% +1realPG PASS, CIcontract9 and Ruff0; types4legacy unchanged. Default summaries avoid2/16MiB ORM body materialization; accepted limits in m07-final-module-source-review.md; not aggregate/release |
| M08 | Custom/public skills, resources, sharing/discovery/usage | Reviewed/fixed: independent whole mapped-module SOURCE_APPROVE_CLEAR; C1-C7 narrowly integrated. Freshroot37+25 Web,80HTTP92.36% and12realPG; serialPG localcontract9, typesbaseline/noadded. Accepted WATCHs in m08-final-module-source-review.md; not aggregate/release |
| M09 | Materials upload/decomposition/retry/refund/import/library, Prefect stages/subflows/tasks/worker | Reviewed/fixed: independent whole-module SOURCE_CLEAR; durable monthly reservations/refunds, atomic interrupted download, originating-session upload/import refresh and Chapter projections. Fresh root92PASS80.89%, separate8/99PASS; legacy types not green. See m09-final-module-source-review.md; final aggregate/release pending |
| M10 | Voice | Reviewed/fixed: independent architecture approval; backend/web/Chromium/WebKit evidence; physical-device smoke boundary documented |
| M11 | Vector/hybrid search, context, index lifecycle | Reviewed/fixed: independent whole-module SOURCE_CLEAR; metadata dictionary compatibility, authoritative indexed identity and body-free ownership validation. Exact4paths root180PASS83.82445%; legacy55types unchanged. See m11-final-module-source-review.md; aggregate/release pending |
| M12 | Plans/subscription/quota, Zpay callback/reconcile, redemption, checkout/return | Reviewed/fixed: independent whole mapped-module SOURCE_APPROVE_CLEAR; seven SQL route worker declarations62PASS95.77%, Web84PASS, prior atomic financial/calendar/PG approvals retained; legacy types13→13 and accepted WATCHs in m12-final-module-source-review.md; not aggregate/release |
| M13 | Points, check-in, referrals/rewards, membership redemption | Reviewed/fixed: independent whole-module SOURCE_APPROVE/CLEAR; SQL worker boundaries and FIFO/check-in/referral contracts, UTC UI dates and rewards-error state reviewed. Fresh root27 dates and60 error-related PASS; backend/scoped/static gates and accepted limits in m13-final-module-source-review.md; not aggregate/release |
| M14 | External Agent API, keys/scopes, file/version/search/context contracts | Reviewed/fixed: independent whole mapped-module SOURCE_APPROVE_CLEAR; final key paging/null contracts36PASS99.38%, prior auth/CRUD/projection/write/context/search approvals retained. Accepted capacity/compatibility WATCHs in m14-final-module-source-review.md; not aggregate/release |
| M15 | Admin shell/users/dashboard/metrics/audit | Reviewed/fixed: independent all-leaf SOURCE_APPROVE_CLEAR; canonical FIFO circulation, total audit pagination order, pending user header lock and UTC desktop/mobile timestamps. Fresh backend114PASS/scoped93.24%, Web30/typeslint0;19legacy mypy unchanged; accepted policy/scale WATCHs in m15-review.md; not aggregate/release |
| M16 | Admin plans/subscriptions/codes/quota/payment orders | Reviewed/fixed: independent whole mapped-module SOURCE_APPROVE_CLEAR;14handlers70PASS94.90% andfivepages60PASS plusclipboard3ROOTPASS, types/gates andacceptedWATCHs in m16-final-module-source-review.md; not aggregate/release |
| M17 | Admin points/check-in/referrals | Reviewed/fixed: independent whole mapped-module SOURCE_APPROVE_CLEAR;canonicalwalletcount, totalhistorypages andboundedownerjoins108PASS85.75%/types14→10, UTC/calendar/pendingUI34PASS92.02L68.86B84.84F91.66S/typeslint0. AcceptedWATCHs in m17-final-module-source-review.md; not aggregate/release |
| M18 | Admin prompts/skill review | Reviewed/fixed: independent whole mapped-module SOURCE_APPROVE_CLEAR; status/router/timer/resource/pending/date repairs exact fivepath integration0conflicts, freshROOT39PASS and native scopedcoverage/sourceQAtypeslint gates retained. Accepted WATCHs and shell receipt limitation in m18-final-module-source-review.md; not aggregate/release |
| M19 | Inspirations flag/list/detail/copy, admin inspirations/feedback, user feedback | Reviewed/fixed: independent all-leaf SOURCE_APPROVE_CLEAR; C1-C8 plus search page and pending-close parity covered. Metadata5rows491910→0 omittedbody, feedback boundedSQL/worker, review/audit atomic. Tests/types/scopedcoverage and accepted policy/data/scale WATCHs in m19-final-module-source-review.md; exact-head aggregate/build/CI/release remain |
| M20 | Public homepage/pricing/legal/entry, SEO/analytics | Reviewed/fixed: independent whole-module SOURCE_CLEAR; shared owned scene timeout, static legal paragraph parity and existing public contracts. Exact3paths freshroot24PASS, scoped types/lint/coverage retained; see m20-final-module-source-review.md; global artifact/release pending |
| M21 | Bilingual docs, static org generator/routes/content safety/site layout | Reviewed/fixed: independent whole-module SOURCE_CLEAR; bilingual/topic route enumeration and escaped layout parity, existing generators retained. RootNode20site12PASS/routes4PASS/typeslint0; see m21-final-module-source-review.md; full artifact/release pending |
| M22 | All CLI commands, config/security, HTTP/output, installation, nonpublishing packaging | Reviewed/fixed: independent whole MODULE_SOURCE_APPROVE_CLEAR;22commands, metadata2→1/base safety andintegratedatomicCLI/serverCAS. Fresh112PASS/typesbuildhelp0,releasecontracts12/nonpublishingcheck0; acceptedWATCHs/environment/package limits in m22-final-module-source-review.md; not registry/release |

## Review and repair protocol

1. Map each leaf and call graph, normal/error states, current feature flags,
   tests and integration boundaries. Unmapped leaves block final publication.
2. A: remove only proven redundant work; use existing utilities/patterns instead
   of adding layers. Write the cleanup plan and lock behavior with regression
   tests before refactoring. Keep author and reviewer separate.
3. P: measure relevant request/query/render/operation counts, latency, bundle,
   blocking IO, memory or concurrency with a reproducible pre/post experiment.
   Do not present a synthetic result as production p95.
4. B: check auth/isolation, validation, period boundaries, idempotency/races,
   errors/timeouts, persistence/history and schema compatibility. Establish RED
   regression evidence before the minimal fix when coverage is missing.
5. Record fixes or justified no-change results for each axis. Do not defer a
   proven blocking bug merely to declare the row complete. Reopen affected rows
   when shared-boundary fixes change their assumptions.
6. Targeted tests first, then lint/type/static and relevant integration checks;
   bounded independent review of meaningful changes. Working reports stay local
   under `docs/reviews/` (git-ignored) with source references, experiment/test
   evidence and explicit remaining gaps; durable decisions go to `.agents/notes/`.

## First bounded candidate: M23 readiness

`services/infra/readiness_service.py` currently awaits independent database and
Redis probes sequentially. Review a minimal `asyncio.gather` repair to avoid
adding their latency, preserving per-probe deadlines, logging, response ordering,
skipped Redis, error/timeout statuses and static `/health` liveness.

- Existing baseline: observability + deployment-memory tests, **13 passed**.
- Before edit: deterministic concurrent-start regression and a repeated
  controlled equal-delay probe experiment.
- After edit: preserve one dependency's result when the other fails/times out;
  Redis absent must invoke no Redis probe. No health/gate semantics change.
- This candidate alone does **not** complete M23.

## Final gate

All 23 rows and leaf coverage complete; final-head backend/web/CLI/site tests,
lint/type/compile/build, and relevant full/release/default E2E and performance
scenarios. Clean-context independent code reviewer and architect must approve.
Aggregate PR exact-SHA CI and merge, then exact-main CI/E2E and authenticated
provider Git source/gate/deployment readback plus readiness receipts. Follow
`docs/ops/release-ci-cd.md`; report `PROVIDER_GATE_NOT_VERIFIED` until actual
readback. Never replace the existing Vercel/Railway Git producer with manual
deploys or force an unchanged application deployment.

## Bounded cleanup candidate: M02 route provider scope

Admin and persona onboarding do not consume project, material, quote or skill
contexts, but their route elements mount all five workbench providers outside
their auth guards. `ProjectProvider` eagerly calls `projectApi.getAll`; the
material provider fetches subscription status and, when entitled, library
summary. Plan: remove only these two unused wrapper applications, preserve
`AdminRoute` / `ProtectedRoute` and top-level auth/theme/SEO/query boundaries.
Keep dashboard, editor and material detail composition unchanged (dashboard
shell itself consumes project state). First add an actual-provider integration
regression measuring the three initialization API calls on fresh entitled
sessions, and preserve dashboard initialization and denied-access behavior.
No new provider layer or path-dependent hooks.

## Monthly refund durability repair plan (M09/M12)

The existing charged flag is sufficient as durable pending state; do not add a
ledger, schema or scheduler. Preserve it on refundable failure, then atomically
settle quota decrement and billing flags in one caller-owned transaction, guarded
by the exact job snapshot. Failed charged jobs must retry settlement on reads.
Old-month reservations never affect a different month's counter. Upload/retry
reservation and initial job creation must commit together: pre-job errors roll
back, not compensate an already committed unit. Remove duplicated quota-only
dispatch-failure refunds; use the job service. If failure-state persistence fails,
retain the charged job for reconciliation rather than create a quota-only crash
window. Include older failed jobs already returned in material list joins; no
unbounded reset loop or new worker. Independent architecture advice precedes
implementation; deterministic fault/rollback/stale-snapshot regressions first.

## Bounded conditional file-write repair (M14/M22)

The CLI's GET/preflight is advisory: the write must enforce its exact timestamp
on the server. Add an optional `base_updated_at` field consistent with the web
DTO and pass the raw CLI token (including microseconds). Keep existing backup,
shrink, empty-content and no-precondition behavior. Reuse the current SQLite
stripe lock and PostgreSQL NO KEY UPDATE write transaction, refreshing stale
identity-map objects after taking the lock; normalize naive legacy timestamps
as UTC, reject both older and future/nonmatching tokens before any mutation.
Advance the persisted token even under a frozen/backward clock. Save content and
version in one transaction, retain version-quota and rollback semantics, schedule
no index task for rejected writes. Since this handler has no async IO, run its
sync DB/lock work in FastAPI's ordinary sync handler threadpool, not the event
loop. Lock regression behavior first; get separate architecture advice before
source changes. Prove true concurrency on isolated PostgreSQL, not only mocks.

## Cross-writer timestamp follow-up (M04/M06/M07/M14/M22)

Independent review found that a frozen/regressed clock lets a web/tool/rollback
save retain the old token, even with the Agent endpoint's own strict advance.
First reproduce on each existing locked content writer and on real PostgreSQL
web-to-Agent writes. Reuse one small datetime utility accepting the current
stamp and caller's clock, preserving naive-UTC normalization and microseconds.
Call it only after a fresh locked load; keep snapshots, quotas and indexing
unchanged. Test future clock, equal clock, backward clock and offset timestamps.
Move/reorder handlers currently lack the same locked load and remain explicit
M04 follow-up work; do not claim a universal token contract before those leaves
are reviewed. No new transaction/lock layer or version-counter schema.

## M04 synchronous export boundary

The TXT export handler has no asynchronous IO but performs sync ORM queries and
full-project formatting on the event loop. First use an actual ASGI request to
assert the handler executes outside the loop thread. Change only its declaration
to FastAPI's sync handler path, preserving authorization, format entitlements,
sorting, BOM, filename/header, and error contracts. Verify existing export API
and service cases. Do not introduce streaming or change the download payload;
large-export memory cost remains a separate measured/contract-sensitive concern.

## M10 recording lifecycle and real audio format

Lock cancellation, unmount, queued MediaRecorder events, empty recordings,
normal-stop resource release and error-reset races with regressions first. Reuse
the existing request generation across the entire recording/conversion/request
lifecycle; detach event handlers before disposal and release recording resources
once final data has been captured. Track the existing recovery timer rather than
adding a state-machine abstraction. Mirror the existing desktop permission-pending
touch cancellation in MobileChatInput. Keep UI, language and 55-second contracts.

The provider's actual SentenceRecognition contract is 60 seconds / 3 MB and
does not accept WebM or FLAC. WebM Opus cannot be relabeled Ogg Opus. Get bounded
architecture advice for browser-native mono WAV conversion/resampling; verify
container, sample rate and channels in a real local browser, then tighten the
advertised/backend formats and size bound together. No production FFmpeg/new
dependency, provider charges, remote workflows or deployment mutations.

## M06 snapshot follow-up — first regression boundary

Independent all-leaf review found raw snapshot-rollback timestamps can regress
content, undelete, metadata-only and soft-delete writes. First lock all four
mutation paths with a frozen clock and future persisted tokens. Use the existing
strict advancement helper, not a new counter/schema. Architecture advice must
separate the bounded clock defect from fresh-row/project-wide locking races;
prove true PostgreSQL interleavings before claiming a universal concurrency fix.
Preserve the current caller-owned transaction covering safety snapshot, all file
mutations and restore versions. Delta/FK cleanup, baseline creation, undo and UI
findings remain separate ordered repairs; do not enable a retention job or change
production records as part of this review.

## Bounded cleanup: remove unreachable delta-chain deletion

`FileVersionService.cleanup_old_versions` has no repository production, API,
CLI, test or documentation caller (whole-repo rg, excluding dependency trees and
review reports). Prior independent M06 reviewer reproduced deletion of an
intermediate auto-save delta changing later replay; architect recommends deleting
this unused unsafe helper rather than inventing a compaction subsystem. Preserve
all active create/read/diff/rollback and quota interfaces. Before removal, lock a
three-version aged auto-save delta chain and snapshot pinning with a service
regression, and run existing reconstruction tests. Then delete only the method
and imports used exclusively by it. No retention job/migration/actual data cleanup,
new dependency, public version endpoint or best-effort history contract changes.
Author and independent reviewer remain separate; bounded approval still required.

## M06 structural creation baseline — five missing paths

Read-only creation inventory confirms Web ordinary creation, material upload,
draft upload, material import and inspiration copy commit populated files with
no initial history. Lock those paths with authentic ASGI/direct-service tests:
exactly one system/create full-content v1 before opening Editor; empty/folder
exclusion; zero user-version quota; each split-upload child; unexpected failure
before commit, including the second child's failure; inspiration commit=False
and material-import folder/file transaction preservation.

Reuse FileVersionService.create_version with its caller-owned transaction flags
through one small create_initial_version method on the existing service. It is
only for newly added files, skips folders/empty content, flushes, then creates a
system/create, force_base, skip_quota, commit=False baseline. Call the draft batch
baseline stage outside its per-upload parsing exception handler, so a database
failure cannot be converted to a validation warning and committed without v1.
Preserve upload validation/partial-invalid-input behavior and the existing
canonical-folder helpers' independent commits; do not claim folder creation is
part of upload-content atomicity. Material import already shares its folder/file
transaction; inspiration preserves caller-owned commit=False.

Keep Agent/Tool's existing quota-aware/best-effort variants and all subsequent
history-save contracts unchanged in this slice. Therefore this does not yet
establish a universal creation-baseline invariant, and Editor's legacy fallback
must remain until those contracts and old-file compatibility are settled. No
retention job, schema change, dependency, public-version-endpoint removal or
production data mutation. Independently review the bounded implementation after
focused local tests; main CI investigation remains queued, not started.

## M06 integration rework after independent review

The first UX integration is REQUEST CHANGES: SimpleEditor hard reload destroys
new quota/omission feedback; append failure hides loaded history; snapshot
file_metadata DTO mistakenly declares object instead of JSON string. Lock all
three with regressions before source changes. Use existing Editor state/refetch
boundary, no page reload or detached replica of its file token. Keep history
mounted so feedback survives, reset stale dirty/debounce baseline around refresh,
and prove actual Editor→SimpleEditor→FileVersionHistory flow plus the next save's
fresh optimistic token. Keep append errors separate and retryable. Preserve the
JSON-string metadata contract instead of changing backend storage/shape. Any
save-queue/restore race affecting this path must be measured; broader project
snapshot lock repair remains separate. Independent architect advice requested.

## M06 snapshot protocol — architect-refined transaction repair

The earlier baseline-only slice's independent upload-folder commits are now
explicitly superseded for production constructors: hold Project SHARE from fresh
parent validation through the caller's final folder/content/history commit. Keep
the private standalone helpers' committing defaults. Duplicate canonical IDs
recover inside a savepoint, never outer rollback; a bounded real SQLite BEGIN
fix prevents first-write savepoint release from committing a folder prematurely.
Regression tests were RED before these changes. No global engine policy changes.

Rollback takes Project NO KEY UPDATE plus relevant File NO KEY UPDATE in ID order,
refreshing rows before safety. This refinement avoids a writer-first FileVersion
FK deadlock of the initial Project UPDATE proposal. Standalone snapshots take
SHARE before gather; API auth and scoped target validation are refreshed after
waiting. Protected affected IDs feed reconciliation without changing response
shape. Existing ordinary one-file writers do not join Project SHARE; a scoped
rollback must permit unrelated edits. No all-file point-in-time guarantee against
ordinary edits, universal SQLite cross-process locking, new dependency/schema or
retention activation. Bounded evidence and remaining review are recorded in
`m06-snapshot-protocol.md`; all remaining module leaves and delivery gates continue.

## M06 immutable Chat Undo — bounded plan closure

Anchor an edit only to an existing latest version that exactly reconstructs its
fresh locked before-content. Read this optional provenance inside a savepoint;
do not insert another before row or change best-effort history/quota contracts.
Carry before_version_number plus this edit's own normalized monotone timestamp
through persisted result/card/list/panel. Capture that timestamp under lock before
commit, emit only after successful commit, never from post-commit refreshed state.
Conditional rollback checks exact normalized token after fresh File lock before
all history/content/quota mutation; no-body restores preserve intentional history
compatibility. Legacy/malformed cards hide Undo and never guess another target.

Authentic SQLite/HTTP and real-PG interleaving REDs became focused GREEN; root's
post-commit race counterexample superseded initial approval before minimal repair.
Final independent architect **APPROVE / CLEAR**, full bounded receipts in
`m06-immutable-undo-plan.md`. Do not treat bounded source approval as closure of
remaining M06 leaves, all23 modules, aggregate quality gates or production release.

## M06 missing-version rollback error consistency

The all-leaf review identifies a Web rollback of a missing version returning400
while Web content/compare and Agent version detail/rollback report typed404.
Lock actual HTTP rollback with/without a current conditional token, compare its
error to existing content/compare, and prove content/token/history/index/cache
unchanged. Align only Web rollback's existing ValueError mapping to the same
VERSION_NOT_FOUND404 used by sibling read/compare routes. Keep service ValueError
compatibility, token-first409, authorization, no-body restore, and quota behavior
unchanged. No extra existence query or public endpoint removal is needed.

## M06 comparison work and inline diff regressions

Compare currently loads each target twice (content then metadata), and computes
the same splitlines SequenceMatcher once for structured HTML and again for stats.
First lock exact payloads (including newline-only/empty/repeated content, reversed
and same-version comparison, missing targets) and count base/delta SELECTs and
matcher passes. Reuse existing get_version_by_number/loaded-target reconstruction
and derive added/removed counts from the already-generated structured diff; retain
unified_diff's keepends semantics and all response fields. No new replay layer or
schema/dependency/response truncation.

Inline DiffViewer exposes Hide unchanged but does not filter its inline rows, and
changed paragraphs lack separators while its text container collapses whitespace.
Lock all three view modes' filter toggle plus exact inline text/newline rendering
before changes. Reuse the same existing filter rule, preserve paragraphs with
whitespace-pre-wrap and a newline for each structured line. Measure representative
small/large comparison and local-browser node counts/render time; no speculative
virtualization, limits or default-view change absent evidence/contract approval.

## M06 creation/history policy closure — architect-approved minimal repair

Read-only architect confirms Agent API quota-aware creation (unexpected failure
aborts; expected quota overflow alone may omit history), FileCRUD AI best-effort
creation/fill, and public arbitrary-content user checkpoint POST are intentional.
Preserve these contracts and add direct injected-failure/empty-fill tests where
coverage is absent. Public POST is not a baseline-only endpoint; do not remove it.

Remove only Editor's GET-latest→POST-initial bootstrap. It mutates on read using
stale loaded content, races other writers/tabs, is forcibly user-attributed and
quota-charged, and adds a history GET on every reload. Genuine frontend RED first
requires populated/new/legacy loads to do zero history reads/writes; existing
save-quota feedback stays. Legacy empty history remains explicitly supported;
do not invent server backfill-on-GET or a universal migration. Populated Web
constructors already own their transactional initial system baseline.

Align the two local backend E2E contracts with the new system v1: preserve the
populated creation path and assert user checkpoints v2/v3, list3/2/1, compare2/3,
rollback2 producesv4. Do not make test files empty just to keep old numbering.
This is current M06 affected local validation, not the deferred remote main-CI
investigation. No Actions, gate reduction or source/history API strictification.

## M06 automatic snapshot trigger — approved cleanup plan

Independent architect approved the done-authority contract and exact test matrix
in `m06-snapshot-trigger-inventory.md`. Lock genuine RED tests first, then expose
commit-owned mutation_applied signals, preserve them through truncation/parallel
aggregation, and emit a default-false file_mutated normal done after history
persistence. Stream save uses exact locked content_changed result, never end-event
guessing. Web forwards completion metadata and replaces prose/tool-name triggers
with explicit normal confirmed mutation gating and generic description.

Keep all cancellation/error billing and partial chat persistence unchanged; no
api/agent.py mutation. Absent/failed/no-op/read-only/drop/refusal signals false.
Preserve existing cleanup, dedup, manual snapshots, and best-effort exceptions.
Checkpoint is then-current project state, not per-turn atomic attribution (WATCH).
Review/coverage/type/lint/build/integration follow, no dependency/schema/Actions
change or gate reduction. Full M06/all23/delivery remains open.
