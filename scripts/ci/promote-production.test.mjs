import assert from "node:assert/strict";
import test from "node:test";

import {
  selectExactSuccessfulRun,
  validateAncestorComparison,
  validatePromotionInputs,
  validateTagEventSource,
  validateUnchangedReleaseRef,
} from "./promote-production.mjs";

const oldSha = "a".repeat(40);
const sourceSha = "b".repeat(40);

test("production promotion accepts only the fixed repository, tag namespace, and release branch", () => {
  assert.doesNotThrow(() => validatePromotionInputs({
    repository: "zenstory-ai/zenstory",
    tagName: "prod-v2026.10.07",
    releaseBranch: "release-production",
    expectedSourceSha: sourceSha,
  }));
  for (const input of [
    { repository: "fork/zenstory", tagName: "prod-v1", releaseBranch: "release-production" },
    { repository: "zenstory-ai/zenstory", tagName: "v1", releaseBranch: "release-production" },
    { repository: "zenstory-ai/zenstory", tagName: "prod-v1", releaseBranch: "main" },
  ]) {
    assert.throws(() => validatePromotionInputs({expectedSourceSha: sourceSha, ...input}));
  }
});

test("ancestor comparison permits only an identical or fast-forward target", () => {
  assert.doesNotThrow(() => validateAncestorComparison({ status: "ahead", base_commit: { sha: oldSha } }, oldSha, "release-production"));
  assert.doesNotThrow(() => validateAncestorComparison({ status: "identical", base_commit: { sha: oldSha } }, oldSha, "release-production"));
  assert.throws(
    () => validateAncestorComparison({ status: "diverged", base_commit: { sha: oldSha } }, oldSha, "release-production"),
    /must contain/,
  );
  assert.throws(
    () => validateAncestorComparison({ status: "ahead", base_commit: { sha: sourceSha } }, oldSha, "release-production"),
    /base mismatch/,
  );
});

test("promotion selects one exact successful main-push workflow run", () => {
  const expected = {
    id: 17,
    head_sha: sourceSha,
    head_branch: "main",
    event: "push",
    status: "completed",
    conclusion: "success",
    path: ".github/workflows/e2e.yml@main",
  };
  assert.equal(selectExactSuccessfulRun({
    workflowPath: ".github/workflows/e2e.yml",
    sourceSha,
    runs: [expected, { ...expected, id: 18, event: "workflow_dispatch" }],
  }), expected);
  assert.throws(() => selectExactSuccessfulRun({
    workflowPath: ".github/workflows/e2e.yml",
    sourceSha,
    runs: [expected, { ...expected, id: 18 }],
  }), /found 2/);
  assert.throws(() => selectExactSuccessfulRun({
    workflowPath: ".github/workflows/e2e.yml",
    sourceSha,
    runs: [{ ...expected, conclusion: "failure" }],
  }), /found 0/);
});

test("release ref reread closes a changed-baseline update", () => {
  const before = { ref: "refs/heads/release-production", object: { sha: oldSha } };
  assert.equal(validateUnchangedReleaseRef(before, structuredClone(before), "release-production"), oldSha);
  assert.throws(
    () => validateUnchangedReleaseRef(before, { ...before, object: { sha: sourceSha } }, "release-production"),
    /changed during promotion/,
  );
});

test("promotion binds the tag to the immutable triggering event commit", () => {
  assert.doesNotThrow(() => validateTagEventSource(sourceSha, sourceSha));
  assert.throws(() => validateTagEventSource(oldSha, sourceSha), /tag moved/);
  assert.throws(() => validatePromotionInputs({ repository: "zenstory-ai/zenstory", tagName: "prod-v1", releaseBranch: "release-production", expectedSourceSha: "bad" }), /event source SHA/);
});
