import { describe, expect, it } from 'vitest';

import { CliError, httpError, ZenstoryClient } from '../src/client.js';
import { KEY, mockFetch } from './helpers.js';

describe('ZenstoryClient', () => {
  it('sends the X-Agent-API-Key header, JSON body and query params', async () => {
    const { fetch, calls } = mockFetch(() => ({ body: { ok: true } }));
    const client = new ZenstoryClient({ apiKey: KEY, apiBase: 'https://api.test/api/v1/', fetch, userAgent: 'zenstory-cli/9' });

    await client.get('/agent/projects/p1/files', { file_type: 'draft', parent_id: undefined, limit: 5 });
    await client.post('/agent/projects/p1/search', { query: 'q', top_k: 3 });

    expect(calls[0].url).toBe('https://api.test/api/v1/agent/projects/p1/files?file_type=draft&limit=5');
    expect(calls[0].method).toBe('GET');
    expect(calls[0].headers['X-Agent-API-Key']).toBe(KEY);
    expect(calls[0].headers['User-Agent']).toBe('zenstory-cli/9');
    expect(calls[0].headers['Content-Type']).toBeUndefined();
    expect(calls[1].method).toBe('POST');
    expect(calls[1].headers['Content-Type']).toBe('application/json');
    expect(calls[1].body).toEqual({ query: 'q', top_k: 3 });
  });

  it('wraps network failures without leaking the key', async () => {
    const client = new ZenstoryClient({
      apiKey: KEY,
      apiBase: 'https://api.test/api/v1',
      fetch: async () => {
        throw new TypeError('fetch failed');
      },
    });
    const err = await client.get('/agent/projects').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(CliError);
    expect((err as CliError).message).toContain('Could not reach https://api.test/api/v1');
    expect((err as CliError).message).not.toContain(KEY);
  });

  it('maps API error responses to CliErrors with exit codes', async () => {
    const { fetch } = mockFetch(() => ({
      status: 403,
      body: { detail: 'ERR_NOT_AUTHORIZED', error_code: 'ERR_NOT_AUTHORIZED', error_detail: 'API Key lacks required scope: write' },
    }));
    const client = new ZenstoryClient({ apiKey: KEY, apiBase: 'https://api.test/api/v1', fetch });
    const err = (await client.put('/agent/files/f1', {}).catch((e: unknown) => e)) as CliError;
    expect(err.exitCode).toBe(3);
    expect(err.status).toBe(403);
    expect(err.code).toBe('ERR_NOT_AUTHORIZED');
    expect(err.message).toContain('lacks required scope: write');
  });
});

describe('httpError', () => {
  it('keeps generic conflicts separate from stale-write errors', () => {
    for (const body of [
      { error_code: 'ERR_RESOURCE_CONFLICT', error_detail: { reason: 'duplicate_name' } },
      { error_code: 'OTHER_CONFLICT', error_detail: { reason: 'stale_write' } },
      { error_code: 'ERR_RESOURCE_CONFLICT', error_detail: 'stale_write' },
    ]) {
      expect(httpError(409, body).code).toBe(body.error_code);
    }
  });
  it('401 → auth exit code with login hint', () => {
    const e = httpError(401, { detail: 'ERR_AUTH_TOKEN_INVALID', error_code: 'ERR_AUTH_TOKEN_INVALID', error_detail: 'Invalid API Key' });
    expect(e.exitCode).toBe(3);
    expect(e.message).toMatch(/Invalid API Key/);
    expect(e.message).toMatch(/zenstory login/);
  });

  it('404 → not-found exit code', () => {
    const e = httpError(404, { detail: 'ERR_FILE_NOT_FOUND', error_code: 'ERR_FILE_NOT_FOUND', error_detail: 'ERR_FILE_NOT_FOUND' });
    expect(e.exitCode).toBe(4);
    expect(e.message).toContain('ERR_FILE_NOT_FOUND');
  });

  it('429 → rate-limit exit code, retry-after and documented limits', () => {
    const e = httpError(429, { detail: 'Rate limit exceeded. Please try again later.' }, '3600');
    expect(e.exitCode).toBe(5);
    expect(e.message).toContain('Retry after 3600s');
    expect(e.message).toContain('2000/h');
  });

  it('422 → usage exit code with field-level validation messages', () => {
    const e = httpError(422, {
      detail: 'ERR_VALIDATION_ERROR',
      error_code: 'ERR_VALIDATION_ERROR',
      errors: [{ loc: ['body', 'name'], msg: 'String should have at least 1 character' }],
    });
    expect(e.exitCode).toBe(2);
    expect(e.message).toContain('name: String should have at least 1 character');
  });

  it('falls back to plain text bodies', () => {
    const e = httpError(502, 'Bad Gateway');
    expect(e.exitCode).toBe(1);
    expect(e.message).toBe('Request failed (502): Bad Gateway');
  });
});
