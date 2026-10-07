import { chmodSync, mkdirSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  assertSafeAuth,
  backupDir,
  configPath,
  configPermissionWarning,
  DEFAULT_API_BASE,
  deleteConfig,
  maskKey,
  readConfig,
  resolveAuth,
  validateApiBase,
  writeConfig,
} from '../src/config.js';
import { KEY, tempDir } from './helpers.js';

describe('config file', () => {
  it('uses XDG_CONFIG_HOME, then ~/.config', () => {
    expect(configPath({ XDG_CONFIG_HOME: '/x' })).toBe('/x/zenstory/config.json');
    expect(configPath({ HOME: '/home/me' })).toBe('/home/me/.config/zenstory/config.json');
  });

  it('writes with 0700 dir and 0600 file permissions and reads back', () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const path = writeConfig({ apiKey: KEY, apiBase: 'https://example.test/api/v1' }, env);
    expect(statSync(path).mode & 0o777).toBe(0o600);
    expect(statSync(join(env.XDG_CONFIG_HOME, 'zenstory')).mode & 0o777).toBe(0o700);
    expect(readConfig(env)).toEqual({ apiKey: KEY, apiBase: 'https://example.test/api/v1' });
  });

  it('tightens permissions of a pre-existing directory and file', () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const dir = join(env.XDG_CONFIG_HOME, 'zenstory');
    mkdirSync(dir, { mode: 0o755 });
    writeFileSync(join(dir, 'config.json'), '{}', { mode: 0o644 });
    writeConfig({ apiKey: KEY }, env);
    expect(statSync(dir).mode & 0o777).toBe(0o700);
    expect(statSync(join(dir, 'config.json')).mode & 0o777).toBe(0o600);
  });

  it('treats a corrupt file as empty and deletes cleanly', () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    mkdirSync(join(env.XDG_CONFIG_HOME, 'zenstory'));
    writeFileSync(configPath(env), 'not json');
    expect(readConfig(env)).toEqual({});
    expect(deleteConfig(env)).toBe(true);
    expect(deleteConfig(env)).toBe(false);
  });
});

describe('resolveAuth', () => {
  it('falls back to the default base with no config', () => {
    const auth = resolveAuth({ XDG_CONFIG_HOME: tempDir() });
    expect(auth).toEqual({
      apiKey: undefined,
      apiBase: DEFAULT_API_BASE,
      keySource: 'none',
      baseSource: 'default',
      boundApiBase: DEFAULT_API_BASE,
    });
  });

  it('lets env vars override the saved config', () => {
    const env: Record<string, string> = { XDG_CONFIG_HOME: tempDir() };
    writeConfig({ apiKey: 'eg_saved', apiBase: 'https://saved.test/api/v1/' }, env);
    expect(resolveAuth(env)).toMatchObject({ apiKey: 'eg_saved', apiBase: 'https://saved.test/api/v1', keySource: 'config' });
    env.ZENSTORY_API_KEY = 'eg_env';
    env.ZENSTORY_API_BASE = 'http://localhost:8000/api/v1';
    expect(resolveAuth(env)).toEqual({
      apiKey: 'eg_env',
      apiBase: 'http://localhost:8000/api/v1',
      keySource: 'env',
      baseSource: 'env',
      boundApiBase: 'https://saved.test/api/v1',
    });
  });

  it('treats blank env vars as unset', () => {
    const auth = resolveAuth({ XDG_CONFIG_HOME: tempDir(), ZENSTORY_API_BASE: '', ZENSTORY_API_KEY: ' ' });
    expect(auth).toMatchObject({ apiBase: DEFAULT_API_BASE, baseSource: 'default', keySource: 'none' });
  });
});

