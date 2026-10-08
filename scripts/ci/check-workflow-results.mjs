#!/usr/bin/env node

const finalResults = new Set(["success", "failure", "cancelled", "skipped"]);

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function booleanOutput(value, name) {
  invariant(value === "true" || value === "false", `${name} must be true or false, received ${value || "empty"}`);
  return value === "true";
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
  if (mode === "ci") {
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
    throw new Error(`expected mode ci or e2e, received ${mode || "empty"}`);
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
