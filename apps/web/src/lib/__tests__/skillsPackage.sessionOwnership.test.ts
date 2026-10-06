import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Actual package APIs + actual shared refresh lineage. Identity replacement is
// a controlled native Storage command, not an AuthProvider/App identity proof.
// Only HTTP and the browser's download side effects are intercepted.
type Surface = 'import' | 'export'
type Client = typeof import('../apiClient')
type APIs = typeof import('../api')
type RecordEntry = {
  url: string; path: string; method: string; authorization: string | null
  language: string | null; refresh?: string; form?: FormData; file?: File
}
type Outcome = { ok: true; value: unknown } | {
  ok: false; name: string; status?: number; rawMessage?: string; details?: Record<string, unknown>
}
let client: Client
let apis: APIs
let records: RecordEntry[]
let unexpected: string[]
let results: Outcome[]
let gates: { settled: () => boolean; release: () => void }[]
let pending: Set<Promise<Outcome>>
let route: (record: RecordEntry) => Promise<Response>
let file: File
let downloads: { filename: string; href: string }[]
let blobs: Blob[]
let revoked: string[]
let logoutReasons: string[]
let onLogout: EventListener

const pair = (actor: 'a' | 'b', generation = 0) => ({
  accessToken: `package-access-${actor}-${generation}`,
  refreshToken: `package-refresh-${actor}-${generation}`,
})
const storage = () => ({
  access: localStorage.getItem('access_token'), refresh: localStorage.getItem('refresh_token'),
  user: localStorage.getItem('user'), validated: localStorage.getItem('auth_validated_at'),
})
function establish(actor: 'a' | 'b', generation = 0) {
  const tokens = pair(actor, generation)
  localStorage.setItem('access_token', tokens.accessToken)
  localStorage.setItem('refresh_token', tokens.refreshToken)
  localStorage.setItem('user', JSON.stringify({ id: actor }))
  localStorage.setItem('auth_validated_at', `seed-${actor}-${generation}`)
}
function deferred<T>(fallback: T) {
  let done = false
  let resolve!: (value: T) => void
  const promise = new Promise<T>(fulfill => { resolve = value => {
    if (!done) { done = true; fulfill(value) }
  } })
  gates.push({ settled: () => done, release: () => resolve(fallback) })
  return { promise, resolve }
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const denialPayload = { detail: 'Denied original A package request', error_detail: { reason: 'entry-a-denied' } }
const denied = () => json(denialPayload, 401)
const rotated = (actor: 'a' | 'b', generation: number) => {
  const tokens = pair(actor, generation)
  return json({ access_token: tokens.accessToken, refresh_token: tokens.refreshToken, user: { id: actor } })
}
function success(surface: Surface, authorization: string | null) {
  const actor = authorization?.includes('access-b-') ? 'b' : 'a'
  return surface === 'import' ? json({ skill: { id: `imported-as-${actor}` }, warnings: ['scripts/run.py dropped'] })
    : new Response(`zip-bytes-as-${actor}`, { headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': "attachment; filename=\"fallback.zip\"; filename*=UTF-8''%E6%8A%80%E8%83%BD.zip",
    } })
}
function start(surface: Surface) {
  const raw = surface === 'import' ? apis.skillsApi.importSkill(file) : apis.skillsApi.exportSkill('skill-a', 'fallback')
  const observed: Promise<Outcome> = raw.then(value => ({ ok: true, value }), (error: unknown) => ({
    ok: false, name: error instanceof Error ? error.name : 'unknown',
    status: error instanceof client.ApiError ? error.status : undefined,
    rawMessage: error instanceof client.ApiError ? error.rawMessage : undefined,
    details: error instanceof client.ApiError ? error.details : undefined,
  }))
  pending.add(observed)
  void observed.then(result => { results.push(result); pending.delete(observed) })
  return observed
}
function assertDenied(result: Outcome) {
  expect(result).toEqual({ ok: false, name: 'ApiError', status: 401,
    rawMessage: denialPayload.detail, details: denialPayload.error_detail })
  expect(downloads).toEqual([])
  expect(blobs).toEqual([])
}
function assertNoReplacementWork(result: Outcome, before: ReturnType<typeof storage>) {
  expect({ result, bRefreshes: records.filter(r => r.refresh?.includes('refresh-b-')).length,
    bRequests: records.filter(r => r.authorization?.includes('access-b-')).length,
    storage: storage(), downloads }).toEqual({
    result: { ok: false, name: 'ApiError', status: 401,
      rawMessage: denialPayload.detail, details: denialPayload.error_detail },
    bRefreshes: 0, bRequests: 0, storage: before, downloads: [],
  })
  assertDenied(result)
}
async function assertSuccess(result: Outcome, surface: Surface) {
  expect(result).toEqual({ ok: true, value: surface === 'import'
    ? { skill: { id: 'imported-as-a' }, warnings: ['scripts/run.py dropped'] } : undefined })
  if (surface === 'export') {
    expect(downloads).toEqual([{ filename: '技能.zip', href: 'blob:owned-package' }])
    expect(await blobs[0].text()).toBe('zip-bytes-as-a')
    expect(revoked).toEqual(['blob:owned-package'])
  }
}

