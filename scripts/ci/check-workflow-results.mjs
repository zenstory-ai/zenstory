#!/usr/bin/env node

import { appendFileSync } from "node:fs";

const finalResults = new Set(["success", "failure", "cancelled", "skipped"]);

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function booleanOutput(value, name) {
  invariant(value === "true" || value === "false", `${name} must be true or false, received ${value || "empty"}`);
  return value === "true";
}

export function validateRecoverySource({ eventName, ref, sourceSha, expectedSourceSha, suite }) {
  invariant(eventName === "workflow_dispatch", "release recovery requires workflow_dispatch");
  invariant(ref === "refs/heads/main", "release recovery requires the main branch");
  invariant(/^[a-f0-9]{40}$/.test(sourceSha), "invalid recovery source SHA");
  invariant(expectedSourceSha === sourceSha, "recovery source does not match the event SHA");
  invariant(suite === undefined || suite === "default" || suite === "full", "recovery requires the full default/full E2E suite");
  return { event: eventName, sourceSha, ref };
}

export function resolveCiScope({ eventName, filters }) {
  if (eventName === "workflow_dispatch") return { backend: true, frontend: true, cli: true, ci: true };
  invariant(eventName === "push" || eventName === "pull_request", "unsupported CI scope event");
  return Object.fromEntries(["backend", "frontend", "cli", "ci"].map(name => [name, booleanOutput(filters[name], `${name} output`)]));
}

function requireResult(result, name) {
  invariant(finalResults.has(result), `${name} has invalid result: ${result || "empty"}`);
  return result;
}

export function checkConditionalJob({ name, result, required }) {
  requireResult(result, name);
  if (required) {
    invariant(result === "success", `${name} was required but finished as ${result}`);
  } else {
    invariant(result === "skipped", `${name} was irrelevant but finished as ${result}`);
  }
}

// Every CI job except detect-changes and ci-summary, mapped to whether the
// validated change scopes require it.
export function ciJobExpectations({ backend, frontend, cli, ci }) {
  return {
    "frontend-lint": frontend || ci,
    "ci-lint": ci,
    "backend-test": backend || ci,
    "backend-coverage": backend || ci,
    "backend-integration": backend || ci,
    "prefect-compatibility": backend || ci,
    "production-images": backend || ci,
    "frontend-test": frontend || ci,
    "frontend-build": frontend || ci,
    "cli-test": cli || ci,
    "vercel-build": frontend || ci,
  };
}

export function validateCiSummary({ detector, outputs, jobs }) {
  invariant(detector === "success", `detect-changes did not succeed: ${detector || "empty"}`);
  const backend = booleanOutput(outputs.backend, "backend output");
  const frontend = booleanOutput(outputs.frontend, "frontend output");
  const cli = booleanOutput(outputs.cli, "cli output");
  const ci = booleanOutput(outputs.ci, "ci output");

  const expectations = ciJobExpectations({ backend, frontend, cli, ci });
  for (const [name, required] of Object.entries(expectations)) {
    checkConditionalJob({ name, result: jobs[name], required });
  }
  return { detector: "success", scopes: { backend, frontend, cli, ci } };
}

export function validateE2eSummary({ detector, outputs, eventName, jobResult }) {
  invariant(detector === "success", `detect-changes did not succeed: ${detector || "empty"}`);
  const backend = booleanOutput(outputs.backend, "backend output");
  const frontend = booleanOutput(outputs.frontend, "frontend output");
  const ci = booleanOutput(outputs.ci, "ci output");
  const forced = eventName === "schedule" || eventName === "workflow_dispatch";
  const required = forced || backend || frontend || ci;
  checkConditionalJob({ name: "e2e-test", result: jobResult, required });
  return { detector: "success", forced, required };
}

function env(name) {
  return process.env[name] ?? "";
}

function main() {
  const mode = process.argv[2];
  let result;
  if (mode === "recovery-source") {
    result = validateRecoverySource({ eventName: env("EVENT_NAME"), ref: env("GITHUB_REF"),
      sourceSha: env("GITHUB_SHA"), expectedSourceSha: env("EXPECTED_SOURCE_SHA"),
      ...(env("E2E_SUITE") ? { suite: env("E2E_SUITE") } : {}) });
  } else if (mode === "scope") {
    result = resolveCiScope({ eventName: env("EVENT_NAME"), filters: {
      backend: env("FILTER_BACKEND"), frontend: env("FILTER_FRONTEND"),
      cli: env("FILTER_CLI"), ci: env("FILTER_CI"),
    } });
    invariant(env("GITHUB_OUTPUT"), "GITHUB_OUTPUT is required for CI scope");
    appendFileSync(env("GITHUB_OUTPUT"), Object.entries(result).map(([name, value]) => `${name}=${value}\n`).join(""));
  } else if (mode === "ci") {
    result = validateCiSummary({
      detector: env("DETECT_CHANGES_RESULT"),
      outputs: {
        backend: env("BACKEND_CHANGED"),
        frontend: env("FRONTEND_CHANGED"),
        cli: env("CLI_CHANGED"),
        ci: env("CI_CHANGED"),
      },
      jobs: {
        "frontend-lint": env("FRONTEND_LINT_RESULT"),
        "ci-lint": env("CI_LINT_RESULT"),
        "backend-test": env("BACKEND_TEST_RESULT"),
        "backend-coverage": env("BACKEND_COVERAGE_RESULT"),
        "backend-integration": env("BACKEND_INTEGRATION_RESULT"),
        "prefect-compatibility": env("PREFECT_COMPATIBILITY_RESULT"),
        "production-images": env("PRODUCTION_IMAGES_RESULT"),
        "frontend-test": env("FRONTEND_TEST_RESULT"),
        "frontend-build": env("FRONTEND_BUILD_RESULT"),
        "cli-test": env("CLI_TEST_RESULT"),
        "vercel-build": env("VERCEL_BUILD_RESULT"),
      },
    });
  } else if (mode === "e2e") {
    result = validateE2eSummary({
      detector: env("DETECT_CHANGES_RESULT"),
      outputs: {
        backend: env("BACKEND_CHANGED"),
        frontend: env("FRONTEND_CHANGED"),
        ci: env("CI_CHANGED"),
      },
      eventName: env("EVENT_NAME"),
      jobResult: env("E2E_TEST_RESULT"),
    });
  } else {
    throw new Error(`expected mode ci, e2e, recovery-source or scope, received ${mode || "empty"}`);
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`workflow-results: ${error.message}\n`);
    process.exitCode = 1;
  }
}
