import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../logger', () => ({ logger: { log: vi.fn(), warn: vi.fn(), error: vi.fn() } }));

const jwt = (claims: Record<string, unknown>) => `header.${btoa(JSON.stringify(claims))}.signature`;
const pair = (sub = 'owner', family = 'family', jti = 'old', expired = false) => ({
  accessToken: jwt({ sub, typ: 'access', exp: Date.now() / 1000 + 3600 }),
  refreshToken: jwt({ sub, family_id: family, jti, typ: 'refresh', exp: Date.now() / 1000 + (expired ? -10 : 86400) }),
});
const store = (tokens: ReturnType<typeof pair>) => {
  localStorage.setItem('access_token', tokens.accessToken);
  localStorage.setItem('refresh_token', tokens.refreshToken);
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

function serializedLock() {
  let tail = Promise.resolve();
  let entered = 0;
  const request = vi.fn((_name: string, callback: () => Promise<boolean>) => {
    const result = tail.then(() => {
      entered += 1;
      return callback();
    });
    tail = result.then(() => undefined, () => undefined);
    return result;
  });
  Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true });
  return { request, entered: () => entered };
}

function controlledMessageChannel(onTask?: () => void) {
  const deliveries: Array<() => void> = [];
  const close = vi.fn();
  const MessageChannelMock = vi.fn(function MessageChannelMock() {
    const port1: { onmessage: ((event: MessageEvent) => void) | null; close: () => void } = {
      onmessage: null,
      close,
    };
    const port2 = {
      close,
      postMessage: vi.fn(() => {
        deliveries.push(() => {
          onTask?.();
          port1.onmessage?.(new MessageEvent('message'));
        });
      }),
    };
    return { port1, port2 };
  });
  vi.stubGlobal('MessageChannel', MessageChannelMock);
  return {
    MessageChannelMock,
    close,
    pending: () => deliveries.length,
    deliver: () => {
      const delivery = deliveries.shift();
      if (!delivery) throw new Error('No MessageChannel task is pending');
      delivery();
    },
  };
}

function stagedLocalStorage() {
  const canonical = new Map<string, string>();
  const pending = new Map<string, string | null>();
  let stageWrites = false;
  const storage = {
    get length() { return canonical.size; },
    clear: vi.fn(() => {
      if (stageWrites) {
        for (const key of canonical.keys()) pending.set(key, null);
      } else canonical.clear();
    }),
    getItem: vi.fn((key: string) => canonical.get(key) ?? null),
    key: vi.fn((index: number) => [...canonical.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => {
      if (stageWrites) pending.set(key, null);
      else canonical.delete(key);
    }),
    setItem: vi.fn((key: string, value: string) => {
      if (stageWrites) pending.set(key, String(value));
      else canonical.set(key, String(value));
    }),
  } satisfies Storage;
  vi.stubGlobal('localStorage', storage);
  return {
    beginStaging: () => { stageWrites = true; },
    commit: () => {
      for (const [key, value] of pending) {
        if (value === null) canonical.delete(key);
        else canonical.set(key, value);
      }
      pending.clear();
      stageWrites = false;
    },
  };
}

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409 }));
});
afterEach(() => {
  Reflect.deleteProperty(navigator, 'locks');
  vi.unstubAllGlobals();
});

function pausedLock() {
  let release!: () => void;
  const ready = new Promise<void>(resolve => { release = resolve; });
  const request = vi.fn(async (_name: string, callback: () => Promise<boolean>) => {
    await ready;
    return callback();
  });
  Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true });
  return { request, release };
}

