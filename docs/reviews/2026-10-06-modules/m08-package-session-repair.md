# M08 C1 skill package 401 session repair — bounded native handoff

Candidate ready for independent root SOURCE review; uncommitted. This is one private frontend package-fetch callsite, not M08/all23/release closure. Authoritative root was read-only; prior M08/R6/M19/M01 handoffs remain untouched.

## Regression-first result and minimum repair

Actual `skillsApi.importSkill` / `exportSkill` call private `fetchSkillPackage` in `apps/web/src/lib/api.ts`. The untouched helper re-read current access on recursive replay and invoked the real shared refresh primitive without checking its request's captured pair. A delayed A401 could refresh B and replay A's package under B credentials. For export the fixture downloaded B-backed bytes. Confirmed A0→A1→A2 lineage also incurred unnecessary A3 refresh. These are real helper/client executions with fake HTTP, not a modeled auth generation or production corruption claim.

Baseline root/child api.ts already matched: SHA256 `52fa01c298166b0196851bacb943d2bcc87feb37baf182211de0b3f89750aa50`, 62593 bytes; no prerequisite sync needed. Before product edit, the same 37 ordinary assertions ran with **12 genuine RED /25 passing controls** under native config and repo-default setup. Frozen red-test snapshot is byte-identical to final test; no skip/fails/early-return or weakened expectations.

Conditional authorized design was applied only after both valid RED receipts. Candidate `api.ts:32,1287` adds the existing readonly `resolveOwnedAuthSession` import, captures entry access/refresh before first await, and replaces recursive late-token fetch with an explicit-token `fetchOnce`. First401 refreshes only exact owned pair; an already-confirmed descendant replays directly; after actual refresh await re-resolve original ownership. Missing/unowned pair or failed refresh keeps original response for existing ApiError decoder; retry401 does not refresh again. No additional clear, AbortError policy or 200 response-affinity change.

**All bytes outside this helper and that import addition are exact** (`source-preservation.json`); export/import bodies, ordinary JSON methods and all other API domains unchanged. No apiClient/AuthProvider/backend/session registry changes. Existing feedback/admin wrapper products and both existing API tests unchanged (five-path `dependency-preservation.json`).

## Actual observations and compatibility

New suite uses actual wrappers, actual apiClient/refresh persistence/lineage, real happy-dom Storage installed before each test under ordinary repo setup. Actor replacement is a **controlled Storage command**, with actual clearAuthStorage in logout cases; this does not prove App/AuthProvider identity navigation. Only fetch and browser download side effects are intercepted; no helper/client/storage-function mocks, no real endpoint/network/provider/browser.

| Scenario, each import/export | Before | After |
|---|---|---|
| Delayed A401 →direct B or logout+B | 3 requests: A + B refresh + B replay; import returns B result/export downloads B bytes; B pair rotates | 1 A request, original ApiError401/details, no B refresh/replay/download, B unchanged |
| Another real refresh awaiter establishes B before replay | 3 requests: A + owned A refresh + B replay/download | 2 requests: A + A refresh, original401; B unchanged |
| Same access but arbitrary replacement refresh | B refresh/replay, replacement result | Original401; no B work/storage effect |
| Missing entry access but refresh present | Refresh/replay, 3 requests | Original401, 1 request; partial storage unchanged |
| A0 held; two actual successful rotations A1 then A2 | 5 requests including unnecessary A3 refresh | Exact4: initial A0 + two real rotations + A2 replay; no A3 |

The remaining25 controls already passed untouched and remain GREEN: current200 en/fallback-zh, exact own401→A1 one replay, B replacement while refresh awaits response or JSON (shared primitive already fenced), logout-only, missing refresh/both, retry401 without second refresh/extra clear, refresh network/503 retaining tokens and403 clearing own tokens, and export fallback filename. Current FormData object/file identity, one file field, file name/type/UTF8 bytes, endpoint/method/base/language, refresh body token, RFC5987 Chinese filename, blob content, object URL revocation and removed anchor are checked/captured. Current source does not cancel/undo an issued server write.

Native and default logs each contain37 `M08_PACKAGE_OBSERVATION` records with request Bearers, refresh token, payload metadata, outcome, storage, download and cleanup. Parsed records saved separately. Every record reports unexpected endpoints=[], pending=0, unsettled gates=0, remaining download anchors=0. No synthetic timing is production latency.

