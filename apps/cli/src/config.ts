import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

import { CliError, EXIT } from './client.js';

export const DEFAULT_API_BASE = 'https://api.zenstory.ai/api/v1';
export const API_KEY_PREFIX = 'eg_';

export interface StoredConfig {
  apiKey?: string;
  apiBase?: string;
}

export type Env = Record<string, string | undefined>;

export interface ResolvedAuth {
  apiKey: string | undefined;
  apiBase: string;
  keySource: 'env' | 'config' | 'none';
  baseSource: 'env' | 'config' | 'default';
  /** The base a config-file key is bound to (the base it was saved with, or the default). */
  boundApiBase: string;
}

export function configDir(env: Env = process.env): string {
  const xdg = env.XDG_CONFIG_HOME;
  const base = xdg && xdg.trim() ? xdg : join(env.HOME || homedir(), '.config');
  return join(base, 'zenstory');
}

export function configPath(env: Env = process.env): string {
  return join(configDir(env), 'config.json');
}

/** Where `files put` keeps pre-write backups: $XDG_CACHE_HOME/zenstory/backups (default ~/.cache/...). */
export function backupDir(env: Env = process.env): string {
  const xdg = env.XDG_CACHE_HOME;
  const base = xdg && xdg.trim() ? xdg : join(env.HOME || homedir(), '.cache');
  return join(base, 'zenstory', 'backups');
}

/** Warning text when the config file is readable by group/other (POSIX only), else null. */
export function configPermissionWarning(env: Env = process.env, platform: string = process.platform): string | null {
  if (platform === 'win32') return null;
  const path = configPath(env);
  try {
    const mode = statSync(path).mode & 0o777;
    if (mode & 0o077) {
      return `Warning: ${path} has permissions ${mode.toString(8).padStart(4, '0')}; it contains your API key. Run: chmod 600 ${path}`;
    }
  } catch {
    // Missing file: nothing to warn about.
  }
  return null;
}

export function readConfig(env: Env = process.env): StoredConfig {
  const path = configPath(env);
  if (!existsSync(path)) return {};
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'));
    if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      return {
        apiKey: typeof obj.apiKey === 'string' ? obj.apiKey : undefined,
        apiBase: typeof obj.apiBase === 'string' ? obj.apiBase : undefined,
      };
    }
  } catch {
    // Corrupt config: treat as logged out rather than crashing every command.
  }
  return {};
}

/** Write config atomically with 0700 dir / 0600 file permissions. */
export function writeConfig(config: StoredConfig, env: Env = process.env): string {
  const path = configPath(env);
  const dir = dirname(path);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  chmodSync(dir, 0o700);
  const tmp = `${path}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
    chmodSync(tmp, 0o600);
    renameSync(tmp, path);
  } catch (err) {
    rmSync(tmp, { force: true });
    throw err;
  }
  chmodSync(path, 0o600);
  return path;
}

export function deleteConfig(env: Env = process.env): boolean {
  const path = configPath(env);
  if (!existsSync(path)) return false;
  rmSync(path, { force: true });
  return true;
}

export function normalizeApiBase(base: string): string {
  return base.trim().replace(/\/+$/, '');
}

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * Validate an API base URL. The API key travels in a request header, so only https is
 * accepted, except for loopback hosts (local development / self-hosting on this machine).
 */
export function validateApiBase(base: string): string {
  const normalized = normalizeApiBase(base);
  let url: URL;
  try {
    url = new URL(normalized);
  } catch {
    throw new CliError(`Invalid API base URL. Example: ${DEFAULT_API_BASE}`, EXIT.USAGE);
  }
  if (url.username || url.password) {
    throw new CliError('Invalid API base URL: embedded credentials are not allowed.', EXIT.USAGE);
  }
  if (url.search) {
    throw new CliError('Invalid API base URL: a query string is not allowed.', EXIT.USAGE);
  }
  if (url.hash) {
    throw new CliError('Invalid API base URL: a fragment is not allowed.', EXIT.USAGE);
  }
  if (url.protocol === 'https:') return normalized;
  if (url.protocol === 'http:' && LOOPBACK_HOSTS.has(url.hostname)) return normalized;
  throw new CliError(
    `Refusing to use API base "${normalized}": the API key would be sent unencrypted. ` +
      'Use an https:// URL (plain http is only allowed for localhost, 127.0.0.1 and [::1]).',
    EXIT.USAGE,
  );
}

export function resolveAuth(env: Env = process.env): ResolvedAuth {
  const stored = readConfig(env);
  const envKey = env.ZENSTORY_API_KEY?.trim();
  const envBase = env.ZENSTORY_API_BASE?.trim();

  let apiKey: string | undefined;
  let keySource: ResolvedAuth['keySource'] = 'none';
  if (envKey) {
    apiKey = envKey;
    keySource = 'env';
  } else if (stored.apiKey) {
    apiKey = stored.apiKey;
    keySource = 'config';
  }

  let apiBase = DEFAULT_API_BASE;
  let baseSource: ResolvedAuth['baseSource'] = 'default';
  if (envBase) {
    apiBase = envBase;
    baseSource = 'env';
  } else if (stored.apiBase?.trim()) {
    apiBase = stored.apiBase;
    baseSource = 'config';
  }

  return {
    apiKey,
    apiBase: normalizeApiBase(apiBase),
    keySource,
    baseSource,
    boundApiBase: normalizeApiBase(stored.apiBase?.trim() || DEFAULT_API_BASE),
  };
}

/**
 * Refuse to send a saved key to a host it was not saved for. A saved key is bound to the base
 * it was logged in with; pointing ZENSTORY_API_BASE elsewhere requires ZENSTORY_API_KEY too
 * (or an explicit --allow-base-override). Also enforces https (see validateApiBase).
 */
export function assertSafeAuth(auth: ResolvedAuth, opts: { allowBaseOverride?: boolean } = {}): void {
  validateApiBase(auth.apiBase);
  if (
    auth.keySource === 'config' &&
    auth.baseSource === 'env' &&
    auth.apiBase !== auth.boundApiBase &&
    !opts.allowBaseOverride
  ) {
    throw new CliError(
      `ZENSTORY_API_BASE (${auth.apiBase}) differs from the API base your saved key belongs to ` +
        `(${auth.boundApiBase}); refusing to send the saved key there. Set ZENSTORY_API_KEY for that ` +
        'server, run `zenstory login --api-base <url>` for it, or pass --allow-base-override.',
      EXIT.USAGE,
    );
  }
}

/** Never show a full key: `eg_1a2b…9f0e`. Keeps whatever prefix the key really has. */
export function maskKey(key: string | undefined): string {
  if (!key) return '(none)';
  const prefix = key.startsWith(API_KEY_PREFIX) ? API_KEY_PREFIX : '';
  const body = key.slice(prefix.length);
  if (body.length <= 12) return `${prefix}…`;
  return `${prefix}${body.slice(0, 4)}…${body.slice(-4)}`;
}
