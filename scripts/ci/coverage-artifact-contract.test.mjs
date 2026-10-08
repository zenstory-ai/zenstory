import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const workflow = readFileSync(
  new URL('../../.github/workflows/ci.yml', import.meta.url),
  'utf8',
);

const between = (start, end) => {
  const from = workflow.indexOf(start);
  const to = workflow.indexOf(end, from + 1);
  assert.notEqual(from, -1, `missing CI text: ${start}`);
  assert.notEqual(to, -1, `missing CI text after ${start}: ${end}`);
  return workflow.slice(from, to);
};

const stepBody = (name, nextName) => between(`- name: ${name}`, `- name: ${nextName}`);

const backendShards = () => {
  const matrix = between('  backend-test:', '    services:');
  return [...matrix.matchAll(/- shard: (\S+)\n\s+targets: (.+)\n/g)].map(([, shard, targets]) => ({
    shard,
    targets: targets.trim(),
  }));
};

test('backend shards partition the whole pytest tree by construction', () => {
  const shards = backendShards();
  const names = shards.map(({ shard }) => shard);
  assert.equal(new Set(names).size, names.length, 'shard names must be unique (artifact and coverage file names)');

  // Exactly one catch-all shard runs tests/ minus whole directories; every
  // ignored directory must be fully owned by the remaining shards, so new test
  // directories land in the catch-all and no file is dropped or run twice.
  const catchAll = shards.filter(({ targets }) => /^tests(\s|$)/.test(targets));
  assert.equal(catchAll.length, 1, 'expected one catch-all shard rooted at tests/');
  const ignored = [...catchAll[0].targets.matchAll(/--ignore=(\S+)/g)].map((match) => match[1]);
  assert.deepEqual(catchAll[0].targets.replace(/\s*--ignore=\S+/g, ''), 'tests');

  const owned = shards.filter((entry) => entry !== catchAll[0]);
  for (const directory of ignored) {
    const inDirectory = owned.filter(({ targets }) => targets.startsWith(`${directory}/`) || targets.startsWith(`${directory} `));
    const globs = inDirectory.map(({ targets }) => targets.match(/^(\S+\*\S*)$/)?.[1]).filter(Boolean);
    const complements = inDirectory
      .map(({ targets }) => targets.match(new RegExp(`^${directory} --ignore-glob=(\\S+)$`))?.[1])
      .filter(Boolean);
    assert.equal(inDirectory.length, 2, `${directory} must be split into one glob shard and its complement`);
    assert.equal(globs.length, 1, `${directory} needs exactly one positive glob shard`);
    assert.deepEqual(complements, globs, `${directory} complement must ignore exactly the positive glob`);
  }
  assert.equal(owned.length, ignored.length * 2, 'every non-catch-all shard must belong to an ignored directory');

  const combine = stepBody('Combine shard coverage', 'Enforce backend coverage gate');
  assert.match(combine, new RegExp(`"\\$shards" != "${shards.length}"`), 'coverage combine must expect every shard');
});

test('each shard records coverage under its own name without gating a partial total', () => {
  const unit = stepBody('Run unit tests with pytest', 'Upload shard coverage data');
  assert.match(unit, /COVERAGE_FILE: \.coverage\.\$\{\{ matrix\.shard \}\}/);
  assert.match(unit, /pytest \$SHARD_TARGETS --cov=\. --cov-fail-under=0/);

  const upload = between('- name: Upload shard coverage data', '  backend-coverage:');
  assert.match(upload, /name: backend-coverage-\$\{\{ matrix\.shard \}\}/);
  assert.match(upload, /path: apps\/server\/\.coverage\.\$\{\{ matrix\.shard \}\}/);
  assert.match(upload, /if-no-files-found: error/);
});

test('the 80% backend gate and Codecov XML come from the combined unit-suite shards', () => {
  const job = between('  backend-coverage:', '  backend-integration:');
  assert.match(job, /needs: \[detect-changes, backend-test\]/);
  assert.match(job, /pattern: backend-coverage-\*/);
  assert.match(job, /coverage combine coverage-data/);
  assert.match(job, /coverage xml -o coverage\.xml/);
  assert.match(job, /coverage report --fail-under=80/);
  assert.match(job, /file: \.\/apps\/server\/coverage\.xml/);

  const flow = stepBody('Run flow tests', 'Register every production Prefect deployment');
  assert.match(flow, /pytest tests\/test_flows\b/);
  assert.match(flow, /--no-cov\b/);
});
