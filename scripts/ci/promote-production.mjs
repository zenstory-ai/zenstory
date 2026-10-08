#!/usr/bin/env node

import {
  collectJobs,
  githubJson,
  invariant,
  validateDeploymentCiProof,
  validateDeploymentE2eProof,
  validateRunStability,
} from "./deployment-receipt.mjs";

const shaPattern = /^[a-f0-9]{40}$/;
const productionTagPattern = /^prod-v[0-9A-Za-z][0-9A-Za-z._-]*$/;

export function validatePromotionInputs({ repository, tagName, releaseBranch, expectedSourceSha }) {
  invariant(repository === "zenstory-ai/zenstory", `unexpected repository: ${repository}`);
  invariant(shaPattern.test(expectedSourceSha), "invalid tag event source SHA");
  invariant(productionTagPattern.test(tagName), `invalid production tag: ${tagName}`);
  invariant(releaseBranch === "release-production", `unexpected release branch: ${releaseBranch}`);
}

export function validateAncestorComparison(comparison, baseSha, headLabel) {
  invariant(comparison?.base_commit?.sha === baseSha, `${headLabel} comparison base mismatch`);
  invariant(
    comparison.status === "ahead" || comparison.status === "identical",
    `${headLabel} must contain ${baseSha}; comparison status was ${comparison.status}`,
  );
}

export function selectExactSuccessfulRun({ workflowPath, sourceSha, runs }) {
  invariant(Array.isArray(runs), `unexpected ${workflowPath} runs response`);
  const matches = runs.filter((run) =>
    run?.head_sha === sourceSha &&
    run?.head_branch === "main" &&
    run?.event === "push" &&
    run?.status === "completed" &&
    run?.conclusion === "success" &&
    String(run?.path ?? "").replace(/^\//, "").split("@", 1)[0] === workflowPath,
  );
  invariant(matches.length === 1, `expected one exact successful ${workflowPath} run; found ${matches.length}`);
  return matches[0];
}

export function validateUnchangedReleaseRef(before, reread, releaseBranch) {
  const beforeSha = before?.object?.sha;
  invariant(shaPattern.test(beforeSha), `invalid ${releaseBranch} baseline SHA`);
  invariant(
    reread?.ref === before?.ref && reread?.object?.sha === beforeSha,
    `${releaseBranch} changed during promotion; retry from the current baseline`,
  );
  return beforeSha;
}

export function validateTagEventSource(sourceSha, expectedSourceSha) {
  invariant(sourceSha === expectedSourceSha, "production tag moved from its event source SHA");
}

async function resolveTagCommit(base, tagName, token) {
  let object = (await githubJson(`${base}/git/ref/tags/${encodeURIComponent(tagName)}`, token)).object;
  for (let depth = 0; object?.type === "tag" && depth < 5; depth += 1) {
    object = (await githubJson(`${base}/git/tags/${object.sha}`, token)).object;
  }
  invariant(object?.type === "commit" && shaPattern.test(object.sha), `tag ${tagName} does not resolve to a commit`);
  return object.sha;
}

async function workflowProof(base, repository, sourceSha, workflowFile, validator, token) {
  const workflowPath = `.github/workflows/${workflowFile}`;
  const workflow = await githubJson(`${base}/actions/workflows/${workflowFile}`, token);
  const params = new URLSearchParams({
    branch: "main",
    event: "push",
    status: "completed",
    head_sha: sourceSha,
    per_page: "100",
  });
  const listed = await githubJson(`${base}/actions/workflows/${workflow.id}/runs?${params}`, token);
  invariant(
    Number(listed.total_count) === listed.workflow_runs?.length,
    `exact ${workflowPath} run set exceeded one response page`,
  );
  const selected = selectExactSuccessfulRun({ workflowPath, sourceSha, runs: listed.workflow_runs });
  const run = await githubJson(`${base}/actions/runs/${selected.id}`, token);
  const jobs = await collectJobs(repository, run.id, run.run_attempt, token);
  const checkSuite = await githubJson(`${base}/check-suites/${run.check_suite_id}`, token);
  const proof = validator({
    repository,
    sourceSha,
    requestedRunId: String(run.id),
    workflow,
    run,
    jobs,
    checkSuite,
  });
  const finalRun = await githubJson(`${base}/actions/runs/${run.id}`, token);
  validateRunStability(run, finalRun);
  return proof;
}

async function main() {
  const [repository, tagName, releaseBranch, expectedSourceSha] = process.argv.slice(2);
  const token = process.env.GITHUB_TOKEN;
  invariant(token, "GITHUB_TOKEN is required");
  validatePromotionInputs({ repository, tagName, releaseBranch, expectedSourceSha });

  const base = `https://api.github.com/repos/${repository}`;
  const sourceSha = await resolveTagCommit(base, tagName, token);
  validateTagEventSource(sourceSha, expectedSourceSha);
  const mainComparison = await githubJson(`${base}/compare/${sourceSha}...main`, token);
  validateAncestorComparison(mainComparison, sourceSha, "main");

  const [ci, e2e] = await Promise.all([
    workflowProof(base, repository, sourceSha, "ci.yml", validateDeploymentCiProof, token),
    workflowProof(base, repository, sourceSha, "e2e.yml", validateDeploymentE2eProof, token),
  ]);

  const releaseRefPath = `heads/${releaseBranch}`;
  let releaseRef;
  try {
    releaseRef = await githubJson(`${base}/git/ref/${releaseRefPath}`, token);
  } catch (error) {
    throw new Error(`${releaseBranch} must already exist at the deployed production baseline: ${error.message}`, {
      cause: error,
    });
  }
  const releaseSha = releaseRef?.object?.sha;
  invariant(shaPattern.test(releaseSha), `release branch ${releaseBranch} has no valid baseline`);
  const releaseComparison = await githubJson(`${base}/compare/${releaseSha}...${sourceSha}`, token);
  validateAncestorComparison(releaseComparison, releaseSha, releaseBranch);

  if (releaseSha === sourceSha) {
    process.stdout.write(`${JSON.stringify({ repository, tagName, sourceSha, releaseBranch, updated: false, ci, e2e })}\n`);
    return;
  }

  const reread = await githubJson(`${base}/git/ref/${releaseRefPath}`, token);
  validateUnchangedReleaseRef(releaseRef, reread, releaseBranch);
  const updated = await githubJson(`${base}/git/refs/${releaseRefPath}`, token, {
    method: "PATCH",
    body: JSON.stringify({ sha: sourceSha, force: false }),
  });
  invariant(updated?.object?.sha === sourceSha, `GitHub did not update ${releaseBranch} to the tagged commit`);
  process.stdout.write(`${JSON.stringify({ repository, tagName, sourceSha, releaseBranch, updated: true, ci, e2e })}\n`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    process.stderr.write(`promote-production: ${error.message}\n`);
    process.exitCode = 1;
  });
}
