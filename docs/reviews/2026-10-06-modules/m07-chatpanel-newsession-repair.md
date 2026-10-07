# M07 R6 ChatPanel new-session ownership repair

Attributed same standalone native frontend lane, direct solo. Independent DESIGN_CLEAR supplied by root; this is a bounded SOURCE candidate for independent review/integration. Root remains read-only. No M07/all23/release closure; no backend/physical-browser claim. The accepted original14-case proof and its67 entries plus manifest remain immutable.

## Outcome and exact ownership

**22 ordinary lifecycle cases pass under both native evidence config and actual repository Vitest config/setup**: original14 plus8 new scoped cases, default and one actual outer rootStrictMode. Clean pre-source matrix22=12 genuineRED+10 passingcontrols; no skip/fails/xfail/todo. Five existing affected suites178PASS. Both scopedcoverage commands run200ordinarytestsPASS. Source+QA noEmit and maxwarnings0lint exit0 after evidence-config correction. Appbuild deliberately deferred to root consolidation; no fresh build claimed.

**Coverage caveat:** ChatPanel-only gate fails unchanged68/55/61/66 thresholds (65.76L/52.2B/67.5F/64.9S). The explicitly requested ChatPanel/useChatStreaming/useAgentStream scope passes unchanged global thresholds (77.54L/60.87B/82.59F/75.54S). Both receipts/coverage artifacts retained; this is not an all-gatesGREEN or isolatedChatPanelcoverageGREEN claim. No threshold relaxed, file excluded from that scope or assertion weakened; no unrelated handler tests added to fill coverage.

Only candidate paths:

- `apps/web/src/components/ChatPanel.tsx`: baseline74901bytes/SHA256`bd2b9f993a795ae4792c987f489acf44e026596f7c31c6b2f4d36580bca3bbb2` → candidate75380bytes/SHA256`f7acd10cca69241a1dc227c97bacd16d59348590bac6b79668d01195e2f51296`.
- `apps/web/src/components/__tests__/ChatPanel.newSessionLifetime.test.tsx`: acceptedproof baseline18288bytes/SHA256`8e3d46ec5902e19e1e74674b200d5d2c1f21692aac5c9d1508d83de7910ff24a` → candidate23078bytes/SHA256`6e65d90834a07972723de3648f23b604639c05593b30d5b6e20aa2062f9ca9ce`. Old frozen snapshot unchanged; current owned test gains ABA/unmount/loader/failure recovery fixtures.
- `docs/reviews/2026-10-06-modules/m07-chatpanel-newsession-repair.md`: new attributed review; exact digest/bytes in final-source-manifest.json (not self-referential).

All other writes solely under `/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair`. No prerequisite/product copy from root:20 pertinent paths rootexact at phase start. Root C8 service changes unrelated, not synchronized. No hook/API/context/cache/globalframework/dependency/config/retention source edit.624 guarded unowned child paths unchanged; all seven prior evidence directories including acceptedR6proof exact. Earlier root-only test adapters or auth diffs outside reviewed scope remain attributed, not permission to sync.

## Grounded reproduction and minimum approved repair

Real ChatPanel/MessageList/MessageInput/LazyMarkdown/QuotaBadge, useChatStreaming/useAgentStream/useDraftPersistence, actual chatApi/agentApi/apiClient history/new-session/SSE path, public real Project/Auth/mobile/material/quote/skill providers, realQueryClient/locali18next/MemoryRouter. Workbench calls real projectcommand on actual `/project/:projectId` route transitions; conditional panel hide/show is actual unmount. Only fake local HTTP/SSE and peripheral analytics boundary. Default plusONE outer rootStrict replay. Test self-installs nativeStorage after ordinary setup's Storage mock; preimport hoisted localefetch fence prevents real globali18n XHR. AllAPI/identity/project/token values fake. This is actual component/provider/router proof, not fullApp/Layout/auth/production topology.

Accepted14-case proof established A success erases currentB history/usesAcache; A error overwritesBsuggestions; old sameA history repopulates afternewsession; oldA success aborts realB frontendstream. New pre-source cases independently show A→B→A old completion erases returnedA history and uncached old-panel completion issues one extraA suggestPOST. Owned current new-session success leaves initial-history loader/input disabled until oldGET finishes. Original healthy A, warmed unmount replacement, currenterror/retry controls retained; new newerBloader and failedcurrentcreate/pendinghistory recovery controls pass before repair.

The only product changes are:

