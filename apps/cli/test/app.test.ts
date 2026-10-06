import { existsSync, readdirSync, readFileSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { buildTree, defaultProjectLang, normalizeFileType, redactKeys } from '../src/app.js';
import { configPath, writeConfig } from '../src/config.js';
import { KEY, mockFetch, runCli, tempDir } from './helpers.js';

const BASE = 'https://api.test/api/v1';

function loggedInEnv(): Record<string, string> {
  const env = { XDG_CONFIG_HOME: tempDir(), XDG_CACHE_HOME: tempDir() };
  writeConfig({ apiKey: KEY, apiBase: BASE }, env);
  return env;
}

const project = {
  id: 'p1',
  name: '星海',
  description: null,
  project_type: 'novel',
  owner_id: 'u1',
  created_at: '2026-01-01T00:00:00',
  updated_at: '2026-01-02T00:00:00',
};

describe('login / logout / whoami', () => {
  it('validates the key, saves it 0600 and never prints the full key', async () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const { fetch, calls } = mockFetch(() => ({ body: [project] }));
    const res = await runCli(['login', '--key', KEY, '--api-base', `${BASE}/`], { env, fetch });

    expect(res.code).toBe(0);
    expect(calls[0].url).toBe(`${BASE}/agent/projects`);
    expect(calls[0].headers['X-Agent-API-Key']).toBe(KEY);
    expect(res.stdout).toContain('1 project(s) visible');
    expect(res.stdout + res.stderr).not.toContain(KEY);
    const saved = JSON.parse(readFileSync(configPath(env), 'utf8'));
    expect(saved).toEqual({ apiKey: KEY, apiBase: BASE });
    expect(statSync(configPath(env)).mode & 0o777).toBe(0o600);
  });

  it('prompts for the key (echo off) when --key is omitted on a TTY', async () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const prompts: string[] = [];
    const res = await runCli(['login'], {
      env,
      fetch,
      promptSecret: async (p) => {
        prompts.push(p);
        return `  ${KEY}  `;
      },
    });
    expect(res.code).toBe(0);
    expect(prompts).toEqual(['Paste API key: ']);
    expect(calls[0].headers['X-Agent-API-Key']).toBe(KEY);
    expect(res.stdout + res.stderr).not.toContain(KEY);
    // No --api-base: the default base is not frozen into the config file.
    expect(JSON.parse(readFileSync(configPath(env), 'utf8'))).toEqual({ apiKey: KEY });
  });

  it('without --key and without a TTY, fails with a hint to use --key -', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const res = await runCli(['login'], { env: { XDG_CONFIG_HOME: tempDir() }, fetch });
    expect(res.code).toBe(2);
    expect(res.stderr).toContain('--key -');
    expect(calls).toHaveLength(0);
  });

  it('warns when the key is passed as an argument', async () => {
    const { fetch } = mockFetch(() => ({ body: [] }));
    const res = await runCli(['login', '--key', KEY], { env: { XDG_CONFIG_HOME: tempDir() }, fetch });
    expect(res.code).toBe(0);
    expect(res.stderr).toContain('shell history');
  });

  it('treats an empty ZENSTORY_API_BASE as unset', async () => {
    const env = { XDG_CONFIG_HOME: tempDir(), ZENSTORY_API_BASE: '  ' };
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const res = await runCli(['login', '--key', '-'], { env, fetch, stdin: KEY });
    expect(res.code).toBe(0);
    expect(calls[0].url).toBe('https://api.zenstory.ai/api/v1/agent/projects');
  });

  it('refuses to log in against a ZENSTORY_API_BASE the key would not be bound to', async () => {
    const env = { XDG_CONFIG_HOME: tempDir(), ZENSTORY_API_BASE: 'https://self.test/api/v1' };
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const res = await runCli(['login', '--key', '-'], { env, fetch, stdin: KEY });
    expect(res.code).toBe(2);
    expect(res.stderr).toContain('--api-base https://self.test/api/v1');
    expect(calls).toHaveLength(0);
    const ok = await runCli(['login', '--key', '-', '--api-base', 'https://self.test/api/v1'], { env, fetch, stdin: KEY });
    expect(ok.code).toBe(0);
    expect(JSON.parse(readFileSync(configPath(env), 'utf8')).apiBase).toBe('https://self.test/api/v1');
  });

  it('rejects plain-http API bases except loopback', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const env = { XDG_CONFIG_HOME: tempDir() };
    const bad = await runCli(['login', '--key', '-', '--api-base', 'http://evil.test/api/v1'], { env, fetch, stdin: KEY });
    expect(bad.code).toBe(2);
    expect(bad.stderr).toContain('unencrypted');
    expect(calls).toHaveLength(0);
    const local = await runCli(['login', '--key', '-', '--api-base', 'http://127.0.0.1:8000/api/v1'], { env, fetch, stdin: KEY });
    expect(local.code).toBe(0);
  });

  it('whoami on a 404 hints at the /api/v1 prefix', async () => {
    const { fetch } = mockFetch(() => ({ status: 404, body: { detail: 'Not Found' } }));
    const res = await runCli(['whoami'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(2);
    expect(res.stderr).toContain('/api/v1 prefix');
    expect(res.stderr).not.toContain('project/file id');
  });

  it('reads the key from stdin with --key -', async () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const { fetch } = mockFetch(() => ({ body: [] }));
    const res = await runCli(['login', '--key', '-', '--json'], { env, fetch, stdin: `${KEY}\n` });
    expect(res.code).toBe(0);
    expect(JSON.parse(res.stdout)).toMatchObject({ ok: true, key: 'eg_a1b2…a1b2', projectCount: 0 });
  });

  it('does not save a rejected key', async () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const { fetch } = mockFetch(() => ({
      status: 401,
      body: { detail: 'ERR_AUTH_TOKEN_INVALID', error_code: 'ERR_AUTH_TOKEN_INVALID', error_detail: 'Invalid API Key' },
    }));
    const res = await runCli(['login', '--key', KEY], { env, fetch });
    expect(res.code).toBe(3);
    expect(res.stderr).toContain('Invalid API Key');
    expect(() => statSync(configPath(env))).toThrow();
  });

  it.each([
    ['inactive user', 'ERR_AUTH_INACTIVE_USER', 'Account has been disabled'],
    ['project permission denial', 'ERR_NOT_AUTHORIZED', 'API key cannot access this project'],
    ['localized denial', undefined, '账户已被禁用'],
    ['unknown denial', 'ERR_SOMETHING_NEW', 'Forbidden'],
  ])('does not save a key after an unrecognized 403 (%s)', async (_label, errorCode, errorDetail) => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const { fetch } = mockFetch(() => ({
      status: 403,
      body: { detail: errorCode ?? errorDetail, ...(errorCode ? { error_code: errorCode } : {}), error_detail: errorDetail },
    }));
    const res = await runCli(['login', '--key', KEY], { env, fetch });
    expect(res.code).toBe(3);
    expect(() => statSync(configPath(env))).toThrow();
  });

  it('saves an explicitly scope-limited key without relying on localized error text', async () => {
    const env = { XDG_CONFIG_HOME: tempDir() };
    const { fetch } = mockFetch(() => ({
      status: 403,
      body: { detail: 'ERR_AUTH_SCOPE_DENIED', error_code: 'ERR_AUTH_SCOPE_DENIED', error_detail: '缺少所需权限' },
    }));
    const res = await runCli(['login', '--key', KEY], { env, fetch });
    expect(res.code).toBe(0);
    expect(res.stderr).toContain('lacks the "read" scope');
    expect(JSON.parse(readFileSync(configPath(env), 'utf8'))).toEqual({ apiKey: KEY });
  });

  it('rejects keys without the eg_ prefix before any request', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const res = await runCli(['login', '--key', 'sk-123'], { env: { XDG_CONFIG_HOME: tempDir() }, fetch });
    expect(res.code).toBe(2);
    expect(calls).toHaveLength(0);
  });

  it('whoami reports scopes using a side-effect-free write probe', async () => {
    const { fetch, calls } = mockFetch((c) =>
      c.method === 'GET'
        ? { body: [project, project] }
        : { status: 404, body: { detail: 'ERR_PROJECT_NOT_FOUND', error_code: 'ERR_PROJECT_NOT_FOUND', error_detail: 'Project not found' } },
    );
    const res = await runCli(['whoami', '--json'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(0);
    const out = JSON.parse(res.stdout);
    expect(out).toMatchObject({ apiBase: BASE, key: 'eg_a1b2…a1b2', keySource: 'config', scopes: { read: true, write: true }, projectCount: 2 });
    expect(calls[1]).toMatchObject({ method: 'PUT', body: {} });
    expect(res.stdout).not.toContain(KEY);
  });

  it('whoami detects a read-only key', async () => {
    const { fetch } = mockFetch((c) =>
      c.method === 'GET'
        ? { body: [] }
        : { status: 403, body: { detail: 'ERR_AUTH_SCOPE_DENIED', error_code: 'ERR_AUTH_SCOPE_DENIED', error_detail: 'API Key lacks required scope: write' } },
    );
    const res = await runCli(['whoami'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(0);
    expect(res.stdout).toMatch(/Write scope\s+no/);
    expect(res.stdout).toMatch(/Read scope\s+yes/);
  });

  it.each([
    ['project permission', 'ERR_NOT_AUTHORIZED', 'API key cannot access this project'],
    ['localized response', undefined, '没有项目权限'],
    ['unknown response', 'ERR_SOMETHING_NEW', 'Forbidden'],
  ])('whoami never infers write scope from an unrecognized 403 (%s)', async (_label, errorCode, errorDetail) => {
    const { fetch } = mockFetch((c) => c.method === 'GET'
      ? { body: [] }
      : {
          status: 403,
          body: { detail: errorCode ?? errorDetail, ...(errorCode ? { error_code: errorCode } : {}), error_detail: errorDetail },
        });
    const res = await runCli(['whoami', '--json'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(0);
    expect(JSON.parse(res.stdout).scopes.write).toBeNull();
  });

  it('whoami without a key exits 3 with setup guidance', async () => {
    const res = await runCli(['whoami'], { env: { XDG_CONFIG_HOME: tempDir() } });
    expect(res.code).toBe(3);
    expect(res.stderr).toContain('Settings → Agent');
    expect(res.stderr).toContain('zenstory login');
    expect(res.stderr).toContain('paste the key when prompted');
  });

  it('env vars override the saved config', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const env = { ...loggedInEnv(), ZENSTORY_API_KEY: 'eg_fromenv', ZENSTORY_API_BASE: 'http://localhost:8000/api/v1' };
    await runCli(['projects', 'list', '--json'], { env, fetch });
    expect(calls[0].url).toBe('http://localhost:8000/api/v1/agent/projects');
    expect(calls[0].headers['X-Agent-API-Key']).toBe('eg_fromenv');
  });

  it('refuses to send the saved key to a different ZENSTORY_API_BASE', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const env = { ...loggedInEnv(), ZENSTORY_API_BASE: 'https://other.test/api/v1' };
    const res = await runCli(['projects', 'list'], { env, fetch });
    expect(res.code).toBe(2);
    expect(res.stderr).toContain('differs from the API base your saved key belongs to');
    expect(calls).toHaveLength(0);

    const same = await runCli(['projects', 'list', '--json'], { env: { ...env, ZENSTORY_API_BASE: `${BASE}/` }, fetch });
    expect(same.code).toBe(0);
    const override = await runCli(['projects', 'list', '--json', '--allow-base-override'], { env, fetch });
    expect(override.code).toBe(0);
    expect(calls.at(-1)!.url).toBe('https://other.test/api/v1/agent/projects');
  });

  it('refuses a plain-http base for every command', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: [] }));
    const env = { XDG_CONFIG_HOME: tempDir(), ZENSTORY_API_KEY: KEY, ZENSTORY_API_BASE: 'http://api.test/api/v1' };
    const res = await runCli(['projects', 'list'], { env, fetch });
    expect(res.code).toBe(2);
    expect(calls).toHaveLength(0);
  });

  it('warns when the config file is readable by others', async () => {
    if (process.platform === 'win32') return;
    const env = loggedInEnv();
    const { chmodSync } = await import('node:fs');
    chmodSync(configPath(env), 0o644);
    const { fetch } = mockFetch(() => ({ body: [] }));
    const res = await runCli(['projects', 'list', '--json'], { env, fetch });
    expect(res.code).toBe(0);
    expect(res.stderr).toContain('chmod 600');
  });

  it('logout removes the saved config', async () => {
    const env = loggedInEnv();
    const res = await runCli(['logout'], { env });
    expect(res.code).toBe(0);
    expect(() => statSync(configPath(env))).toThrow();
  });
});

