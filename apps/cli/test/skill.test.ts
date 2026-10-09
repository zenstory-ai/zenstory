import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { bundledSkillDir, installSkill, isPreviousInstall, resolveSkillsRoot } from '../src/skill.js';
import { runCli, tempDir } from './helpers.js';

describe('resolveSkillsRoot', () => {
  it('maps known agents to their skills directories', () => {
    expect(resolveSkillsRoot('claude', '/h')).toBe('/h/.claude/skills');
    expect(resolveSkillsRoot('codex', '/h')).toBe('/h/.agents/skills');
    expect(resolveSkillsRoot('openclaw', '/h')).toBe('/h/.openclaw/skills');
    expect(resolveSkillsRoot('openclaw', '/h', '/work', { OPENCLAW_STATE_DIR: '/state' })).toBe('/state/skills');
    expect(resolveSkillsRoot('agents', '/h')).toBe('/h/.agents/skills');
    expect(resolveSkillsRoot('~/custom', '/h')).toBe('/h/custom');
    expect(resolveSkillsRoot('rel/skills', '/h', '/work')).toBe('/work/rel/skills');
  });
});

describe('installSkill', () => {
  it('copies the bundled skill and refuses to overwrite without force', () => {
    const home = tempDir();
    const result = installSkill({ target: 'claude', home });
    const dest = join(home, '.claude', 'skills', 'zenstory');
    expect(result.destination).toBe(dest);
    expect(existsSync(join(dest, 'SKILL.md'))).toBe(true);
    expect(existsSync(join(dest, 'references', 'cli.md'))).toBe(true);
    expect(existsSync(join(dest, 'references', 'concepts.md'))).toBe(true);

    expect(() => installSkill({ target: 'claude', home })).toThrow(/--force/);

    writeFileSync(join(dest, 'references', 'stale.md'), 'old');
    expect(isPreviousInstall(dest)).toBe(true);
    const again = installSkill({ target: 'claude', home, force: true });
    expect(again.replaced).toBe(true);
    expect(existsSync(join(dest, 'references', 'stale.md'))).toBe(false);
    expect(existsSync(join(dest, 'SKILL.md'))).toBe(true);
    expect(readdirSync(join(home, '.claude', 'skills'))).toEqual(['zenstory']);
  });

  it('--force never deletes a directory that is not a previous install (e.g. a git clone)', () => {
    const root = tempDir();
    const dest = join(root, 'zenstory');
    mkdirSync(join(dest, '.git'), { recursive: true });
    writeFileSync(join(dest, 'SKILL.md'), '---\nname: zenstory\ndescription: x\n---\n');
    writeFileSync(join(dest, 'package.json'), '{}');
    expect(() => installSkill({ target: root, force: true })).toThrow(/does not look like a ZenStory skill/);
    expect(existsSync(join(dest, '.git'))).toBe(true);
    expect(existsSync(join(dest, 'package.json'))).toBe(true);

    const other = join(root, 'other');
    mkdirSync(join(other, 'zenstory'), { recursive: true });
    writeFileSync(join(other, 'zenstory', 'SKILL.md'), '---\nname: something-else\n---\n');
    expect(() => installSkill({ target: other, force: true })).toThrow(/does not look like/);
    expect(readFileSync(join(other, 'zenstory', 'SKILL.md'), 'utf8')).toContain('something-else');
  });

  it('never follows a symlinked or dangling-symlink destination', () => {
    const root = tempDir();
    const target = join(tempDir(), 'real');
    mkdirSync(target);
    writeFileSync(join(target, 'precious.txt'), 'keep');
    symlinkSync(target, join(root, 'zenstory'));
    expect(() => installSkill({ target: root })).toThrow(/--force/);
    const replaced = installSkill({ target: root, force: true });
    expect(replaced.replaced).toBe(true);
    expect(lstatSync(join(root, 'zenstory')).isSymbolicLink()).toBe(false);
    expect(readFileSync(join(target, 'precious.txt'), 'utf8')).toBe('keep');
    expect(readdirSync(target)).toEqual(['precious.txt']);

    const root2 = tempDir();
    symlinkSync(join(root2, 'does-not-exist'), join(root2, 'zenstory'));
    expect(() => installSkill({ target: root2 })).toThrow(/--force/);
    installSkill({ target: root2, force: true });
    expect(existsSync(join(root2, 'zenstory', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(root2, 'does-not-exist'))).toBe(false);
  });

  it('installs into an arbitrary directory via the CLI', async () => {
    const root = tempDir();
    const res = await runCli(['skill', 'install', '--target', root, '--json']);
    expect(res.code).toBe(0);
    expect(JSON.parse(res.stdout).destination).toBe(join(root, 'zenstory'));
    const refused = await runCli(['skill', 'install', '--target', root]);
    expect(refused.code).toBe(2);
    expect(refused.stderr).toContain('--force');
  });

  it('does not replace a non-directory at the destination', () => {
    const root = tempDir();
    mkdirSync(root, { recursive: true });
    writeFileSync(join(root, 'zenstory'), 'file');
    expect(() => installSkill({ target: root, force: true })).toThrow(/not a directory/);
  });
});

describe('bundled skill (Agent Skills spec)', () => {
  const dir = bundledSkillDir();
  const skillMd = readFileSync(join(dir, 'SKILL.md'), 'utf8');
  const match = /^---\n([\s\S]*?)\n---\n/.exec(skillMd);

  it('has frontmatter with a name matching the directory', () => {
    expect(match).not.toBeNull();
    const fm = match![1];
    expect(fm).toMatch(/^name: zenstory$/m);
    expect(dir.endsWith('zenstory')).toBe(true);
  });

  it('has a description of at most 1024 characters', () => {
    const fm = match![1];
    const desc = /^description: (.+)$/m.exec(fm);
    expect(desc).not.toBeNull();
    expect(desc![1].length).toBeGreaterThan(50);
    expect(desc![1].length).toBeLessThanOrEqual(1024);
    expect(fm).not.toMatch(/^(triggers|version|api_base):/m);
  });

  it('metadata.version matches package.json', () => {
    const pkg = JSON.parse(readFileSync(join(dir, '..', '..', 'package.json'), 'utf8')) as { version: string };
    expect(match![1].split('\n')).toContain(`  version: "${pkg.version}"`);
  });

  it('never tells users to put the key on the command line', () => {
    expect(skillMd).not.toMatch(/login --key eg_/);
    expect(skillMd).not.toMatch(/\/tmp\/ch/);
  });

  it('keeps the body under 500 lines and references existing files', () => {
    expect(skillMd.split('\n').length).toBeLessThan(500);
    for (const ref of skillMd.matchAll(/\]\((references\/[^)]+)\)/g)) {
      expect(existsSync(join(dir, ref[1]))).toBe(true);
    }
    expect(readdirSync(join(dir, 'references')).sort()).toEqual(['cli.md', 'concepts.md']);
  });
});
