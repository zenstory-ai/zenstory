import assert from "node:assert/strict";
import * as controls from "./check-workflow-results.mjs";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { ciJobExpectations, validateCiSummary, validateE2eSummary } from "./check-workflow-results.mjs";

const irrelevantJobs = {
  "frontend-lint": "skipped",
  "ci-lint": "skipped",
  "backend-test": "skipped",
  "backend-coverage": "skipped",
  "backend-integration": "skipped",
  "prefect-compatibility": "skipped",
  "production-images": "skipped",
  "frontend-test": "skipped",
  "frontend-build": "skipped",
  "cli-test": "skipped",
  "vercel-build": "skipped",
};

test("CI permits skipped jobs only when their validated scope is irrelevant", () => {
  assert.deepEqual(
    validateCiSummary({
      detector: "success",
      outputs: { backend: "false", frontend: "true", cli: "false", ci: "false" },
      jobs: {
        ...irrelevantJobs,
        "frontend-lint": "success",
        "frontend-test": "success",
        "frontend-build": "success",
        "vercel-build": "success",
      },
    }).scopes,
    { backend: false, frontend: true, cli: false, ci: false },
  );
});

test("CI fails closed on detector failure even when every downstream job skipped", () => {
  assert.throws(
    () => validateCiSummary({
      detector: "failure",
      outputs: { backend: "", frontend: "", cli: "", ci: "" },
      jobs: irrelevantJobs,
    }),
    /detect-changes did not succeed/,
  );
});

test("CI rejects malformed scope outputs and a skipped relevant job", () => {
  assert.throws(
    () => validateCiSummary({
      detector: "success",
      outputs: { backend: "", frontend: "false", cli: "false", ci: "false" },
      jobs: irrelevantJobs,
    }),
    /backend output must be true or false/,
  );
  assert.throws(
    () => validateCiSummary({
      detector: "success",
      outputs: { backend: "false", frontend: "true", cli: "false", ci: "false" },
      jobs: irrelevantJobs,
    }),
    /frontend-lint was required but finished as skipped/,
  );
});

test("CI workflow changes require every conditional gate", () => {
  const successJobs = Object.fromEntries(Object.keys(irrelevantJobs).map((name) => [name, "success"]));
  assert.doesNotThrow(() => validateCiSummary({
    detector: "success",
    outputs: { backend: "false", frontend: "false", cli: "false", ci: "true" },
    jobs: successJobs,
  }));
  assert.throws(
    () => validateCiSummary({
      detector: "success",
      outputs: { backend: "false", frontend: "false", cli: "false", ci: "true" },
      jobs: { ...successJobs, "cli-test": "cancelled" },
    }),
    /cli-test was required but finished as cancelled/,
  );
});

test("backend changes require the coverage gate and integration lane, not only the test shards", () => {
  const backendJobs = {
    ...irrelevantJobs,
    "backend-test": "success",
    "backend-coverage": "success",
    "backend-integration": "success",
    "prefect-compatibility": "success",
    "production-images": "success",
  };
  const outputs = { backend: "true", frontend: "false", cli: "false", ci: "false" };
  assert.doesNotThrow(() => validateCiSummary({ detector: "success", outputs, jobs: backendJobs }));
  // A failed shard skips the combine job; the gate must not accept that skip.
  assert.throws(
    () => validateCiSummary({ detector: "success", outputs, jobs: { ...backendJobs, "backend-coverage": "skipped" } }),
    /backend-coverage was required but finished as skipped/,
  );
  assert.throws(
    () => validateCiSummary({ detector: "success", outputs, jobs: { ...backendJobs, "backend-integration": "failure" } }),
    /backend-integration was required but finished as failure/,
  );
});

test("every CI job is a ci-summary dependency with a validated result", async () => {
  const ci = await readFile(".github/workflows/ci.yml", "utf8");
  const jobs = [...ci.slice(ci.indexOf("\njobs:")).matchAll(/^  ([a-z0-9-]+):$/gm)]
    .map((match) => match[1])
    .filter((name) => name !== "detect-changes" && name !== "ci-summary")
    .sort();
  const validated = Object.keys(ciJobExpectations({ backend: true, frontend: true, cli: true, ci: true })).sort();
  assert.deepEqual(jobs, validated, "a CI job outside the validator would be silently ungated");

  const summary = ci.slice(ci.indexOf("\n  ci-summary:"));
  const needs = summary.match(/needs: \[([^\]]+)\]/)[1].split(",").map((name) => name.trim()).sort();
  assert.deepEqual(needs, ["detect-changes", ...validated].sort());
  for (const name of validated) {
    assert.match(summary, new RegExp(`: \\$\\{\\{ needs\\['${name}'\\]\\.result \\}\\}`), `${name} result is not passed to the validator`);
  }
});

test("scheduled and manual E2E runs are forced regardless of changed paths", () => {
  for (const eventName of ["schedule", "workflow_dispatch"]) {
    assert.equal(validateE2eSummary({
      detector: "success",
      outputs: { backend: "false", frontend: "false", ci: "false" },
      eventName,
      jobResult: "success",
    }).forced, true);
    assert.throws(
      () => validateE2eSummary({
        detector: "success",
        outputs: { backend: "false", frontend: "false", ci: "false" },
        eventName,
        jobResult: "skipped",
      }),
      /e2e-test was required but finished as skipped/,
    );
  }
});

test("push E2E permits skip only when every validated scope is false", () => {
  assert.doesNotThrow(() => validateE2eSummary({
    detector: "success",
    outputs: { backend: "false", frontend: "false", ci: "false" },
    eventName: "push",
    jobResult: "skipped",
  }));
  assert.throws(
    () => validateE2eSummary({
      detector: "success",
      outputs: { backend: "true", frontend: "false", ci: "false" },
      eventName: "push",
      jobResult: "failure",
    }),
    /e2e-test was required but finished as failure/,
  );
});

test("manual release recovery binds the event to the immutable main SHA and a full E2E lane", () => {
  const input = { eventName: "workflow_dispatch", ref: "refs/heads/main", sourceSha: "a".repeat(40), expectedSourceSha: "a".repeat(40) };
  assert.doesNotThrow(() => controls.validateRecoverySource(input));
  assert.doesNotThrow(() => controls.validateRecoverySource({ ...input, suite: "default" }));
  assert.doesNotThrow(() => controls.validateRecoverySource({ ...input, suite: "full" }));
  for (const patch of [
    { eventName: "push" }, { ref: "refs/heads/develop" }, { ref: "refs/tags/main" },
    { expectedSourceSha: "" }, { expectedSourceSha: "a" }, { expectedSourceSha: "b".repeat(40) },
    { sourceSha: "main" }, { suite: "smoke" }, { suite: "release" }, { suite: "nightly" },
  ]) assert.throws(() => controls.validateRecoverySource({ ...input, ...patch }), /recovery|source|main|suite/i);
});

test("manual CI recovery forces all scopes without trusting empty or false diff outputs", () => {
  for (const value of ["", "false", "true"]) {
    assert.deepEqual(controls.resolveCiScope({ eventName: "workflow_dispatch", filters: { backend: value, frontend: value, cli: value, ci: value } }),
      { backend: true, frontend: true, cli: true, ci: true });
  }
  assert.deepEqual(controls.resolveCiScope({ eventName: "push", filters: { backend: "true", frontend: "false", cli: "false", ci: "false" } }),
    { backend: true, frontend: false, cli: false, ci: false });
  assert.throws(() => controls.resolveCiScope({ eventName: "push", filters: { backend: "", frontend: "false", cli: "false", ci: "false" } }), /true or false/);
});