describe('projects', () => {
  it('create posts the real field names', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: project }));
    const res = await runCli(['projects', 'create', '--name', '星海', '--type', 'short', '--description', 'd'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(0);
    expect(calls[0]).toMatchObject({ method: 'POST', url: `${BASE}/agent/projects`, body: { name: '星海', description: 'd', project_type: 'short' } });
  });

  it('create prints the default folders from the response', async () => {
    const folders = [
      { id: 'p1-lore-folder', title: '设定', file_type: 'folder', order: 0 },
      { id: 'p1-draft-folder', title: '正文', file_type: 'folder', order: 4 },
    ];
    const { fetch } = mockFetch(() => ({ body: { ...project, folders } }));
    const env = loggedInEnv();
    const res = await runCli(['projects', 'create', '--name', '星海'], { env, fetch });
    expect(res.code).toBe(0);
    expect(res.stdout).toContain('Folders:\n  设定/  p1-lore-folder\n  正文/  p1-draft-folder');
    const json = await runCli(['projects', 'create', '--name', '星海', '--json'], { env, fetch });
    expect(JSON.parse(json.stdout).folders).toEqual(folders);
  });

  it('create sends Accept-Language from --lang, else ZENSTORY_LANG / LC_ALL / LANG', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: project }));
    const env = loggedInEnv();
    const lang = async (extraEnv: Record<string, string>, args: string[] = []) => {
      const res = await runCli(['projects', 'create', '--name', '星海', ...args], { env: { ...env, ...extraEnv }, fetch });
      expect(res.code).toBe(0);
      return calls.at(-1)!.headers['Accept-Language'];
    };
    expect(await lang({})).toBe('en');
    expect(await lang({ LANG: 'zh_CN.UTF-8' })).toBe('zh');
    expect(await lang({ LANG: 'zh_CN.UTF-8', LC_ALL: 'en_US.UTF-8' })).toBe('en');
    expect(await lang({ LANG: 'en_US.UTF-8', ZENSTORY_LANG: 'zh' })).toBe('zh');
    expect(await lang({ ZENSTORY_LANG: 'zh' }, ['--lang', 'en'])).toBe('en');
    expect(calls.at(-1)!.body).toEqual({ name: '星海', project_type: 'novel' });

    const before = calls.length;
    const bad = await runCli(['projects', 'create', '--name', '星海', '--lang', 'fr'], { env, fetch });
    expect(bad.code).toBe(2);
    expect(calls).toHaveLength(before);
    expect(defaultProjectLang({ LC_ALL: 'ZH_tw' })).toBe('zh');
  });

  it('create surfaces the 403 for keys limited to specific projects', async () => {
    const detail = 'This API key is limited to specific projects and cannot create new projects.';
    const { fetch } = mockFetch(() => ({ status: 403, body: { detail: 'ERR_NOT_AUTHORIZED', error_code: 'ERR_NOT_AUTHORIZED', error_detail: detail } }));
    const res = await runCli(['projects', 'create', '--name', '星海'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(3);
    expect(res.stderr).toContain(detail);
  });

  it('list renders a table with CJK names', async () => {
    const { fetch } = mockFetch(() => ({ body: [project] }));
    const res = await runCli(['projects', 'list'], { env: loggedInEnv(), fetch });
    expect(res.stdout).toContain('星海');
    expect(res.stdout).toMatch(/^ID\s+TYPE\s+UPDATED\s+NAME/);
  });

  it('delete requires --yes', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: { message: 'Project deleted successfully' } }));
    const denied = await runCli(['projects', 'delete', 'p1'], { env: loggedInEnv(), fetch });
    expect(denied.code).toBe(2);
    expect(calls).toHaveLength(0);
    const ok = await runCli(['projects', 'delete', 'p1', '--yes'], { env: loggedInEnv(), fetch });
    expect(ok.code).toBe(0);
    expect(calls[0]).toMatchObject({ method: 'DELETE', url: `${BASE}/agent/projects/p1` });
  });
});