1. `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web/src/components/ChatPanel.tsx:438` newSessionRequestSeqRef; `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web/src/components/ChatPanel.tsx:470` existing projectref effect setup retains currentproject. Its cleanup nulls that ref and increments new-session sequence on projectchange/actualunmount/Strictreplay. No mountedboolean/framework/hook contract.
2. `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web/src/components/ChatPanel.tsx:1367` reserves projectId/requestSeq synchronously before createawait. `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web/src/components/ChatPanel.tsx:1384` requires currentproject+same sequence before any completion messages/feedback/stream/skills/reset/suggestions. ServerPOST remains valid and finishes; no cancel/undo/notificationpolicy change. Catch logging unchanged; only fallback presentation requires ownership at1396.
3. Owned success first invalidates history sequence at1385 and settles historyloading=false at1386, then preserves existing clear/reset/requestInitialSuggestions(projectId,[]) path. It cannot settle a newerBloader because its ownership fence precedes invalidation/loading mutation.
4. `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web/src/components/ChatPanel.tsx:983` loadChatHistory returns `Message[] | null`; success991/catch1020 require both project+latesthistory request. Stale/invalidated returnsnull, validempty/currenterror remains[]. Existing current-error logging and finallyownership retained. Caller1096 stops suggestion work fornull. No independent suggestioncache rewrite.

`semantic-preservation.json` reverses only those exact bounded regions and reproduces baseline ChatPanel bytes. Other function bodies/regions, existing finally, stream/hooks/API/provider logic remain exact. Prior M03 summary/partialcompletion and feedback code preserved; no broader R1 finalization repair inferred.

## Regression matrix and observed cost

The22 ordinary cases cover11 scenarios×default/rootStrict:

- ordinary currentA success;
- oldAsuccess/currentB history+suggestions;
- oldAerror/currentB suggestionpresentation;
- ownnewsession/oldinitialhistory plus input-loader settlement;
- actual warmed unmount/replacement;
- oldAsuccess/actualactiveBstream;
- ABA returnedA history;
- actual uncached unmount/replacement with no extraA request;
- obsoleteA cannot settle newerB loader; Bcompletion restores input;
- failedcurrentcreate preserves pendingownhistory recovery;
- current500/history/fallback then legitimate retry.

Clean pre-source12REDs are the6 obsolete/loader scenarios×2;10 compatibility controls passed. After all22PASS. The original sameA old-history assertion remains present alongside strengthened loader expectation; raw snapshots independently record repopulation. Requests and issuedvalidAPOST unchanged except prevented obsoleteuncachedA suggest. Bstream abortsignal false→true beforeteardown inRED becomes false→false afterfix; actual currentowner cleanup later aborts normally and is distinguished in snapshots. Broute/history/rendered content/cache/fallback are asserted actual DOM.

| Scenario | Default fake API requests before→after | RootStrict before→after |
|---|---:|---:|
| ordinary current A new session clears A history using enabled toolbar and actual API | 7→7 | 8→8 |
| retains real B history and B suggestions after pending A new session succeeds | 9→9 | 10→10 |
| retains current B suggestion presentation after pending A new session fails | 9→9 | 10→10 |
| does not repopulate old same-project history after new session succeeds during initial history load | 7→7 | 8→8 |
| actual old panel unmount and B replacement retain B after A completion | 10→10 | 11→11 |
| does not abort or erase the actual B stream when pending A new session succeeds | 10→10 | 11→11 |
| retains returned A history after A to B to A supersedes pending new session | 10→10 | 11→11 |
| does not request uncached A suggestions after actual old panel unmount | 11→10 | 12→11 |
| obsolete A completion cannot settle a newer B history loader | 9→9 | 10→10 |
| failed current creation retains pending own history recovery | 7→7 | 8→8 |
| current A new-session failure preserves history and ordinary retry succeeds | 8→8 | 9→9 |

Every single-create scenario retainsonePOST; failure/retry has2. The uncached actual-unmount case's A suggest count2→1 removes only the obsolete follow-up (total11→10 default/12→11 Strict). No network request cancellation/new backend policy.19 fake startupnamespace loads separate; Strict's extra background `/me` belongs to realAuth replay. Captured localheaders/form/payload/title/status/routes/SSEsignals in observations.json; projections in before-after-measurements.json. No productionp95/SQL/durabledata claim; no generic perfoptimization or frontendframework rewrite.

## Fresh commands, gates and invalid receipts

Cwd `/private/tmp/zenstory-m04-native-frontend-20261006/apps/web`. Node **v25.9.0**, CI20gap explicit. Existing `node_modules` symlink to installed `/Users/pite/makemoney/zenstory/apps/web/node_modules`; no installs/download/writes. `NODE_OPTIONS=--no-experimental-webstorage`, native configLoader/envDir=false/evidencecache, source/QA tsBuildInfo paths evidence-only. Repository config/setup imported directly by ordinary wrapper; no testsetup/globalconfig mutation.