describe('cross-tab refresh ownership', () => {
  it('keeps the browser lock through the storage task checkpoint before a queued tab refreshes', async () => {
    const storage = stagedLocalStorage();
    const original = pair();
    const replacement = pair('owner', 'family', 'new');
    store(original);
    storage.beginStaging();
    const lock = serializedLock();
    const checkpoint = controlledMessageChannel(storage.commit);
    const response = deferred<Response>();
    vi.mocked(fetch)
      .mockReturnValueOnce(response.promise)
      .mockResolvedValue({ ok: false, status: 409 } as Response);
    const owner = await import('../apiClient');
    vi.resetModules();
    const queued = await import('../apiClient');

    const ownerRefresh = owner.tryRefreshToken();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const queuedRefresh = queued.tryRefreshToken();
    await vi.waitFor(() => expect(lock.request).toHaveBeenCalledTimes(2));
    expect(lock.entered()).toBe(1);

    response.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: replacement.accessToken,
        refresh_token: replacement.refreshToken,
        user: { id: 'owner' },
      }),
    } as Response);

    await vi.waitFor(() => expect(checkpoint.pending()).toBe(1));
    expect(lock.entered()).toBe(1);
    expect(fetch).toHaveBeenCalledOnce();
    let ownerSettled = false;
    void ownerRefresh.finally(() => { ownerSettled = true; });
    await Promise.resolve();
    expect(ownerSettled).toBe(false);

    checkpoint.deliver();
    await expect(ownerRefresh).resolves.toBe(true);
    await vi.waitFor(() => expect(checkpoint.pending()).toBe(1));
    expect(fetch).toHaveBeenCalledOnce();
    expect(lock.entered()).toBe(2);
    checkpoint.deliver();
    await expect(queuedRefresh).resolves.toBe(true);
    expect(localStorage.getItem('access_token')).toBe(replacement.accessToken);
    expect(localStorage.getItem('refresh_token')).toBe(replacement.refreshToken);
    expect(checkpoint.close).toHaveBeenCalledTimes(4);
  });

  it.each(['logout', 'different-user', 'new-login-family'] as const)(
    'rejects its successful refresh after %s replaces the session during the storage checkpoint',
    async scenario => {
      const original = pair();
      const rotated = pair('owner', 'family', 'rotated');
      store(original);
      serializedLock();
      const checkpoint = controlledMessageChannel();
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          access_token: rotated.accessToken,
          refresh_token: rotated.refreshToken,
          user: { id: 'owner' },
        }),
      } as Response);
      const { tryRefreshToken } = await import('../apiClient');

      const refresh = tryRefreshToken();
      await vi.waitFor(() => expect(checkpoint.pending()).toBe(1));
      if (scenario === 'logout') localStorage.clear();
      else store(scenario === 'different-user' ? pair('other') : pair('owner', 'new-family'));
      const expected = [localStorage.getItem('access_token'), localStorage.getItem('refresh_token')];

      checkpoint.deliver();
      await expect(refresh).resolves.toBe(false);
      expect([localStorage.getItem('access_token'), localStorage.getItem('refresh_token')]).toEqual(expected);
      expect(fetch).toHaveBeenCalledOnce();
    },
  );

  it('keeps a rejected credential refresh locked through the storage checkpoint', async () => {
    const original = pair();
    store(original);
    const lock = serializedLock();
    const checkpoint = controlledMessageChannel();
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 401 } as Response);
    const owner = await import('../apiClient');
    vi.resetModules();
    const queued = await import('../apiClient');

    const ownerRefresh = owner.tryRefreshToken();
    const queuedRefresh = queued.tryRefreshToken();
    await vi.waitFor(() => expect(checkpoint.pending()).toBe(1));
    expect(lock.entered()).toBe(1);
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(fetch).toHaveBeenCalledOnce();

    checkpoint.deliver();
    await expect(ownerRefresh).resolves.toBe(false);
    await vi.waitFor(() => expect(checkpoint.pending()).toBe(1));
    expect(lock.entered()).toBe(2);
    expect(fetch).toHaveBeenCalledOnce();
    checkpoint.deliver();
    await expect(queuedRefresh).resolves.toBe(false);
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
  });

  it('uses the same-family result committed by the tab that owned the browser lock', async () => {
    const original = pair();
    const replacement = pair('owner', 'family', 'new');
    store(original);
    const lock = pausedLock();
    const { tryRefreshToken, resolveOwnedAuthSession } = await import('../apiClient');
    const pending = tryRefreshToken();
    try {
      expect(lock.request).toHaveBeenCalledOnce();
      store(replacement);
    } finally { lock.release(); }
    expect(await pending).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
    expect(resolveOwnedAuthSession(original.accessToken, original.refreshToken)).toEqual(replacement);
  });

  it.each(['different-user', 'new-login-family', 'wrong-access-user', 'malformed', 'expired', 'logout'])(
    'does not adopt or overwrite %s while queued for the browser lock', async scenario => {
      const original = pair();
      store(original);
      const lock = pausedLock();
      const { tryRefreshToken, resolveOwnedAuthSession } = await import('../apiClient');
      const pending = tryRefreshToken();
      try {
        expect(lock.request).toHaveBeenCalledOnce();
        const replacement = scenario === 'different-user' ? pair('other')
          : scenario === 'new-login-family' ? pair('owner', 'new-family')
          : scenario === 'expired' ? pair('owner', 'family', 'new', true)
          : pair('owner', 'family', 'new');
        store(replacement);
        if (scenario === 'wrong-access-user') localStorage.setItem('access_token', pair('other').accessToken);
        if (scenario === 'malformed') localStorage.setItem('refresh_token', 'malformed');
        if (scenario === 'logout') localStorage.clear();
      } finally { lock.release(); }
      const before = [localStorage.getItem('access_token'), localStorage.getItem('refresh_token')];
      expect(await pending).toBe(false);
      expect(fetch).not.toHaveBeenCalled();
      expect([localStorage.getItem('access_token'), localStorage.getItem('refresh_token')]).toEqual(before);
      expect(resolveOwnedAuthSession(original.accessToken, original.refreshToken)).toBeNull();
    },
  );

  it('retries a late original 401 with the matching family already refreshed in another tab', async () => {
    const original = pair();
    const replacement = pair('owner', 'family', 'new');
    store(original);
    let respond!: (response: unknown) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise(resolve => { respond = resolve; }))
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) } as Response);
    const { apiCall } = await import('../apiClient');
    const pending = apiCall('/api/shared-session-probe');
    store(replacement);
    respond({ ok: false, status: 401, json: async () => ({}) });
    expect(await pending).toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(vi.mocked(fetch).mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
    expect(vi.mocked(fetch).mock.calls[1][1]?.headers).toMatchObject({ Authorization: `Bearer ${replacement.accessToken}` });
  });

  it('preserves credentials on a rotation conflict when Web Locks are unavailable', async () => {
    const original = pair();
    store(original);
    const { tryRefreshToken } = await import('../apiClient');
    expect(await tryRefreshToken()).toBe(false);
    expect(fetch).toHaveBeenCalledOnce();
    expect(localStorage.getItem('refresh_token')).toBe(original.refreshToken);
    expect(localStorage.getItem('access_token')).toBe(original.accessToken);
  });
});
