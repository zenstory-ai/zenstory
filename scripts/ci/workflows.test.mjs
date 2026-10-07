import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";
import test from "node:test";

const execFileAsync = promisify(execFile);

test("changed workflows and composite actions pin every external action to a full SHA", async () => {
  for (const file of [
    ".github/workflows/ci.yml",
    ".github/workflows/e2e.yml",
    ".github/workflows/cli-release.yml",
    ".github/workflows/zenstory-online-smoke.yml",
    ".github/actions/setup-backend/action.yml",
    ".github/actions/setup-frontend/action.yml",
  ]) {
    const source = await readFile(file, "utf8");
    for (const match of source.matchAll(/^\s*- uses:\s*([^\s#]+)/gm)) {
      if (match[1].startsWith("./")) continue;
      assert.match(match[1], /@[a-f0-9]{40}$/i, `${file}: ${match[1]} is not full-SHA pinned`);
    }
  }
});

test("CI summary and E2E summary consume detector result and validated scope", async () => {
  const ci = await readFile(".github/workflows/ci.yml", "utf8");
  assert.match(ci, /DETECT_CHANGES_RESULT: \$\{\{ needs\['detect-changes'\]\.result \}\}/);
  assert.match(ci, /node scripts\/ci\/check-workflow-results\.mjs ci/);
  assert.match(ci, /cancel-in-progress: \$\{\{ github\.event_name == 'pull_request' \|\| github\.ref != 'refs\/heads\/main' \}\}/);

  const e2e = await readFile(".github/workflows/e2e.yml", "utf8");
  assert.match(e2e, /github\.event_name == 'schedule' \|\| github\.event_name == 'workflow_dispatch'/);
  assert.match(e2e, /node scripts\/ci\/check-workflow-results\.mjs e2e/);
  assert.match(e2e, /Malformed paths-filter output/);
});

test("online smoke is read-only, source-bound, canonical-origin only, and not a provider gate claim", async () => {
  const workflow = await readFile(".github/workflows/zenstory-online-smoke.yml", "utf8");
  assert.match(workflow, /source_sha:/);
  assert.match(workflow, /ci_run_id:/);
  assert.match(workflow, /providerSourceBinding: 'NOT_VERIFIED'/);
  assert.match(workflow, /providerGate: 'NOT_VERIFIED'/);
  assert.doesNotMatch(workflow, /VERCEL_TOKEN|RAILWAY_TOKEN|vercel deploy|railway (up|deploy)|secrets\./i);

  await assert.rejects(
    execFileAsync("bash", ["scripts/ci/zenstory-online-smoke.sh"], {
      env: {
        ...process.env,
        ZENSTORY_BACKEND_URL: "https://example.invalid",
        ZENSTORY_FRONTEND_URL: "https://app.zenstory.ai",
      },
    }),
    /Refusing noncanonical backend origin/,
  );
  await assert.rejects(
    execFileAsync("bash", ["scripts/ci/zenstory-online-smoke.sh"], {
      env: {
        ...process.env,
        ZENSTORY_BACKEND_URL: "https://api.zenstory.ai",
        ZENSTORY_FRONTEND_URL: "https://example.invalid",
      },
    }),
    /Refusing noncanonical frontend origin/,
  );
});

test("regression controls are required CI and npm version comes from the npm executable", async () => {
  const workflow = await readFile(".github/workflows/cli-release.yml", "utf8");
  assert.doesNotMatch(workflow, /process\.versions\.npm/);
  assert.match(workflow, /node scripts\/cli-release\.mjs npm-version/);
  const ci = await readFile(".github/workflows/ci.yml", "utf8");
  assert.match(ci, /node --test scripts\/ci\/\*\.test\.mjs/);
  assert.match(ci, /node --test scripts\/cli-release\.test\.mjs/);
  assert.match(ci, /'scripts\/cli-release\*\.mjs'/);
});

test("backend CI runs PostgreSQL regressions serially against a dedicated database", async () => {
  const ci = await readFile(".github/workflows/ci.yml", "utf8");
  const step = ci.slice(
    ci.indexOf("- name: Run PostgreSQL regressions serially"),
    ci.indexOf("- name: Run flow tests"),
  );

  assert.match(step, /ZENSTORY_TEST_POSTGRES_URL: postgresql:\/\/test:test@localhost:5433\/zenstory_postgres_tests/);
  assert.match(step, /^\s+DATABASE_URL: postgresql:\/\/test:test@localhost:5433\/zenstory_postgres_tests$/m);
  assert.match(step, /CREATE DATABASE zenstory_postgres_tests/);
  assert.match(step, /CREATE DATABASE zenstory_postgres_tests ENCODING 'UTF8' TEMPLATE template0/);
  assert.match(step, /pytest -n 0 --no-cov/);
  assert.match(step, /tests\/test_core\/test_database_startup\.py/);
  assert.match(step, /tests\/test_core\/test_database_timezone\.py/);
  assert.match(step, /tests\/test_services\/test_admin_roles_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_admin_skills_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_commercial_backend_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_material_refunds_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_agent_file_preconditions_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_agent_auth_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_auth_refresh_revocation_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_writing_stats_concurrency_postgres\.py/);
  assert.match(step, /tests\/test_api\/test_stats_record_first_rows_postgres\.py/);
  assert.match(step, /tests\/test_api\/test_public_skill_counts_postgres\.py/);
  assert.match(step, /tests\/test_api\/test_chat_worker_postgres\.py/);
  assert.match(step, /tests\/test_api\/test_chat_history_order_postgres\.py/);
  assert.match(step, /tests\/test_agent\/test_query_files_summary_projection\.py/);
  assert.match(step, /tests\/test_agent\/test_failed_generation_finalization_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_project_capacity_concurrency_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_snapshot_cleanup\.py/);
  assert.match(step, /tests\/test_services\/test_snapshot_concurrency_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_snapshot_hierarchy_restore\.py/);
  assert.match(step, /tests\/test_services\/test_tree_writers_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_canonical_folder_repair_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_tree_delete_traversal_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_file_upload_postgres\.py/);
  assert.match(step, /tests\/test_services\/test_persisted_order_bounds_postgres\.py/);
  assert.match(step, /tests\/test_agent\/test_snapshot_mutation_postgres\.py/);

  const fullSuite = ci.slice(
    ci.indexOf("- name: Run unit tests with pytest"),
    ci.indexOf("- name: Run PostgreSQL regressions serially"),
  );
  assert.doesNotMatch(fullSuite, /ZENSTORY_TEST_POSTGRES_URL/);
  assert.match(fullSuite, /ZENSTORY_TEST_REDIS_URL: redis:\/\/localhost:6380\/1/);
});

test("real CLI metadata and pre-write live source checks cannot be omitted", async () => {
  const ci = await readFile(".github/workflows/ci.yml", "utf8");
  assert.match(ci, /node scripts\/cli-release\.mjs check --publishing false/);
  const {stdout} = await execFileAsync("node", ["scripts/cli-release.mjs", "check", "--publishing", "false"]);
  assert.equal(JSON.parse(stdout).publishing, false);
  const helper = await readFile("scripts/cli-release.mjs", "utf8");
  const github = helper.slice(helper.indexOf("async function commandGithubPublish"), helper.indexOf("async function registryMetadata"));
  assert.ok(github.indexOf("verifyPublicationSource") > github.indexOf("findRelease"));
  assert.ok(github.indexOf("verifyPublicationSource") < github.indexOf('method: "POST"'));
  const npm = helper.slice(helper.indexOf("async function commandNpmPublish"), helper.indexOf("async function retry"));
  assert.ok(npm.indexOf("verifyPublicationSource") > npm.indexOf("registryMetadata"));
  assert.ok(npm.indexOf("verifyPublicationSource") < npm.indexOf('await run("npm"'));
  const receipt = await readFile("scripts/ci/deployment-receipt.mjs", "utf8");
  assert.ok(receipt.indexOf("const finalRun") > receipt.indexOf("const proof = validateDeploymentCiProof"));
});

test("receipt input values enter shell commands only through environment variables", async () => {
  const workflow = await readFile(".github/workflows/zenstory-online-smoke.yml", "utf8");
  const command = workflow.split("\n").find((line) => line.includes("node scripts/ci/deployment-receipt.mjs"));
  assert.doesNotMatch(command, /\$\{\{ inputs\./);
  assert.match(command, /"\$SOURCE_SHA" "\$CI_RUN_ID"/);
});

test("readiness runs trusted controls rather than executing a supplied source checkout", async () => {
  const workflow = await readFile(".github/workflows/zenstory-online-smoke.yml", "utf8");
  assert.doesNotMatch(workflow, /ref: \$\{\{ inputs\.source_sha \}\}/);
  assert.match(workflow, /Checkout trusted main receipt controls/);
  assert.match(workflow, /node scripts\/ci\/deployment-receipt\.mjs "\$GITHUB_REPOSITORY" "\$SOURCE_SHA" "\$CI_RUN_ID"/);
});

test("canonical E2E account fixtures use email domains accepted by admin EmailStr", async () => {
  for (const file of [
    ".github/actions/setup-backend/action.yml", ".github/workflows/e2e.yml",
    "apps/server/scripts/seed_test_user.py", "apps/server/scripts/seed_test_admin.py",
    "apps/web/e2e/config/test-users.ts", "apps/web/e2e/admin.spec.ts",
    "apps/web/e2e/dashboard-coachmark.spec.ts", "apps/web/e2e/helpers/common.ts",
    "scripts/ci/ci.sh", "apps/web/e2e/README.md",
  ]) {
    const source = await readFile(file, "utf8");
    assert.doesNotMatch(source, /@zenstory\.(?:local|test)\b/, `${file}: invalid seeded email identity`);
  }
});