| Receipt | Actual child exit | Exact command |
|---|---:|---|
| acceptance-native-red | 1 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.evidence.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/acceptance-native-red.json` |
| acceptance-default-red | 1 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.default-setup.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/acceptance-default-red.json` |
| acceptance-clean-native-red | 1 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.evidence.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/acceptance-clean-native-red.json` |
| acceptance-clean-default-red | 1 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.default-setup.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/acceptance-clean-default-red.json` |
| native-green | 0 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.evidence.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/native-green.json` |
| default-green | 0 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.default-setup.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/default-green.json` |
| affected-existing | 0 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.affected.config.mjs --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/affected-existing.json` |
| source-types | 2 | `node node_modules/typescript/bin/tsc -p /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/tsconfig.source.json --noEmit` |
| affected-lint | 0 | `node node_modules/eslint/bin/eslint.js src/components/ChatPanel.tsx src/components/__tests__/ChatPanel.newSessionLifetime.test.tsx --max-warnings 0` |
| qa-types | 0 | `node node_modules/typescript/bin/tsc -p /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/tsconfig.qa.json --noEmit` |
| scoped-coverage | 1 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.coverage.config.mjs --coverage --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/scoped-coverage.json` |
| source-types-corrected | 0 | `node node_modules/typescript/bin/tsc -p /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/tsconfig.source.json --noEmit` |
| affected-scope-coverage | 0 | `NODE_OPTIONS=--no-experimental-webstorage node node_modules/vitest/vitest.mjs run --configLoader native --config /private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/vitest.coverage-affected.config.mjs --coverage --reporter verbose --reporter json --outputFile.json=/private/tmp/zenstory-module-audit-evidence-20261006/parallel-native-tmux/frontend/m07-chatpanel-newsession-repair/affected-scope-coverage.json` |

Runner outerexit0 is receipt wrapper only; actual childexit above controls classification. First acceptance-native-red/default-red22=12fail10pass included2 afterEach unsettledgate assertion failures because loader expectation fired before oldGETrelease; those **cleanup assertions are fixture failures, not productRED**. Original receipts and pre-source-test preserved. Test moved the same loader expectation after capturing pre-release disabledstate and settling oldGET, retaining both loader+oldhistory assertions. Clean repeat native/default22=12genuineRED10PASS, everygate/timer cleanupzero before source changes. No unknownendpoint/selector/bootstrap failure countedRED. No skipped/weakened acceptance.

Initial source-types exit2 is evidenceconfig TS2688 cannotfindvite/client from explicit@typestypeRoots outsideapps/web. Invalidconfig preserved; corrected source config explicitly includes existinginstalledvite/client.d.ts with empty `types` list instead of failed named lookup. Strict source noEmit still imports actual currentChatPanel/dependencygraph; QA additionally compiles actual ownedtest and source dependencies with installedNode types. Correctedsource exit0,QA0,lint0. No product diagnostic suppression.

Five existing suites: ChatPanel.test.tsx (interface history, not itself actual renderer proof), ChatPanel.mount.test.tsx, ChatPanel.projectSwitch.test.tsx, useChatStreaming.test.ts, useAgentStream.test.ts.178PASS; existingact warnings retained, not hidden or changed. Scoped6suites200PASS, but ChatPanel-only coverage exit1 is genuinegate miss, not fixture invalid. Three-product affected scope exit0 uses the same200cases, explicitly listedmodifiedChatPanel + actual reset/clear dependency hooks. IndividualChatPanelgap persists, no sourceclosure inference from aggregate.

## Ownership, cleanup and remaining limits

Phase-start exact snapshots/hashes and endmanifests; product-only.diff and fullowned source-phase.diff compare **phase baseline**, never entire dirtyHEAD. Newdoc /dev/null. Final-source exact3paths.624 unownedpaths preserved,20rootreviewedpaths stable; childonlyChatPanel changes within those20. Seven priorproof/repairdirs unchanged (67acceptedR6entries+manifest, fullpriorC6/C1/C7/C2). Root unrelatedC8/M23 currentwork protected, no broadtotalrootdriftclaim. Productguard and frozen evidence checked again atfreeze.

All22 finalobservations per setup: unsettledAtAssertion0,pending0,unexpected[],remainingTimers0,timersAfterClear0,dialogs0,bodyOverflowRestored=true. Fullactualroot unmount before ownedQuery cancel/clear; SSEreader closed/listenerdisposed; routerunsubscribe/dispose; locali18n listeneroff; globals/Storage refs restored/asserted; native authStorage/refreshledger and local/session cleared; original fetch/real timers restored. Drain unused completedsuggestion Promise.race timeouts2000ms afterunmount, then timers0 beforefixtureclear. All positivelyowned commands exited. No server/browser/DB/SDK/providerresource created, no unowneddelete/cleanup, no secrets/.secret/environmentfiles read.

Remaining gaps: ChatPanel-only coverage below3 unchangedthresholds; Appbuild deferred rootaggregate; physicalbrowser/fullApp/featureflag/auth topology unrun; backend commit/cancellation/finalization/retention/durable outcomes unclaimed. Sameproject simultaneousinvocation/suggestion asyncordering beyond measuredABA, normal unmount of arbitrary historywork/wholequery UX, error notification policy beyond preservedlogging and fakecurrentfallback remain bounded WATCHs. No extra hookAPI/cache cancellation framework added. Root owns independent SOURCE approval/integration/all23/release. NoActions/remotegit/push/PR/merge/deploy/provider/mainCI investigation.

SOURCE candidate ready with disclosedcoveragegap; same lane idle after frozen handoff.