beforeEach(async () => {
  vi.resetModules()
  // Repo setup installs spy Storage: this suite reinstalls native Storage itself.
  vi.stubGlobal('localStorage', new window.Storage())
  vi.stubGlobal('sessionStorage', new window.Storage())
  expect(localStorage).toBeInstanceOf(window.Storage)
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  records = []; unexpected = []; results = []; downloads = []; blobs = []; revoked = []
  gates = []; pending = new Set(); logoutReasons = []
  file = new File(['fake-local-技能-package'], 'actor-a.zip', { type: 'application/zip' })
  route = async record => { throw new Error(`OFFLINE missing fixture: ${record.path}`) }
  // No module invokes locale loading; imports are real, with a fetch fence in
  // place before any product import. Unexpected endpoints never reach network.
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href)
    const headers = new Headers(init?.headers)
    const record: RecordEntry = {
      url: String(input), path: url.pathname, method: init?.method ?? 'GET',
      authorization: headers.get('Authorization'), language: headers.get('Accept-Language'),
    }
    if (!client || url.origin !== new URL(client.getApiBase()).origin || ![
      '/api/v1/skills/import', '/api/v1/skills/skill-a/export', '/api/auth/refresh',
    ].includes(record.path)) {
      unexpected.push(String(input)); throw new Error('OFFLINE blocked unexpected endpoint')
    }
    if (typeof init?.body === 'string') record.refresh = (JSON.parse(init.body) as { refresh_token?: string }).refresh_token
    if (init?.body instanceof FormData) {
      record.form = init.body
      record.file = init.body.get('file') as File
    }
    records.push(record)
    return route(record)
  })
  vi.stubGlobal('URL', URL)
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL(blob: Blob) { blobs.push(blob); return 'blob:owned-package' }
    static revokeObjectURL(url: string) { revoked.push(url) }
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push({ filename: this.download, href: this.href })
  })
  client = await import('../apiClient')
  apis = await import('../api')
  onLogout = event => logoutReasons.push((event as CustomEvent<{ reason: string }>).detail.reason)
  window.addEventListener('auth:logout', onLogout)
  establish('a'); localStorage.setItem('zenstory-language', 'en')
})

afterEach(async () => {
  const unsettledAtEnd = gates.filter(g => !g.settled()).length
  for (const gate of gates) gate.release()
  await Promise.all(pending)
  for (const record of records) {
    if (record.form) {
      expect(Array.from(record.form.keys())).toEqual(['file'])
      expect(record.file).toBe(file)
      expect(record.file?.name).toBe('actor-a.zip')
      expect(record.file?.type).toBe('application/zip')
      expect(await record.file?.text()).toBe('fake-local-技能-package')
      expect(record.file?.size).toBe(new TextEncoder().encode('fake-local-技能-package').byteLength)
      expect(record.method).toBe('POST')
    }
    if (record.path.endsWith('/export')) expect(record.method).toBe('GET')
    expect(record.url).toBe(`${client.getApiBase()}${record.path}`)
  }
  console.info('M08_PACKAGE_OBSERVATION', JSON.stringify({ case: expect.getState().currentTestName,
    identityCommand: 'controlled native Storage / actual clearAuthStorage',
    requests: records.map(({ form, file: uploaded, ...r }) => ({ ...r,
      ...(form ? { formKeys: Array.from(form.keys()), fileName: uploaded?.name, fileBytes: uploaded?.size } : {}),
    })), results, storage: storage(), downloads, revoked, logoutReasons, unexpected,
    cleanup: { unsettledAtEnd, pending: pending.size, remainingAnchors: document.querySelectorAll('a[download]').length },
  }))
  window.removeEventListener('auth:logout', onLogout)
  client.clearAuthStorage('owned_fixture_cleanup')
  expect(client.resolveOwnedAuthSession(pair('a').accessToken, pair('a').refreshToken)).toBeNull()
  localStorage.clear(); sessionStorage.clear()
  expect(storage()).toEqual({ access: null, refresh: null, user: null, validated: null })
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.resetModules()
  expect(unexpected).toEqual([])
  expect(unsettledAtEnd).toBe(0)
  expect(pending.size).toBe(0)
  expect(document.querySelectorAll('a[download]')).toHaveLength(0)
})