describe('files', () => {
  it('list sends filters, defaults to content-free fields and maps the material alias', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: { files: [{ id: 'f1', title: '第一章', file_type: 'snippet', parent_id: null }], total: 3, limit: 1, offset: 0 } }));
    const res = await runCli(['files', 'list', 'p1', '--type', 'material', '--parent', 'p1-material-folder', '--limit', '1'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(0);
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe('/api/v1/agent/projects/p1/files');
    expect(url.searchParams.get('file_type')).toBe('snippet');
    expect(url.searchParams.get('parent_id')).toBe('p1-material-folder');
    expect(url.searchParams.get('fields')).toBe('id,title,file_type,parent_id,order,updated_at');
    expect(url.searchParams.get('limit')).toBe('1');
    expect(res.stdout).toContain('Showing 1-1 of 3');
  });

  it('list --all pages through results', async () => {
    const { fetch, calls } = mockFetch((c) => {
      const offset = Number(new URL(c.url).searchParams.get('offset'));
      const files = offset === 0 ? Array.from({ length: 200 }, (_, i) => ({ id: `f${i}` })) : [{ id: 'f200' }];
      return { body: { files, total: 201, limit: 200, offset } };
    });
    const res = await runCli(['files', 'list', 'p1', '--all', '--json'], { env: loggedInEnv(), fetch });
    expect(calls).toHaveLength(2);
    expect(JSON.parse(res.stdout).files).toHaveLength(201);
  });

  it('list --all rejects --limit/--offset and dedupes rows repeated across pages', async () => {
    const { fetch, calls } = mockFetch((c) => {
      const offset = Number(new URL(c.url).searchParams.get('offset'));
      const files = offset === 0 ? Array.from({ length: 200 }, (_, i) => ({ id: `f${i}` })) : [{ id: 'f199' }, { id: 'f200' }];
      return { body: { files, total: 202, limit: 200, offset } };
    });
    const env = loggedInEnv();
    const bad = await runCli(['files', 'list', 'p1', '--all', '--limit', '5'], { env, fetch });
    expect(bad.code).toBe(2);
    expect(calls).toHaveLength(0);
    const res = await runCli(['files', 'list', 'p1', '--all', '--json'], { env, fetch });
    const ids = JSON.parse(res.stdout).files.map((f: { id: string }) => f.id);
    expect(ids).toHaveLength(201);
    expect(new Set(ids).size).toBe(201);
  });

  it('get prints raw content by default and the full object with --json', async () => {
    const file = { id: 'f1', title: '第一章', content: '晨光。', file_type: 'draft', parent_id: 'p1-draft-folder', order: 0 };
    const { fetch, calls } = mockFetch(() => ({ body: file }));
    const env = loggedInEnv();
    const raw = await runCli(['files', 'get', 'f1'], { env, fetch });
    expect(raw.stdout).toBe('晨光。\n');
    expect(calls[0].url).toBe(`${BASE}/agent/files/f1`);
    const json = await runCli(['files', 'get', 'f1', '--json'], { env, fetch });
    expect(JSON.parse(json.stdout)).toEqual(file);
  });

  it('get -o writes the exact content to a new 0600 file', async () => {
    const { fetch } = mockFetch(() => ({ body: { id: 'f1', title: 't', content: 'no trailing newline' } }));
    const dir = tempDir();
    const out = join(dir, 'ch1.md');
    const env = loggedInEnv();
    const res = await runCli(['files', 'get', 'f1', '-o', out], { env, fetch });
    expect(res.code).toBe(0);
    expect(readFileSync(out, 'utf8')).toBe('no trailing newline');
    if (process.platform !== 'win32') expect(statSync(out).mode & 0o777).toBe(0o600);
    expect(readdirSync(dir)).toEqual(['ch1.md']);

    const again = await runCli(['files', 'get', 'f1', '-o', out], { env, fetch });
    expect(again.code).toBe(2);
    expect(again.stderr).toContain('--force');
    const forced = await runCli(['files', 'get', 'f1', '-o', out, '--force'], { env, fetch });
    expect(forced.code).toBe(0);
  });

  it('get -o never follows symlinks, even with --force', async () => {
    const { fetch } = mockFetch(() => ({ body: { id: 'f1', title: 't', content: 'payload' } }));
    const dir = tempDir();
    const victim = join(dir, 'victim.txt');
    writeFileSync(victim, 'keep me');
    const link = join(dir, 'link.md');
    symlinkSync(victim, link);
    const dangling = join(dir, 'dangling.md');
    symlinkSync(join(dir, 'missing.txt'), dangling);
    const env = loggedInEnv();
    for (const target of [link, dangling]) {
      const res = await runCli(['files', 'get', 'f1', '-o', target, '--force'], { env, fetch });
      expect(res.code).toBe(2);
      expect(res.stderr).toContain('symbolic link');
    }
    expect(readFileSync(victim, 'utf8')).toBe('keep me');
    expect(existsSync(join(dir, 'missing.txt'))).toBe(false);
  });

  it('create sends title/file_type/content/parent_id/metadata from a content file', async () => {
    const src = join(tempDir(), 'char.md');
    writeFileSync(src, '# 林远\n冷静');
    const { fetch, calls } = mockFetch((c) => ({ body: { id: 'f9', ...(c.body as object) } }));
    const res = await runCli(
      ['files', 'create', 'p1', '--title', '林远', '--type', 'character', '--parent', 'p1-character-folder', '--content-file', src, '--metadata', '{"role":"protagonist"}'],
      { env: loggedInEnv(), fetch },
    );
    expect(res.code).toBe(0);
    expect(calls[0]).toMatchObject({
      method: 'POST',
      url: `${BASE}/agent/projects/p1/files`,
      body: { title: '林远', file_type: 'character', content: '# 林远\n冷静', parent_id: 'p1-character-folder', metadata: { role: 'protagonist' } },
    });
  });

  it('create and put send --order as an integer', async () => {
    const { fetch, calls } = mockFetch((c) =>
      c.method === 'GET' ? { body: { id: 'f1', title: 't', content: 'x', updated_at: 'u' } } : { body: { id: 'f1', ...(c.body as object) } },
    );
    const env = loggedInEnv();
    const created = await runCli(['files', 'create', 'p1', '--title', 'Ch 3', '--order', '3'], { env, fetch });
    expect(created.code).toBe(0);
    expect(calls[0].body).toMatchObject({ title: 'Ch 3', order: 3 });

    const put = await runCli(['files', 'put', 'f1', '--order', '0'], { env, fetch });
    expect(put.code).toBe(0);
    expect(calls.at(-1)).toMatchObject({ method: 'PUT', url: `${BASE}/agent/files/f1`, body: { order: 0 } });
    expect(existsSync(join(env.XDG_CACHE_HOME, 'zenstory', 'backups'))).toBe(false);

    const before = calls.length;
    for (const bad of ['-1', '1.5', 'x', '2147483648']) {
      expect((await runCli(['files', 'create', 'p1', '--title', 't', '--order', bad], { env, fetch })).code).toBe(2);
      expect((await runCli(['files', 'put', 'f1', '--order', bad], { env, fetch })).code).toBe(2);
    }
    expect(calls).toHaveLength(before);
  });

  it('put skips the full-file read for metadata-only updates', async () => {
    const { fetch, calls } = mockFetch((c) => ({
      body: { id: 'f1', title: 'Renamed', file_type: 'draft', content: 'large server body', ...(c.body as object) },
    }));
    const res = await runCli(['files', 'put', 'f1', '--title', 'Renamed', '--order', '4'], {
      env: loggedInEnv(),
      fetch,
    });

    expect(res.code).toBe(0);
    expect(calls).toEqual([
      expect.objectContaining({
        method: 'PUT',
        url: `${BASE}/agent/files/f1`,
        body: { title: 'Renamed', order: 4 },
      }),
    ]);
  });

  it('move posts parent_id (root → null) and order', async () => {
    const { fetch, calls } = mockFetch((c) => ({ body: { id: 'f1', title: '第一章', file_type: 'draft', order: 1, ...(c.body as object) } }));
    const env = loggedInEnv();
    const res = await runCli(['files', 'move', 'f1', '--parent', 'p1-draft-folder', '--order', '1'], { env, fetch });
    expect(res.code).toBe(0);
    expect(calls[0]).toMatchObject({ method: 'POST', url: `${BASE}/agent/files/f1/move`, body: { parent_id: 'p1-draft-folder', order: 1 } });
    expect(res.stdout).toContain('to p1-draft-folder');

    const root = await runCli(['files', 'move', 'f1', '--parent', 'root', '--json'], { env, fetch });
    expect(root.code).toBe(0);
    expect(calls[1].body).toEqual({ parent_id: null });

    const missing = await runCli(['files', 'move', 'f1'], { env, fetch });
    expect(missing.code).toBe(2);
    expect(calls).toHaveLength(2);
  });

  it('create reads content from stdin with --content -', async () => {
    const { fetch, calls } = mockFetch((c) => ({ body: c.body }));
    await runCli(['files', 'create', 'p1', '--title', 'Ch 2', '--content', '-'], { env: loggedInEnv(), fetch, stdin: 'from stdin' });
    expect(calls[0].body).toMatchObject({ file_type: 'draft', content: 'from stdin' });
  });

  const serverFile = { id: 'f1', title: 't', file_type: 'draft', content: 'old text', updated_at: '2026-01-02T00:00:00' };
  const putFetch = () =>
    mockFetch((c) => (c.method === 'GET' ? { body: serverFile } : { body: { ...serverFile, ...(c.body as object) } }));

  it('put reads the current file, backs it up (0600), then replaces it', async () => {
    const { fetch, calls } = putFetch();
    const env = loggedInEnv();
    const empty = await runCli(['files', 'put', 'f1', '--content', '-'], { env, fetch, stdin: '  \n' });
    expect(empty.code).toBe(2);
    expect(calls).toHaveLength(0);

    const ok = await runCli(['files', 'put', 'f1', '--content', 'new text', '--title', 'T2', '--json'], { env, fetch });
    expect(ok.code).toBe(0);
    expect(calls[0]).toMatchObject({ method: 'GET', url: `${BASE}/agent/files/f1` });
    expect(calls[1]).toMatchObject({ method: 'PUT', url: `${BASE}/agent/files/f1`, body: { content: 'new text', title: 'T2' } });
    const backupPath = JSON.parse(ok.stdout).backupPath as string;
    expect(backupPath.startsWith(join(env.XDG_CACHE_HOME, 'zenstory', 'backups', 'f1-'))).toBe(true);
    expect(readFileSync(backupPath, 'utf8')).toBe('old text');
    if (process.platform !== 'win32') expect(statSync(backupPath).mode & 0o777).toBe(0o600);

    const human = await runCli(['files', 'put', 'f1', '--content', 'newer text'], { env, fetch });
    expect(human.stdout).toContain('Previous content saved to');

    const nothing = await runCli(['files', 'put', 'f1'], { env, fetch });
    expect(nothing.code).toBe(2);
  });

  it('put --if-updated-at refuses when the server copy changed', async () => {
    const { fetch, calls } = putFetch();
    const env = loggedInEnv();
    const stale = await runCli(['files', 'put', 'f1', '--content', 'new text', '--if-updated-at', '2026-01-01T00:00:00'], { env, fetch });
    expect(stale.code).toBe(1);
    expect(stale.stderr).toContain('changed on the server');
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(0);
    const fresh = await runCli(['files', 'put', 'f1', '--content', 'new text', '--if-updated-at', '2026-01-02T00:00:00'], { env, fetch });
    expect(fresh.code).toBe(0);
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  });

  it('put carries the exact conditional timestamp to the server', async () => {
    const timestamp = '2026-01-02T00:00:00.123456Z';
    const { fetch, calls } = mockFetch((call) => ({
      body: call.method === 'GET'
        ? { ...serverFile, updated_at: timestamp }
        : { ...serverFile, ...(call.body as object) },
    }));
    const result = await runCli([
      'files', 'put', 'f1', '--title', 'Changed', '--if-updated-at', timestamp,
    ], { env: loggedInEnv(), fetch });

    expect(result.code).toBe(0);
    expect(calls.find((call) => call.method === 'PUT')?.body).toMatchObject({
      title: 'Changed', base_updated_at: timestamp,
    });
  });

  it('put reports a server-side conditional conflict after a successful preflight', async () => {
    const { fetch, calls } = mockFetch((call) => {
      if (call.method === 'GET') return { body: serverFile };
      if ((call.body as { base_updated_at?: string }).base_updated_at === serverFile.updated_at) {
        return { status: 409, body: { error_code: 'ERR_RESOURCE_CONFLICT', error_detail: 'stale_write' } };
      }
      return { body: { ...serverFile, ...(call.body as object) } };
    });
    const result = await runCli([
      'files', 'put', 'f1', '--content', 'new text', '--if-updated-at', serverFile.updated_at,
    ], { env: loggedInEnv(), fetch });

    expect(calls.map((call) => call.method)).toEqual(['GET', 'PUT']);
    expect(result.code).toBe(1);
    expect(result.stderr).toContain('409');
    expect(result.stdout).not.toContain('Updated');
  });

  it('put and create warn when the version quota is full', async () => {
    const { fetch } = mockFetch((c) =>
      c.method === 'GET' ? { body: serverFile } : { body: { ...serverFile, ...(c.body as object), version_quota_exceeded: true } },
    );
    const env = loggedInEnv();
    const human = await runCli(['files', 'put', 'f1', '--content', 'new text'], { env, fetch });
    expect(human.code).toBe(0);
    expect(human.stderr).toContain("per-file version quota is full; the previous content is only in");
    // Fresh cache dir: two backups of f1 within the same millisecond would collide.
    const json = await runCli(['files', 'put', 'f1', '--content', 'new text', '--json'], { env: loggedInEnv(), fetch });
    expect(JSON.parse(json.stdout).version_quota_exceeded).toBe(true);
    const created = await runCli(['files', 'create', 'p1', '--title', 'x', '--content', 'body'], { env, fetch });
    expect(created.stderr).toContain('no version was recorded');

    const quiet = await runCli(['files', 'put', 'f1', '--content', 'new text'], { env: loggedInEnv(), fetch: putFetch().fetch });
    expect(quiet.stderr).toBe('');
  });

  it('put refuses to shrink content below 50% unless --allow-shrink', async () => {
    const { fetch, calls } = putFetch();
    const env = loggedInEnv();
    const shrink = await runCli(['files', 'put', 'f1', '--content', 'old'], { env, fetch });
    expect(shrink.code).toBe(2);
    expect(shrink.stderr).toContain('--allow-shrink');
    expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(0);
    expect(existsSync(join(env.XDG_CACHE_HOME, 'zenstory', 'backups'))).toBe(false);
    const allowed = await runCli(['files', 'put', 'f1', '--content', 'old', '--allow-shrink'], { env, fetch });
    expect(allowed.code).toBe(0);
  });

  it('tree nests children under folders', () => {
    const tree = buildTree([
      { id: 'd', title: '正文', file_type: 'folder', parent_id: null, order: 4 },
      { id: 'l', title: '设定', file_type: 'folder', parent_id: null, order: 0 },
      { id: 'c2', title: '第二章', file_type: 'draft', parent_id: 'd', order: 1 },
      { id: 'c1', title: '第一章', file_type: 'draft', parent_id: 'd', order: 0 },
      { id: 'o', title: 'orphan', file_type: 'draft', parent_id: 'gone', order: 0 },
    ]);
    // 同 order 的根节点按标题码点排序（与运行环境的语言设置无关）：'orphan' < '设定'
    expect(tree.map((n) => n.id)).toEqual(['o', 'l', 'd']);
    expect(tree[2].children.map((n) => n.id)).toEqual(['c1', 'c2']);
  });

  it('tree keeps nodes that sit in a parent cycle', () => {
    const tree = buildTree([
      { id: 'a', title: 'A', file_type: 'folder', parent_id: 'b', order: 0 },
      { id: 'b', title: 'B', file_type: 'folder', parent_id: 'a', order: 1 },
      { id: 'self', title: 'S', file_type: 'draft', parent_id: 'self', order: 2 },
      { id: 'r', title: 'R', file_type: 'draft', parent_id: null, order: 3 },
    ]);
    const all: string[] = [];
    const walk = (ns: typeof tree) => ns.forEach((n) => (all.push(n.id!), walk(n.children)));
    walk(tree);
    expect(all.sort()).toEqual(['a', 'b', 'r', 'self']);
    expect(() => JSON.stringify(tree)).not.toThrow();
  });

  it('normalizes file types', () => {
    expect(normalizeFileType('Material')).toBe('snippet');
    expect(() => normalizeFileType('chapter')).toThrow(/Valid types/);
  });
});