## Fresh commands and gates

Commands execute in child `apps/web` using installed `node_modules` binaries. `run.py <label> ...` records exact argv, inner command exit, elapsed time and full output in `<label>.receipt.json` /`.log`; `commands.json` aggregates them. NODE_OPTIONS=`--no-experimental-webstorage --max-old-space-size=8192`; envDir=false, native config loader, evidence-only cache/tsbuildinfo/report paths. No environment file read/install/config mutation. Wrapper process may exit0 while recording an expected failing inner command; use receipt exit.

| Command/gate | Inner exit/result |
|---|---|
| Vitest native untouched (`red-native`, native config) | 1: 12 failed/25 passed,37 cases |
| First default-config import (`red-default`) | 1 **invalid startup**, native ESM lacks original config's __dirname; zero tests, not product RED |
| Corrected default config (`red-default-valid`) | 1: same12 failed/25 passed,37 cases |
| Vitest native candidate (`green-native`) | 0:37 PASS |
| Vitest default candidate (`green-default-related`) | 0:156 PASS across new37 + existing api.test62 + apiClient.test57 |
| Vitest scoped coverage over exactly `src/lib/api.ts` (`coverage`) | 0:156 PASS; L69.66 (209/300), B56.47 (109/193), F64.93 (50/77), S68.01 (219/322) |
| `node node_modules/typescript/bin/tsc -b E/tsconfig.source.json --force` | 0; actual candidate production source tree compiled/noEmit, evidence buildinfo |
| `node node_modules/typescript/bin/tsc -p E/tsconfig.qa.json --noEmit` | 0; new test + actual imported source, inherited strict options |
| `node node_modules/eslint/bin/eslint.js src/lib/api.ts src/lib/__tests__/skillsPackage.sessionOwnership.test.ts --max-warnings 0` | 0, no output/warnings |

Coverage thresholds unchanged L68/B55/F61/S66. Whole API file passes; untested other functions remain in denominator, no unrelated test filling or threshold reduction. Existing tests were untouched. App build deliberately deferred to root next consolidation per authorization; no current build/browser/CI/Node20 parity claim.

Default-setup config adaptation is evidence-only: original config body retained with installed absolute imports, explicit root/__dirname/setup paths, envDir=false/cache/include override. Settings, real repo `src/test/setup.ts`, assertions and thresholds unchanged (`default-config-adaptation.json`). The invalid initial startup is preserved. One early evidence-config generator attempted Python JSON parsing of JSONC tsconfig and failed before any compiler invocation; corrected by using a minimal extends config, no source/config change or product RED.

## Ownership, hashes, cleanup and gaps

Exactly three candidate paths:

- `apps/web/src/lib/api.ts`: private helper/import only, 62593→63108 bytes (+515), SHA256 `0dc00a813b998fe823174dcb96dc79a5160c0f9ee85b06d2887b0c230a445fa3`.
- NEW `apps/web/src/lib/__tests__/skillsPackage.sessionOwnership.test.ts`:37 ordinary cases, exact pre-source RED test retained.
- NEW `docs/reviews/2026-10-06-modules/m08-package-session-repair.md`: attributed review.

`final-manifest.json` has exact three source/test/doc hashes and bytes; `final-source/` exact snapshots. `source-phase.diff` compares owned product against immutable phase baseline, not HEAD; `owned-phase.diff` also includes only the two NEW paths. Baseline manifests record the five unchanged relevant dependencies/tests and root/child equality. No whole-branch sync, whole-HEAD patch, huge guard loop or unrelated source mutation.

All deferred responses/body reads and shared refresh awaits settle. Listener removed, actual clearAuthStorage clears ledger, native Storage cleared; mocks/URL/anchor/fetch/global Storage restored and modules reset in afterEach. No real download, timers/server/browser/DB created. Evidence caches/output retained only in owned evidence directory; shared dependencies/cache untouched. Source compiled without shared buildinfo pollution.

Known gaps: no physical-browser/AuthProvider identity/prod API proof; no positive200 cancellation policy or backend write cancellation; no current App build, remote CI/Actions or Node25=CI20 claim. Other M08 candidates remain separate proof/design tasks. Root owns independent SOURCE approval/narrow integration/all23/release. Stop at this verified bounded candidate.