describe.each(['import', 'export'] as const)('actual skill package %s ownership', surface => {
  it.each(['en', null])('current200 keeps payload/download and language %s', async language => {
    if (language) localStorage.setItem('zenstory-language', language)
    else localStorage.removeItem('zenstory-language')
    const before = storage()
    route = async record => success(surface, record.authorization)
    await assertSuccess(await start(surface), surface)
    expect(records).toHaveLength(1)
    expect(records[0].authorization).toBe(`Bearer ${pair('a').accessToken}`)
    expect(records[0].language).toBe(language ?? 'zh')
    expect(storage()).toEqual(before)
  })

  it('current401 refreshes once and replays with confirmed A1', async () => {
    route = async r => r.path === '/api/auth/refresh' ? rotated('a', 1)
      : r.authorization === `Bearer ${pair('a').accessToken}` ? denied() : success(surface, r.authorization)
    await assertSuccess(await start(surface), surface)
    expect(records.map(r => r.authorization)).toEqual([`Bearer ${pair('a').accessToken}`, null, `Bearer ${pair('a', 1).accessToken}`])
    expect(records[1].refresh).toBe(pair('a').refreshToken)
    if (surface === 'import') expect(records[2].form).toBe(records[0].form)
    expect(client.resolveOwnedAuthSession(pair('a').accessToken, pair('a').refreshToken)).toEqual(pair('a', 1))
  })

  it('confirmed multigeneration rotation reuses A2 without a third refresh', async () => {
    const first = deferred(denied()); let generation = 0
    route = async r => r.path === '/api/auth/refresh' ? rotated('a', ++generation)
      : r.authorization === `Bearer ${pair('a').accessToken}` ? first.promise : success(surface, r.authorization)
    const request = start(surface)
    expect(await client.tryRefreshToken()).toBe(true)
    expect(await client.tryRefreshToken()).toBe(true)
    first.resolve(denied())
    await assertSuccess(await request, surface)
    expect(records).toHaveLength(4)
    expect(generation).toBe(2)
    expect(records.at(-1)?.authorization).toBe(`Bearer ${pair('a', 2).accessToken}`)
  })

  it.each(['direct', 'logout-first'] as const)('late A401 cannot use replacement B: %s', async mode => {
    const first = deferred(denied())
    route = async r => r.path === '/api/auth/refresh' ? rotated('b', 1)
      : r.authorization === `Bearer ${pair('a').accessToken}` ? first.promise : success(surface, r.authorization)
    const request = start(surface)
    if (mode === 'logout-first') client.clearAuthStorage('controlled_logout')
    establish('b'); const before = storage()
    first.resolve(denied())
    assertNoReplacementWork(await request, before)
    expect(records).toHaveLength(1)
  })

  it.each(['response', 'json'] as const)('replacement B while A refresh awaits %s stays intact', async boundary => {
    const refresh = deferred(rotated('a', 1)); const body = deferred({
      access_token: pair('a', 1).accessToken, refresh_token: pair('a', 1).refreshToken, user: { id: 'a' },
    })
    let started!: () => void
    const atBoundary = new Promise<void>(resolve => { started = resolve })
    const response = rotated('a', 1)
    if (boundary === 'json') Object.defineProperty(response, 'json', { value: () => { started(); return body.promise } })
    route = async r => {
      if (r.path !== '/api/auth/refresh') return denied()
      if (boundary === 'response') { started(); return refresh.promise }
      return response
    }
    const request = start(surface)
    await atBoundary
    establish('b'); const before = storage()
    refresh.resolve(response); body.resolve({ access_token: pair('a', 1).accessToken,
      refresh_token: pair('a', 1).refreshToken, user: { id: 'a' } })
    assertNoReplacementWork(await request, before)
    expect(records).toHaveLength(2)
    expect(records[1].refresh).toBe(pair('a').refreshToken)
  })

  it('another real refresh awaiter replacing B before replay cannot export/import B', async () => {
    const first = deferred(denied()); const refresh = deferred(rotated('a', 1))
    route = async r => r.path === '/api/auth/refresh' ? refresh.promise
      : r.authorization === `Bearer ${pair('a').accessToken}` ? first.promise : success(surface, r.authorization)
    const request = start(surface)
    let expectedB!: ReturnType<typeof storage>
    const rotation = client.tryRefreshToken().then(ok => { expect(ok).toBe(true); establish('b'); expectedB = storage() })
    first.resolve(denied())
    // The wrapper is a later awaiter of the real shared refresh promise.
    await Promise.resolve(); await Promise.resolve()
    refresh.resolve(rotated('a', 1))
    await rotation
    assertNoReplacementWork(await request, expectedB)
    expect(records).toHaveLength(2)
  })

  it('late401 after actual logout preserves original denial without replay', async () => {
    const first = deferred(denied())
    route = async () => first.promise
    const request = start(surface)
    client.clearAuthStorage('controlled_logout'); const before = storage()
    first.resolve(denied())
    assertDenied(await request)
    expect(records).toHaveLength(1)
    expect(storage()).toEqual(before)
  })

  it.each(['access', 'refresh', 'both'] as const)('missing %s credentials must not refresh/replay', async missing => {
    if (missing === 'access' || missing === 'both') localStorage.removeItem('access_token')
    if (missing === 'refresh' || missing === 'both') localStorage.removeItem('refresh_token')
    const before = storage()
    route = async r => r.path === '/api/auth/refresh' ? rotated('a', 1) : denied()
    assertDenied(await start(surface))
    expect(records).toHaveLength(1)
    expect(storage()).toEqual(before)
  })

  it('same access with unowned replacement refresh is not the original pair', async () => {
    const first = deferred(denied())
    route = async r => r.path === '/api/auth/refresh' ? rotated('b', 1)
      : r.authorization === `Bearer ${pair('a').accessToken}` ? first.promise : success(surface, r.authorization)
    const request = start(surface)
    localStorage.setItem('refresh_token', pair('b').refreshToken); const before = storage()
    first.resolve(denied())
    assertNoReplacementWork(await request, before)
    expect(records).toHaveLength(1)
  })

  it('retry401 decodes failure and never refreshes a second time', async () => {
    route = async r => r.path === '/api/auth/refresh' ? rotated('a', 1) : denied()
    assertDenied(await start(surface))
    expect(records).toHaveLength(3)
    expect(storage().refresh).toBe(pair('a', 1).refreshToken)
  })

  it.each(['network', '503', '403'] as const)('refresh %s keeps existing transient/denial policy', async failure => {
    const before = storage()
    route = async r => {
      if (r.path !== '/api/auth/refresh') return denied()
      if (failure === 'network') throw new TypeError('fake offline refresh')
      return json({ detail: 'Refresh failure' }, Number(failure))
    }
    assertDenied(await start(surface))
    expect(records).toHaveLength(2)
    expect(storage()).toEqual(failure === '403'
      ? { access: null, refresh: null, user: null, validated: null } : before)
    expect(logoutReasons).toEqual(failure === '403' ? ['refresh_failed'] : [])
  })
})

it('export without Content-Disposition retains fallback filename and cleanup', async () => {
  route = async () => new Response('fallback-bytes')
  expect(await start('export')).toEqual({ ok: true, value: undefined })
  expect(downloads).toEqual([{ filename: 'fallback.zip', href: 'blob:owned-package' }])
  expect(revoked).toEqual(['blob:owned-package'])
  expect(await blobs[0].text()).toBe('fallback-bytes')
})
