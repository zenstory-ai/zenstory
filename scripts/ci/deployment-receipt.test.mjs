import assert from "node:assert/strict";
import test from "node:test";

import {
  validateDeploymentCiProof,
  validateDeploymentE2eProof,
  validateRunStability,
} from "./deployment-receipt.mjs";

const sourceSha = "a".repeat(40);
const input = {
  repository: "zenstory-ai/zenstory",
  sourceSha,
  requestedRunId: "42",
  workflow: { id: 7, path: ".github/workflows/ci.yml" },
  run: {
    id: 42,
    workflow_id: 7,
    path: ".github/workflows/ci.yml",
    head_sha: sourceSha,
    head_branch: "main",
    event: "push",
    status: "completed",
    conclusion: "success",
    run_attempt: 2,
    check_suite_id: 8,
  },
  jobs: [{ name: "ci-summary", status: "completed", conclusion: "success" }],
  checkSuite: { id: 8, head_sha: sourceSha, status: "completed", conclusion: "success", app: { id: 15368 } },
};

test("deployment receipt binds a successful ci-summary to an exact main source and attempt", () => {
  assert.deepEqual(validateDeploymentCiProof(input), {
    schemaVersion: 1,
    repository: "zenstory-ai/zenstory",
    sourceSha,
    workflowId: 7,
    workflowPath: ".github/workflows/ci.yml",
    runId: 42,
    runAttempt: 2,
    checkSuiteId: 8,
    checkSuiteAppId: 15368,
    requiredJobs: ["ci-summary"],
  });
});

test("deployment receipt rejects source, event, workflow, app, and summary mismatches", () => {
  const cases = [
    [{ run: { ...input.run, head_sha: "b".repeat(40) } }, /source SHA mismatch/],
    [{ run: { ...input.run, event: "pull_request" } }, /main push run/],
    [{ workflow: { id: 7, path: ".github/workflows/e2e.yml" } }, /workflow path mismatch/],
    [{ checkSuite: { app: { id: 1 } } }, /not GitHub Actions/],
    [{ jobs: [{ name: "ci-summary", status: "completed", conclusion: "failure" }] }, /did not succeed/],
    [{ jobs: [] }, /missing or ambiguous/],
  ];
  for (const [patch, expected] of cases) {
    assert.throws(() => validateDeploymentCiProof({ ...input, ...patch }), expected);
  }
});

test("E2E receipt binds the exact source to the e2e-summary workflow contract", () => {
  const e2e = {
    ...input,
    workflow: { id: 9, path: ".github/workflows/e2e.yml" },
    run: {
      ...input.run,
      workflow_id: 9,
      path: ".github/workflows/e2e.yml@main",
      check_suite_id: 10,
    },
    jobs: [{ name: "e2e-summary", status: "completed", conclusion: "success" }],
    checkSuite: { ...input.checkSuite, id: 10 },
  };
  assert.deepEqual(validateDeploymentE2eProof(e2e), {
    schemaVersion: 1,
    repository: "zenstory-ai/zenstory",
    sourceSha,
    workflowId: 9,
    workflowPath: ".github/workflows/e2e.yml",
    runId: 42,
    runAttempt: 2,
    checkSuiteId: 10,
    checkSuiteAppId: 15368,
    requiredJobs: ["e2e-summary"],
  });
  assert.throws(
    () => validateDeploymentE2eProof({ ...e2e, jobs: input.jobs }),
    /missing or ambiguous e2e-summary/,
  );
});

test("readiness proof validates suite identity and closes a same-run attempt race", () => {
  for (const patch of [{id: 9}, {head_sha: "b".repeat(40)}, {status: "in_progress"}, {conclusion: "failure"}]) {
    assert.throws(() => validateDeploymentCiProof({...input, checkSuite: {...input.checkSuite, ...patch}}), /suite/);
  }
  assert.throws(() => validateRunStability(input.run, {...input.run, run_attempt: 3, status: "in_progress", conclusion: null}), /rerun|changed/);
});