describe('API base safety', () => {
  it('accepts https and loopback http only', () => {
    expect(validateApiBase('https://self.test/api/v1/')).toBe('https://self.test/api/v1');
    expect(validateApiBase('http://localhost:8000/api/v1')).toBe('http://localhost:8000/api/v1');
    expect(validateApiBase('http://127.0.0.1/api/v1')).toBe('http://127.0.0.1/api/v1');
    expect(validateApiBase('http://[::1]:8000/api/v1')).toBe('http://[::1]:8000/api/v1');
    expect(() => validateApiBase('http://api.test/api/v1')).toThrow(/unencrypted/);
    expect(() => validateApiBase('http://localhost.evil.test/api/v1')).toThrow(/unencrypted/);
    expect(() => validateApiBase('ftp://x/api/v1')).toThrow(/unencrypted/);
    expect(() => validateApiBase('not a url')).toThrow(/Invalid API base/);
    try {
      validateApiBase('https://user:password@');
      expect.fail('invalid credential-bearing base should be rejected');
    } catch (err) {
      expect((err as Error).message).not.toContain('password');
    }
  });

  it('rejects credentials, query strings, and fragments in an API base', () => {
    try {
      validateApiBase('https://user:password@self.test/api/v1');
      expect.fail('credential-bearing base should be rejected');
    } catch (err) {
      expect((err as Error).message).toMatch(/credentials/);
      expect((err as Error).message).not.toContain('password');
    }
    for (const [base, expected, secret] of [
      ['https://self.test/api/v1?token=query-secret', /query string/, 'query-secret'],
      ['https://self.test/api/v1#fragment-secret', /fragment/, 'fragment-secret'],
    ] as const) {
      try {
        validateApiBase(base);
        expect.fail('query/fragment API base should be rejected');
      } catch (err) {
        expect((err as Error).message).toMatch(expected);
        expect((err as Error).message).not.toContain(secret);
      }
    }
  });

  it('binds a saved key to the base it was saved with', () => {
    const env: Record<string, string> = { XDG_CONFIG_HOME: tempDir() };
    writeConfig({ apiKey: KEY }, env);
    env.ZENSTORY_API_BASE = 'https://other.test/api/v1';
    expect(() => assertSafeAuth(resolveAuth(env))).toThrow(/differs from the API base/);
    expect(() => assertSafeAuth(resolveAuth(env), { allowBaseOverride: true })).not.toThrow();
    env.ZENSTORY_API_KEY = 'eg_other';
    expect(() => assertSafeAuth(resolveAuth(env))).not.toThrow();
    env.ZENSTORY_API_BASE = `${DEFAULT_API_BASE}/`;
    delete (env as Record<string, string | undefined>).ZENSTORY_API_KEY;
    expect(() => assertSafeAuth(resolveAuth(env))).not.toThrow();
  });
});

describe('config hygiene', () => {
  it('warns about group/world-readable config files on POSIX', () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    writeConfig({ apiKey: KEY }, env);
    expect(configPermissionWarning(env, 'linux')).toBeNull();
    chmodSync(configPath(env), 0o644);
    expect(configPermissionWarning(env, 'linux')).toContain('chmod 600');
    expect(configPermissionWarning(env, 'win32')).toBeNull();
  });

  it('removes the temp file when the final rename fails', () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    mkdirSync(configPath(env), { recursive: true }); // a directory where the file should go
    writeFileSync(`${configPath(env)}/occupied`, 'x');
    expect(() => writeConfig({ apiKey: KEY }, env)).toThrow();
    expect(readdirSync(join(env.XDG_CONFIG_HOME, 'zenstory'))).toEqual(['config.json']);
  });

  it('puts backups under XDG_CACHE_HOME, then ~/.cache', () => {
    expect(backupDir({ XDG_CACHE_HOME: '/c' })).toBe('/c/zenstory/backups');
    expect(backupDir({ HOME: '/home/me' })).toBe('/home/me/.cache/zenstory/backups');
  });
});

describe('maskKey', () => {
  it('never reveals the full key', () => {
    const masked = maskKey(KEY);
    expect(masked).toBe('eg_a1b2…a1b2');
    expect(masked).not.toContain(KEY.slice(3, 20));
    expect(maskKey('eg_short')).toBe('eg_…');
    expect(maskKey(undefined)).toBe('(none)');
    expect(maskKey('sk-0123456789abcdefghij')).toBe('sk-0…ghij');
    expect(maskKey('short')).toBe('…');
  });
});
