# M16 Code clipboard follow-up — native frontend

Handler-only candidate ready for root SOURCE review. Exact fresh root/child Code baseline matched; prior M16/M08 evidence remains untouched. Actual copy click: **2 genuine RED /1 healthy PASS →3 GREEN**; new+existing Code suites **13 PASS**. Lint0 and QA types0 (actual Code imports) pass.

`CodeManagement.tsx:226` now awaits clipboard.writeText inside try/catch. Only after resolution does existing codes.copied toast run; rejection uses existing en/zh common:operationFailed. No new translations/clipboard policy/dependency/cancellation. All source bytes outside this function are identical to baseline, including UTC parser and root header locks. Existing Code test unchanged.

Proof uses actual page/real Query/I18nextProvider/local resources/default repo setup, controlled clipboard/API and notification boundaries, explicit native Storage. No system clipboard/network. One exact FAKE-CODE-A write/click. Pending success count was1 before resolution, now0; after success the same English message appears once. Rejection formerly showed success1/error0 and emitted1 process unhandled rejection, now success0/error1/unhandled0. Healthy Chinese copied string/message preserved. No skips/weak assertions.

Authoritative RED: red-observed receipt/log/json and frozen red-test/. Two earlier probes demonstrated timing/false-toast RED but their vi.fn returned-Promise settlement handlers masked rejection emissions (installed @vitest/spy index.js:361). Final clipboard boundary is a plain recording function returning a native async Promise, with test-owned process observer removed after each case; no generic error interception. The final test bytes equal valid RED bytes.

Commands in commands.json/individual receipts (child apps/web, existing Node25.9.0, NODE_OPTIONS no-experimental-webstorage/max-old-space-size8192):
- Vitest run with evidence vitest.config.mjs/configLoader native: green exit0,2 suites13 PASS; red-observed exit1,2 failed1 PASS.
- ESLint Code source/new test --max-warnings0: exit0.
- Forced tsc-b evidence tsconfig.qa.json: final exit0. Initial exit1 was missing Node process declarations in evidence config; explicit existing @types/node root fixed only that QA config, no installs/product/test changes.

Caches/build-info/envDir:false are evidence-only. New runtime assertions exercise both handler branches; no new full-source coverage/type/build claim. Unchanged prior M16 six-suite coverage79.08L/79.91B/67.46F/77.75S/source-type receipt is historical context, not a rerun of this handler candidate. App build remains root-consolidated.

Cleanup: deferred writes settled, components unmounted, queries canceled/cleared, process observer count restored, Storage/fetch/clipboard getter/mocks/i18n listeners restored; zero unexpected fetch/remaining Query entries. Three GREEN observations have zero unhandled emissions. No physical clipboard/permission/full-App/provider/server/DB/Actions/Git operation.

Baseline/source-phase.diff/owned-phase.diff/final-source/final-manifest.json give exact three-path handoff, never whole HEAD. Root independently reviews/integrates. Follow-up complete; M18 continuation is read-only and separate.