describe('versions', () => {
  const version = {
    id: 'v2',
    file_id: 'f1',
    project_id: 'p1',
    version_number: 2,
    is_base_version: false,
    word_count: 3,
    char_count: 6,
    change_type: 'ai_edit',
    change_source: 'user',
    change_summary: 'Updated via Agent API',
    lines_added: 1,
    lines_removed: 1,
    created_at: '2026-01-02T00:00:00',
  };

  it('versions lists history with limit/offset/include_auto_save', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: { versions: [version], total: 3, limit: 1, offset: 1, file_id: 'f1', file_title: '第一章' } }));
    const env = loggedInEnv();
    const res = await runCli(['files', 'versions', 'f1', '--limit', '1', '--offset', '1', '--include-auto-save'], { env, fetch });
    expect(res.code).toBe(0);
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe('/api/v1/agent/files/f1/versions');
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: '1', offset: '1', include_auto_save: 'true' });
    expect(res.stdout).toMatch(/^VERSION\s+CREATED\s+TYPE/);
    expect(res.stdout).toContain('Showing 2-2 of 3');

    const plain = await runCli(['files', 'versions', 'f1', '--json'], { env, fetch });
    expect(Object.fromEntries(new URL(calls[1].url).searchParams)).toEqual({});
    expect(JSON.parse(plain.stdout).versions[0].version_number).toBe(2);
    expect((await runCli(['files', 'versions', 'f1', '--limit', '101'], { env, fetch })).code).toBe(2);
  });

  it('version prints content, or writes it with -o via the safe writer', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: { ...version, content: '旧稿' } }));
    const env = loggedInEnv();
    const raw = await runCli(['files', 'version', 'f1', '2'], { env, fetch });
    expect(raw.stdout).toBe('旧稿\n');
    expect(calls[0].url).toBe(`${BASE}/agent/files/f1/versions/2`);

    const dir = tempDir();
    const out = join(dir, 'v2.md');
    const written = await runCli(['files', 'version', 'f1', '2', '-o', out], { env, fetch });
    expect(written.code).toBe(0);
    expect(readFileSync(out, 'utf8')).toBe('旧稿');
    if (process.platform !== 'win32') expect(statSync(out).mode & 0o777).toBe(0o600);
    expect((await runCli(['files', 'version', 'f1', '2', '-o', out], { env, fetch })).code).toBe(2);
    const link = join(dir, 'link.md');
    symlinkSync(out, link);
    const viaLink = await runCli(['files', 'version', 'f1', '2', '-o', link, '--force'], { env, fetch });
    expect(viaLink.stderr).toContain('symbolic link');

    const before = calls.length;
    expect((await runCli(['files', 'version', 'f1', '0'], { env, fetch })).code).toBe(2);
    expect((await runCli(['files', 'version', 'f1', 'latest'], { env, fetch })).code).toBe(2);
    expect(calls).toHaveLength(before);
  });

  it('rollback requires --yes and prints the new version number', async () => {
    const result = {
      success: true,
      message: 'Successfully rolled back to version 1',
      file_id: 'f1',
      restored_version: 1,
      new_version_number: 4,
      snapshot_created: true,
      version_quota_exceeded: false,
      updated_at: '2026-01-03T00:00:00',
    };
    const { fetch, calls } = mockFetch(() => ({ body: result }));
    const env = loggedInEnv();
    const denied = await runCli(['files', 'rollback', 'f1', '1'], { env, fetch });
    expect(denied.code).toBe(2);
    expect(denied.stderr).toContain('--yes');
    expect(calls).toHaveLength(0);

    const ok = await runCli(['files', 'rollback', 'f1', '1', '--yes'], { env, fetch });
    expect(ok.code).toBe(0);
    expect(calls[0]).toMatchObject({ method: 'POST', url: `${BASE}/agent/files/f1/versions/1/rollback` });
    expect(ok.stdout).toContain('new version 4');
    const json = await runCli(['files', 'rollback', 'f1', '1', '--yes', '--json'], { env, fetch });
    expect(JSON.parse(json.stdout)).toEqual(result);
  });

  it('rollback explains a missing new version from version_quota_exceeded', async () => {
    const base = { success: true, message: 'm', file_id: 'f1', restored_version: 1, new_version_number: null, snapshot_created: false, updated_at: 'u' };
    const env = loggedInEnv();
    const quota = await runCli(['files', 'rollback', 'f1', '1', '--yes'], { env, fetch: mockFetch(() => ({ body: { ...base, version_quota_exceeded: true } })).fetch });
    expect(quota.stdout).toContain('version quota full');
    const other = await runCli(['files', 'rollback', 'f1', '1', '--yes'], { env, fetch: mockFetch(() => ({ body: { ...base, version_quota_exceeded: false } })).fetch });
    expect(other.stdout).toContain('no snapshot was recorded');
    expect(other.stdout).not.toContain('quota');
  });
});

