import { cpSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, unlinkSync, type Stats } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { CliError, EXIT } from './client.js';

export const SKILL_NAME = 'zenstory';

/** Bundled skill directory: <package>/skill/zenstory (works from src/ and dist/). */
export function bundledSkillDir(): string {
  return fileURLToPath(new URL(`../skill/${SKILL_NAME}`, import.meta.url));
}

/**
 * Known agents and their user-level skills directories.
 * - claude:   Claude Code personal skills (~/.claude/skills)
 * - codex:    Codex USER-scope skills ($HOME/.agents/skills)
 * - openclaw: OpenClaw managed skills (<state-dir>/skills; state dir ~/.openclaw or $OPENCLAW_STATE_DIR)
 * - agents:   the cross-agent ~/.agents/skills directory (read by Codex, and by OpenClaw in its default state)
 */
export const SKILL_TARGETS: Record<string, (home: string, env: Record<string, string | undefined>) => string> = {
  claude: (home) => join(home, '.claude', 'skills'),
  codex: (home) => join(home, '.agents', 'skills'),
  openclaw: (home, env) => join(env.OPENCLAW_STATE_DIR?.trim() || join(home, '.openclaw'), 'skills'),
  agents: (home) => join(home, '.agents', 'skills'),
};

export function resolveSkillsRoot(
  target: string,
  home: string = homedir(),
  cwd: string = process.cwd(),
  env: Record<string, string | undefined> = {},
): string {
  const known = SKILL_TARGETS[target];
  if (known) return known(home, env);
  let dir = target;
  if (dir === '~' || dir.startsWith('~/')) dir = join(home, dir.slice(1));
  return isAbsolute(dir) ? dir : resolve(cwd, dir);
}

export interface InstallResult {
  source: string;
  destination: string;
  replaced: boolean;
}

function lstatOrNull(path: string): Stats | null {
  try {
    return lstatSync(path);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw err;
  }
}

/**
 * True only for a directory that looks exactly like an earlier `skill install`: a regular SKILL.md
 * whose frontmatter says `name: zenstory`, plus an optional `references/` directory holding only
 * regular files. Anything else (a git clone, extra files, symlinks) is never deleted.
 */
export function isPreviousInstall(dir: string): boolean {
  const entries = readdirSync(dir, { withFileTypes: true });
  let hasSkillMd = false;
  for (const entry of entries) {
    if (entry.name === 'SKILL.md' && entry.isFile()) {
      hasSkillMd = true;
    } else if (entry.name === 'references' && entry.isDirectory()) {
      const refs = readdirSync(join(dir, 'references'), { withFileTypes: true });
      if (!refs.every((r) => r.isFile())) return false;
    } else {
      return false;
    }
  }
  if (!hasSkillMd) return false;
  const text = readFileSync(join(dir, 'SKILL.md'), 'utf8');
  const fm = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  return Boolean(fm && new RegExp(`^name:\\s*["']?${SKILL_NAME}["']?\\s*$`, 'm').test(fm[1]));
}

export function installSkill(opts: {
  target: string;
  force?: boolean;
  home?: string;
  cwd?: string;
  env?: Record<string, string | undefined>;
  source?: string;
}): InstallResult {
  const source = opts.source ?? bundledSkillDir();
  if (!existsSync(join(source, 'SKILL.md'))) {
    throw new CliError(`Bundled skill not found at ${source}. Reinstall the zenstory package.`, EXIT.ERROR);
  }
  const root = resolveSkillsRoot(opts.target, opts.home, opts.cwd, opts.env);
  const destination = join(root, SKILL_NAME);

  // lstat: never follow a symlink at the destination (a dangling one would also crash cpSync).
  const existing = lstatOrNull(destination);
  if (existing) {
    if (!opts.force) {
      throw new CliError(`${destination} already exists. Re-run with --force to overwrite it.`, EXIT.USAGE);
    }
    if (!existing.isSymbolicLink()) {
      if (!existing.isDirectory()) {
        throw new CliError(`${destination} exists and is not a directory; refusing to replace it.`, EXIT.ERROR);
      }
      if (!isPreviousInstall(destination)) {
        throw new CliError(
          `${destination} does not look like a ZenStory skill installation (expected only SKILL.md with ` +
            '`name: zenstory` and references/); refusing to replace it even with --force. Move it away manually.',
          EXIT.ERROR,
        );
      }
    }
  }

  mkdirSync(root, { recursive: true });
  const tmp = join(root, `.${SKILL_NAME}.tmp-${process.pid}`);
  const old = join(root, `.${SKILL_NAME}.old-${process.pid}`);
  rmSync(tmp, { recursive: true, force: true });
  try {
    cpSync(source, tmp, { recursive: true });
    if (existing?.isSymbolicLink()) {
      // Remove only the link itself; its target is left untouched.
      unlinkSync(destination);
    } else if (existing) {
      rmSync(old, { recursive: true, force: true });
      renameSync(destination, old);
    }
    try {
      renameSync(tmp, destination);
    } catch (err) {
      if (existing && !existing.isSymbolicLink()) renameSync(old, destination);
      throw err;
    }
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
  rmSync(old, { recursive: true, force: true });
  return { source, destination, replaced: Boolean(existing) };
}
