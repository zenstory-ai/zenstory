import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
const pair = (access: string, refresh: string) => {
  localStorage.setItem('access_token', access);
  localStorage.setItem('refresh_token', refresh);
};
beforeEach(() => { vi.resetModules(); localStorage.clear(); sessionStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); });

describe('actual apiCall continuation after shared refresh', () => {
  it.each(['healthy', 'replacement', 'logout'] as const)('preserves request affinity after shared refresh, session=%s', async mode => {
    const replace = mode === 'replacement';
    pair('A-access', 'A-refresh');
    const response = deferred<Response>();
    const calls: { path: string; authorization: string | null; body: string | null }[] = [];
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://local.test').pathname;
      if (path.includes('/locales/')) return Promise.resolve(new Response('{}', { status: 200 }));
      const headers = new Headers(init?.headers);
      calls.push({ path, authorization: headers.get('Authorization'), body: typeof init?.body === 'string' ? init.body : null });
      if (path === '/api/auth/refresh') return response.promise;
      if (path === '/api/v1/projects/owned-A') {
        const count = calls.filter(item => item.path === path).length;
        return Promise.resolve(new Response(JSON.stringify(count === 1 ? { detail: 'expired' } : { ok: true }), { status: count === 1 ? 401 : 200 }));
      }
      throw new Error('Unexpected offline path: ' + path);
    }));
    const { apiCall, tryRefreshToken } = await import('../apiClient');
    const firstRefresh = tryRefreshToken();
    const replacement = firstRefresh.then(ok => {
      expect(ok).toBe(true);
      if (replace) {
        pair('B-access', 'B-refresh');
        localStorage.setItem('user', '{"id":"B"}');
      }
      if (mode === 'logout') localStorage.clear();
    });
    const request = apiCall('/api/v1/projects/owned-A', { method: 'PUT', body: '{"notes":"A-only-body"}' });
    // Attach rejection handling immediately, before settling any boundary.
    const outcome = request.then(value => ({ value, error: null }), (error: unknown) => ({ value: null, error }));
    // Both callers are now using the real shared refresh primitive.
    await vi.waitFor(() => expect(calls.filter(item => item.path === '/api/v1/projects/owned-A')).toHaveLength(1));
    response.resolve(new Response(JSON.stringify({ access_token: 'A-rotated', refresh_token: 'A-refresh-rotated' }), { status: 200 }));
    await replacement;
    const result = await outcome;
    const requestCalls = calls.filter(item => item.path === '/api/v1/projects/owned-A');
    console.info('ACTUAL_RETRY_AFFINITY', JSON.stringify({ mode, calls, error: result.error instanceof Error ? result.error.message : null }));
    if (mode !== 'healthy') {
      expect(localStorage.getItem('access_token')).toBe(replace ? 'B-access' : null);
      expect(localStorage.getItem('refresh_token')).toBe(replace ? 'B-refresh' : null);
      expect(localStorage.getItem('user')).toBe(replace ? '{"id":"B"}' : null);
      expect(requestCalls).toHaveLength(1);
      expect(result.error).toMatchObject({ status: 401 });
      expect(result.value).toBeNull();
    } else {
      expect(requestCalls).toHaveLength(2);
      expect(requestCalls[0]?.authorization).toBe('Bearer A-access');
      expect(requestCalls[1]?.authorization).toBe('Bearer A-rotated');
      expect(requestCalls[1]?.body).toBe('{"notes":"A-only-body"}');
      expect(result.error).toBeNull();
      expect(result.value).toEqual({ ok: true });
    }
    expect(calls.filter(item => item.path === '/api/auth/refresh')).toHaveLength(1);
  });
});
