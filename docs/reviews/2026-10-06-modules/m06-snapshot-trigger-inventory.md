# M06 automatic snapshot trigger — repaired, bounded APPROVE / CLEAR

Initial bounded native executor fact inventory; root checked onComplete and
StreamAdapter refusal/success paths before repair. Trigger source is now repaired;
billing source remains unchanged.

- useChatStreaming treats any assistant prose or tool call as snapshot-worthy,
  plus tool names regardless of actual write/status. Snapshot data stores file
  refs/metadata, not chat; chat already persists separately.
- Per-stream completion guards prevent duplicate callbacks, not repeated unchanged
  snapshots across turns. Every server POST inserts Snapshot and reconciles file
  history (including system/quota-free rows if live content differs).
- Success is not a mutation proof: edit no-op may report applied operation while
  content unchanged; all-failed edit returns error; reused create may be unchanged;
  parallel success envelope can contain all failures; a file_content_end also
  fires on truncated-overwrite refusal with no save.
- useAgentStream supplies partial completion for error/cancel, but ChatPanel's
  callback drops that metadata. Cancellation may schedule server background save
  after the frontend snapshot; client capture is not final committed state proof.

## Architect-approved bounded repair (implemented)

Independent architect plan **APPROVE**, Editor/E2E **APPROVE / CLEAR**.
The server normal `done` frame is the single authority: additive
`file_mutated: bool = false` on DoneEventData/done_event. Per-request
StreamAdapter ORs only confirmed committed mutations; AgentService carries its
flag when replacing adapter done after successful chat-history persistence.
Do not infer from UI segments, tool names, statuses or 100-character previews.

At commit-owning tools, expose additive `mutation_applied`: new create true
(empty included), unchanged reused episode false, actual reused promotion/order
true, edit iff content differs (all failed/empty/no-op false), committed delete
true, failed delete false. FileCRUD update exposes locked `content_changed`
for the actual streamed-save path. No update_file registry revival.

Preserve the compact boolean in direct MCP and truncated envelopes. Parallel
execution ORs completed raw task envelopes before per-task/outer truncation;
read-only, failed, dropped and unexecuted tasks do not imply mutation. A completed
partial-success edit with real changed content does.

Stream persistence returns its exact locked content-change outcome; set adapter
mutation only after a successful changed commit. file_content_end alone is not
proof: truncated-overwrite refusal emits it without saving. Abort background save
is not a normal completion authority. Return the outcome from the worker thread
to the async wrapper, rather than mutating adapter state in that thread.

Web forwards optional confirmedFileMutation from done through useAgentStream
completion metadata and ChatPanel's existing third argument. Auto snapshot iff
partial !== true, confirmedFileMutation === true and active project exists.
Remove prose/tool-name triggers and speculative file-title descriptions; use
existing generic aiDoneFilesModified. Absent flag false. Keep dedup, cleanup,
refresh, chat persistence, manual snapshots and best-effort failure handling.

**Billing boundary:** no api/agent.py changes or refund coupling in this M06
repair. Cancellation/error partial chat persistence and reservation/refund rules
remain. Demonstrated substantive-output issues belong to M07/M12 review.

**Nonblocking WATCH:** the later browser POST captures then-current project state,
not an immutable per-turn write set or atomic AI-turn snapshot. Contract: normal
completion detected at least one confirmed committed file mutation; create one
best-effort current-project checkpoint. Exact attribution needs a server-owned
version-token design and is intentionally not introduced here.

### Required genuine RED -> GREEN cases

- New empty/populated create true; reused unchanged false, promotion/order true.
- Changed edit/partial-success true; no-op/empty/all-failed false; delete success
  true/failure false; failed commits never advertise mutation.
- Streamed changed save true/same false; refused truncated overwrite false despite
  end event; failed history persistence does not emit normal done.
- Parallel read-only/all-failed false, mixed write true, large truncation true,
  dropped write ignored; multiple writes cause one snapshot.
- AI prose/read-only/failed named tools, absent/false metadata: no snapshot.
- Explicit mutation true: one even without tool segments; partial true suppresses.
- ChatPanel forwards true/false/partial; per-request reset/project switch no carry,
  null project false; snapshot exception remains noncritical.

Root owns backend/docs; native executor owns four Web plumbing files and tests.
Tests lock behavior before source mutation; use focused local unchanged coverage,
types/lint/build and minimum integration. No Actions, pushes, providers, dependencies
or schema changes. Current status: implemented; independent source review
**APPROVE / Architectural CLEAR**. Full M06 integration review remains pending.


