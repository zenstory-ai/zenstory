# M07 R1: retain generation ownership through history finalization

Attributed to the standalone native backend Codex lane, 2026-10-06. Conditional design authorization came from the coordinator's independent architect advice. This candidate is pending independent SOURCE review/integration, not M07/all23 closure.

## Problem and change

Noncancelled failure previously released its exclusive steering ownership before compensating history SQL. A second run could load pre-A history and begin generation while A still saved issued output. Cancellation's drained-steering branch also released ownership before partial/late writes.

`agent/service.py:728` now owns each finalization SQL task and shields it. If its awaiter is cancelled, it waits for the real worker result, consumes its exception, then re-raises the original cancellation. This is a local history-finalization helper, not a new transaction/gate framework.

Cancellation's late-steering decision at1274 and noncancelled decision at1362 use existing `_hand_back_steering` while A remains registered. The existing atomic legacy-holder handback policy is preserved, including deletion of handed-back consumed entries. The obsolete release-before-handback helper was removed. Compensation/late append finish before cleanup at1407, with guaranteed heartbeat stop in the inner finally. Existing cancellation background cleanup still waits for its save task at1323.

No new locks/schema, counter workaround, public signature, session policy, provider, error/SSE/retention policy or generic framework. Imports and all bytes outside process_stream are exact. Nine other top-level/class function texts/ASTs are exact. MessageManager SQL/counters are unchanged.

## Regression-first evidence

Frozen prior R1 proof remains intact:1 genuine RED/3 controls, root actual PG. New required boundaries were added before source edits:

- history_saved=true late append after real primary save;
- history_saved=false compensation with actual drained steering;
- cancellation while awaiting held compensation/append worker;
- cancellation background saves with drained steering and late append.

First expanded race run:6 genuine ownership RED,4 earlier cases deselected. Strengthened before-source run, adding an actual after_commit observation and required failure/handback controls:6 genuine RED/4 PASS/4 deselected. Pre-source service SHA remains730710ff8ef30dd1cde37676c594b9c78a5cef21310446d9b699ca7c2aef9a53. No invalid PG fixture execution.

Final exact candidate matrix: **15 actual-PG PASS**. All held boundaries retain active owner and heartbeat; B's actual same-session stream raises SteeringSessionBusyError before workflow/history generation. Cancellation during finalization does not finish A's awaiter before the worker commits. Subsequent C claims normally and observes A's output plus steering exactly once; counter7 equals seven real rows. Earlier cancellation/sequential controls remain. Real drain/compensation/late-append failures still release ownership/stop heartbeat and preserve existing best-effort warning logs. A real legacy holder receives handed-back steering instead of A storing it. Additional actual primary-history SQL failure emits error SSE, no done, and compensates issued history.

Only synthetic provider/workflow and exact failure-injection boundaries are replaced. Ownership/resolver/history/ORM/compensation execute. Source-attributed first DML holds and real Session after_commit events establish order; no sleeps/timing acceptance threshold. B's continuation cannot write before A's actual commit, even on baseline cancelled-await bugs, avoiding artificial crossrun corruption.

## Candidate/dependency and validation evidence

Fresh-process view loads the child file as real `agent.service` using importlib, then updates root package exports before pytest imports. Root supplies dependencies/tests. `green-final-candidate-import.json` and fixture/SQL frames prove candidate path+SHA07fce33132f203e1bbdf7a07f029034b81927ac46568fd254ebebb9a1cf0bba1. GREEN did not accidentally execute unchanged root service. Root prerequisites remain exact; root R5 sse_pump drift is separately attributed, not synchronized.

Affected offline root service/session-id/steering/memory/Redis-contract/session-loader/stream-hardening batch:158 PASS,1 existing failing fixture. The same single fixture fails against frozen pre-repair service. Its events carry no recorded assistant payload, so the user-only append branch executes; its MessageManager.save_messages fault mock is not exercised. Coordinator subsequently corrected only its root test definition and reported2PASS in m07-service-error-fixture; this lane preserves its original receipt and records later drift without sync/rerun. The owned real-SQL counterpart proves the intended error/no-done contract. This offline batch is not claimed fully GREEN.

Scoped actual candidate service coverage:384/453 statements, **84.77% >= unchanged80**. Ruff and in-memory compile/AST PASS. Installed mypy Counter comparison:11 before/11 after, no added/removed diagnostics; not global backend mypy GREEN. Existing venv lacked mypy module; existing Homebrew mypy used with the venv interpreter, no installation.

Evidence `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/delete/m07-finalization-repair` contains exact commands/exits/XML, raw SQL/source/thread/PID/ownership traces, phase-only diff, baselines/final snapshots/manifests, coverage, known harness corrections and cleanup. Four verified-absent leased PG14.22 UTF8/template0/C databases all dropped normally after zero connections; catalog0/sharedhealth1, no force. Both test DB URLs were set before imports. Current positively owned SQLite/global-conftest resources and cache/temp directories are cleaned; old unknown tempfile gap is untouched. Local PG14 is not CI15.

## Limits and handoff

Tail steering arriving after the final drain while SQL runs remains the existing best-effort WATCH. No atomic stop-accepting stage or universal at-most-once promise is added. Real Redis server/distributed lifecycle, process shutdown cancellation of independently owned tasks and outer HTTP/SSE pump ownership are not proven here; Redis tests are existing offline contracts. Root owns R2/R3/R5/API/permission/feedback/SSE work and integration. No source outside this lane changed; all1,741 child unowned hashes and prior map/proof artifacts remain exact. Source/tests/doc are uncommitted.