describe('search / context', () => {
  it('search posts query/top_k/file_types/include_content', async () => {
    const { fetch, calls } = mockFetch(() => ({
      body: { query: 'q', results: [{ id: 'f1', title: '林远', file_type: 'character', content: null, score: 0.9, snippet: '冷静', line_start: 3, metadata: null }], result_count: 1 },
    }));
    const res = await runCli(['search', 'p1', '主角', '的过去', '--limit', '5', '--type', 'character,lore'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(0);
    expect(calls[0]).toMatchObject({
      method: 'POST',
      url: `${BASE}/agent/projects/p1/search`,
      body: { query: '主角 的过去', top_k: 5, file_types: ['character', 'lore'] },
    });
    expect(res.stdout).toContain('[character] 林远  f1 line 3');
  });

  it('context passes file_id/query/max_items', async () => {
    const { fetch, calls } = mockFetch(() => ({
      body: { items: [{ type: 'character', title: '林远', content_snippet: '冷静', source_file_id: 'f1', relevance: 0.8 }], refs: ['f1'], total_available: 4, returned: 1, token_estimate: 120 },
    }));
    const res = await runCli(['context', 'p1', '--file', 'c1', '--query', '决战', '--max-items', '5'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(0);
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe('/api/v1/agent/projects/p1/writing-context');
    expect(Object.fromEntries(url.searchParams)).toEqual({ file_id: 'c1', query: '决战', max_items: '5' });
    expect(res.stdout).toContain('Returned 1 of 4');
  });
});

describe('errors', () => {
  it.each([false, true])('maps a server stale-write race to STALE_WRITE (json=%s)', async (json) => {
    const { fetch } = mockFetch((call) => call.method === 'PUT'
      ? { status: 409, body: { error_code: 'ERR_RESOURCE_CONFLICT', error_detail: { reason: 'stale_write' } } }
      : { body: { id: 'f1', content: 'old text', updated_at: '2026-01-02T00:00:00.123456' } });
    const res = await runCli(['files', 'put', 'f1', '--content', 'new text', '--if-updated-at', '2026-01-02T00:00:00.123456', ...(json ? ['--json'] : [])], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(1);
    if (json) expect(JSON.parse(res.stderr).error).toMatchObject({ code: 'STALE_WRITE', status: 409 });
    expect(res.stderr).toContain('fetch');
    expect(res.stderr).toContain('merge');
  });

  it('compares UTC-naive tokens without host timezone loss', async () => {
    const previousTimezone = process.env.TZ;
    process.env.TZ = 'America/Los_Angeles';
    try {
      const { fetch, calls } = mockFetch(() => ({ body: { id: 'f1', content: 'old text', updated_at: '2026-01-02T00:00:00.123456' } }));
      const equivalent = await runCli(['files', 'put', 'f1', '--title', 'Same instant', '--if-updated-at', '2026-01-02T08:00:00.123456+08:00'], { env: loggedInEnv(), fetch });
      expect(equivalent.code).toBe(0);
      expect(calls).toHaveLength(2);
    } finally {
      if (previousTimezone === undefined) delete process.env.TZ;
      else process.env.TZ = previousTimezone;
    }
  });

  it('preserves microsecond precision in the stale-write preflight', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: { id: 'f1', content: 'old text', updated_at: '2026-01-02T00:00:00.123456' } }));
    const stale = await runCli(['files', 'put', 'f1', '--title', 'Wrong', '--if-updated-at', '2026-01-02T00:00:00.123457', '--json'], { env: loggedInEnv(), fetch });
    expect(stale.code).toBe(1);
    expect(JSON.parse(stale.stderr).error.code).toBe('STALE_WRITE');
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe('GET');
  });

  it('--json errors go to stderr as JSON with the mapped exit code', async () => {
    const { fetch } = mockFetch(() => ({ status: 429, body: { detail: 'Rate limit exceeded. Please try again later.' }, headers: { 'Retry-After': '3600' } }));
    const res = await runCli(['projects', 'list', '--json'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(5);
    expect(res.stdout).toBe('');
    const err = JSON.parse(res.stderr).error;
    expect(err).toMatchObject({ status: 429, exitCode: 5 });
    expect(err.message).toContain('Retry after 3600s');
  });

  it('HTTP 400 maps to the usage exit code', async () => {
    const { fetch } = mockFetch(() => ({ status: 400, body: { detail: 'ERR_BAD', error_code: 'ERR_BAD', error_detail: 'Parent not found' } }));
    const res = await runCli(['files', 'create', 'p1', '--title', 'x', '--parent', 'nope'], { env: loggedInEnv(), fetch });
    expect(res.code).toBe(2);
    expect(res.stderr).toContain('Parent not found');
  });

  it('never echoes an API key in error messages', async () => {
    const res = await runCli([KEY]);
    expect(res.code).toBe(2);
    expect(res.stderr).not.toContain(KEY);
    expect(res.stderr).toContain('eg_a1b2…a1b2');
    expect(redactKeys(`x ${KEY} y`)).toBe('x eg_a1b2…a1b2 y');
    expect(redactKeys('prefix "eg_"')).toBe('prefix "eg_"');
  });

  it('commands without a key exit 3', async () => {
    const res = await runCli(['projects', 'list'], { env: { XDG_CONFIG_HOME: tempDir() } });
    expect(res.code).toBe(3);
    expect(res.stderr).toContain('Not logged in');
  });

  it('help and version exit 0', async () => {
    expect((await runCli(['--help'])).code).toBe(0);
    expect((await runCli(['files', 'put', '--help'])).stdout).toContain('--content-file');
    expect((await runCli(['--version'])).stdout).toMatch(/^\d+\.\d+\.\d+/);
  });
});
