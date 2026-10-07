# M01 C6: keep retry bound to the original auth lineage

## Architecture / bug / performance

Actual `apiCall` + actual shared `tryRefreshToken` with native Storage and only offline fetch interception reproduced one request-affinity defect: an earlier real-refresh subscriber replaced A credentials with B before apiCall resumed; the old A PUT body was retried as Bearer B. This is not actual server persistence or proof of authorization bypass. The original two-case receipt was one genuine RED/one healthy control. Expanded pre-source receipt is one genuine RED/two controls (healthy rotation and logout). No getter, resolver, auth primitive or API-method mock; no JWT/principal shortcut.

The minimum source change replaces two unchecked storage reads with three lines resolving `resolveOwnedAuthSession` for the original pair. It is unconditional immediately before retry, including the existing already-tracked parallel-rotation branch. Null/missing owned tokens uses the existing 401 without replay or storage clear. The same owned pair feeds retry Authorization and conditional retry-401 cleanup. The pre-existing shared refresh queue, lineage mutation/cooldown, first-401 guard, request body/options, error contracts and every other source byte are unchanged. This reuses the existing readonly helper instead of a new session-generation abstraction; adds only local ownership reads on a rare retry path, no new network/SQL requests.

## Evidence

`/private/tmp/zenstory-module-audit-evidence-20261006/m01-api-retry-affinity/`: expanded-red.json/log, affected.json/log, coverage/coverage-summary.json, source-boundary-proof.json, source-phase.diff, final-manifest.json/final-source, static-exits.json and commands/owned-setup-cleanup.

Fresh affected **60 PASS / two suites / zero failures**; apiClient scope **92.30 lines /86.84 branches /100 functions /91.83 statements**, unchanged68/55/61/66 gate. Existing suite includes delayed already-tracked parallel rotation and replacement after retry dispatch before a retry401, both passed. New logout control preserves absence of credentials and issues no retry. Replacement control preserves B access/refresh/user and issues only original A request. Forced source app types0, new+existing QAtypes0, affected lint0. Prior C2/C4 combined308 and proper-CSS build apply to that prior integration state, not this changed apiClient; final Appbuild after pending F4 integration avoids a redundant intermediate build.

No live server/browser/provider/DB/network payment involved; positively-owned transient setup bytes verified then removed. No Actions/push/PR/merge/deploy. Independent DESIGN and SOURCE APPROVE/CLEAR received; downstream current-root combined189/Apptypes/properCSSbuild gates nowGREEN in m03-dialog-query-performance-integration.md. This phase intentionally supersedes earlier C2 apiClient hash, not the other eleven immutable C2/C3 sources. M01 all-leaf and all23/global quality/release are not claimed.