## Implementation and fresh verification

Commit facts are additive only at tool boundaries: FileCRUD new create true,
reused screenplay existing changed branch exact, update locked content_changed;
FileEditor exact content inequality; MCP delete literal executor true. Direct MCP
and compact overflow envelopes preserve strict booleans. Parallel ORs completed
raw envelopes before task/outer truncation. StreamAdapter ORs confirmed successful
(including partial-success) markers; successful worker save returns
(saved, content_changed), applied only by async owner after commit. Abort background
passes record_mutation=False so an old completion cannot contaminate reset state.
Service emits default-false/true done only after chat history save succeeds.

Web additive done field -> optional completion metadata -> ChatPanel third arg ->
normal literal-true + active-project gating, generic description. Old server
absent flag remains conservative; prose, names, statuses and attempted titles
no longer create or mislabel snapshots. Billing/reservation/refund code untouched.

### Exact receipts (evidence directory)

- m06-mutation-backend-red-candidate.log: initial fixture missing session_id
  produced18 setup errors; not a valid regression receipt.
- m06-mutation-backend-red.log:35 genuine missing-signal failures, four compatibility
  passes, five invalid parallel-call harness failures (dict instead of task list).
  The five are not counted as genuine RED. Corrected original b88d9af
  execute_parallel AST function with current shared helpers produced five genuine
  REDs in m06-mutation-parallel-red.log; no worktree source reversion.
- m06-mutation-backend-green.log:48 pass/33 deselected (44 new signal cases and four
  service cases). Later two integrated cases are separate, not one50 receipt.
- m06-mutation-http-green-candidate.log:four pass/one invalid stream fixture lacked
  real TOOL_USE/tool_use_id, so a body-only fake stream had no assistant payload.
  Corrected authentic event fixture preserves tool/result persistence;
  m06-mutation-http-green.log:five pass/three deselected. Real tools/commits plus
  authenticated HTTP, adapter, history and normal SSE done; no model provider.
- m06-mutation-backend-coverage.log:14-file candidate452 pass/one obsolete HTTP
  harness failure and76.29% scoped coverage — **not GREEN**.
- Stable-source22-file m06-mutation-current-coverage.log: **565 pass**, scoped
  seven-backend-module coverage **85.38%**, unchanged80 gate. Includes the five
  HTTP cases; do not sum them again. The additional relevant tool/skill/parent/
  cross-project tests cover shared serialization, idempotency and failure boundaries.
- Architect requested a marker-flow proof starting false: actual unchanged reused
  create, closed file-marker changed save, committed DB body, done true; contrasted
  with existing refused unclosed/no-save/end-event false. Added oversized partial
  serializer->adapter proof too. m06-mutation-stream-integration-green.log:
  **two pass/44 deselected**, appended stable-source coverage85.38. Not one567 run.
- m06-mutation-postgres-green.log: **two actual PostgreSQL14.22 pass** for unchanged
  and changed persisted streamed content/done flags. Local SQLite parametrization
  of PG control-flow branch is not presented as real PG. Only owned test DB
  zenstory_audit_m06_mutation_20261006_root removed afterward; catalog0/sharedhealth1.
- Web m06-snapshot-trigger-red.log:10 genuine fail/200 pass -> four-file
  m06-snapshot-trigger-green.log: **210 pass**. Scoped S71.49/B57.76/F79.8/L73.08
  exceed unchanged66/55/61/68. ESLint, TypeScript and diff RC0. Existing harmless
  localhost mock ECONNREFUSED noise is not a remote service probe.
- m06-mutation-web-build.log: Vite **11.48s**, org475x2 and docs24 routes pass.
  Backend scoped Ruff/py_compile and git diff --check RC0 from actual tool outputs,
  including the two final new tests. No dependency/lock/source billing changes.

Independent architect final source **APPROVE / CLEAR** with the above integrated
test/doc follow-ups now resolved. WATCH current-project/not-turn-atomic remains.
Whole-backend mypy is still a documented pre-existing SQLModel/type gap, not GREEN;
local Node25/PG14 differ from CI20/PG15. Aggregate all23 local gates, necessary
CI/PR/merge and authenticated exact-SHA production are still required. No Actions,
pushes, paid calls, schema/provider/env/retention changes. Deferred remote main-CI
investigation stays queued and untouched; these local cases establish no remote
main-CI cause.
