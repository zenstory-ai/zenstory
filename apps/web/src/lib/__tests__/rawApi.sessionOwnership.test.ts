import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// i18next's HTTP backend captures fetch when its module loads. Keep a boot
// fence through teardown as well as the per-case HTTP fixture below.
const bootFetch = vi.hoisted(() => {
  const original = globalThis.fetch
  globalThis.fetch = async input => {
    const path = new URL(String(input), 'http://localhost').pathname
    if (path.startsWith('/locales/') && path.endsWith('.json')) {
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    }
    throw new Error(`OFFLINE bootstrap request blocked: ${path}`)
  }
  return { restore: () => { globalThis.fetch = original } }
})

// Actual public raw APIs + actual apiClient lineage. Actor replacement uses
// controlled native Storage, not a simulated AuthProvider or generation model.
// HTTP, analytics and download side effects are the only replaced boundaries.
vi.mock('../analytics', () => ({ trackEvent: vi.fn(), captureException: vi.fn() }))
type Surface = 'stream' | 'suggestions' | 'steer' | 'export' | 'material' | 'draft'
type Client = typeof import('../apiClient')
type RecordEntry = {
  path: string; method: string; authorization: string | null; language: string | null
  refresh?: string; body?: unknown; form?: FormData; signal?: AbortSignal | null
}
type Outcome = { ok: true; value: unknown } | { ok: false; status?: number; code?: string; retryable?: boolean }
let client: Client
let api: typeof import('../api')
let agent: typeof import('../agentApi')
let records: RecordEntry[]
let unexpected: string[]
let outcomes: Outcome[]
let downloads: string[]
let blobs: Blob[]
let revoked: string[]
let logoutReasons: string[]
let gates: Array<{ settled: () => boolean; release: () => void }>
let pending: Set<Promise<Outcome>>
let controllers: AbortController[]
let route: (record: RecordEntry) => Promise<Response>
let file: File
let onLogout: EventListener
const paths: Record<Surface, string> = {
  stream: '/api/v1/agent/stream', suggestions: '/api/v1/agent/suggest', steer: '/api/v1/agent/steer',
  export: '/api/v1/projects/project-a/export/drafts', material: '/api/v1/projects/project-a/files/upload',
  draft: '/api/v1/projects/project-a/files/upload-drafts',
}
const pair = (actor: 'a' | 'b', generation = 0) => ({
  accessToken: `raw-access-${actor}-${generation}`, refreshToken: `raw-refresh-${actor}-${generation}`,
})
const storage = () => ({ access: localStorage.getItem('access_token'), refresh: localStorage.getItem('refresh_token'),
  user: localStorage.getItem('user'), validated: localStorage.getItem('auth_validated_at') })
function establish(actor: 'a' | 'b', generation = 0) {
  const tokens = pair(actor, generation)
  localStorage.setItem('access_token', tokens.accessToken); localStorage.setItem('refresh_token', tokens.refreshToken)
  localStorage.setItem('user', JSON.stringify({ id: actor })); localStorage.setItem('auth_validated_at', `seed-${actor}`)
}
function deferred<T>(fallback: T) {
  let done = false
  let resolve!: (value: T) => void
  const promise = new Promise<T>(fulfill => { resolve = value => { if (!done) { done = true; fulfill(value) } } })
  gates.push({ settled: () => done, release: () => resolve(fallback) })
  return { promise, resolve }
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const denied = () => json({ detail: 'Original request denied' }, 401)
function rotation(actor: 'a' | 'b', generation: number) {
  const tokens = pair(actor, generation)
  return json({ access_token: tokens.accessToken, refresh_token: tokens.refreshToken, user: { id: actor } })
}
function success(surface: Surface, authorization: string | null) {
  const actor = authorization?.includes('access-b-') ? 'b' : 'a'
  switch (surface) {
    case 'stream': return new Response(`event: content\ndata: {"text":"stream-as-${actor}"}\n\nevent: done\ndata: {}\n\n`, {
      headers: { 'Content-Type': 'text/event-stream' },
    })
    case 'suggestions': return json({ suggestions: [`suggest-as-${actor}`] })
    case 'steer': return json({ message_id: `steer-as-${actor}`, queued: true })
    case 'export': return new Response(`export-as-${actor}`, { headers: {
      'Content-Disposition': "attachment; filename*=UTF-8''%E5%AF%BC%E5%87%BA.txt",
    } })
    case 'material': return json({ id: `material-as-${actor}`, file_type: 'material' })
    case 'draft': return json({ files: [{ id: `draft-as-${actor}` }], total: 1, errors: ['kept partial warning'] })
  }
}
function start(surface: Surface) {
  let raw: Promise<unknown>
  if (surface === 'stream') {
    raw = new Promise<Outcome>(resolve => {
      let text = ''
      controllers.push(agent.streamAgentRequest({ project_id: 'project-a', message: 'private-a', session_id: 'session-a' }, {
        onContent: content => { text += content }, onDone: () => resolve({ ok: true, value: text }),
        onError: (_message, code, retryable) => resolve({ ok: false, code, retryable }),
      }))
    })
  } else if (surface === 'suggestions') raw = agent.fetchSuggestions('project-a', [{ role: 'user', content: 'private-a' }], 3)
  else if (surface === 'steer') raw = agent.sendSteeringRequest('session-a', 'private-a')
  else if (surface === 'export') raw = api.exportApi.exportDrafts('project-a')
  else if (surface === 'material') raw = api.fileApi.upload('project-a', file)
  else raw = api.fileApi.uploadDraft('project-a', file, 'parent-a')
  const observed: Promise<Outcome> = raw.then(value => surface === 'stream' ? value as Outcome : { ok: true, value },
    error => ({ ok: false, status: error instanceof client.ApiError ? error.status : undefined }))
  pending.add(observed)
  void observed.then(result => { outcomes.push(result); pending.delete(observed) })
  return observed
}
function assertDenied(result: Outcome, surface: Surface) {
  if (surface === 'stream') expect(result).toEqual({ ok: false, code: 'AUTH_ERROR', retryable: false })
  else if (surface === 'suggestions') expect(result).toEqual({ ok: true, value: [] })
  else if (surface === 'steer') expect(result).toEqual({ ok: false, status: undefined })
  else expect(result).toEqual({ ok: false, status: 401 })
  expect(downloads).toEqual([])
}
async function assertSuccess(result: Outcome, surface: Surface) {
  const values = { stream: 'stream-as-a', suggestions: ['suggest-as-a'], steer: { message_id: 'steer-as-a', queued: true },
    export: { filename: '导出.txt', includesOutline: false }, material: { id: 'material-as-a', file_type: 'material' },
    draft: { files: [{ id: 'draft-as-a' }], total: 1, errors: ['kept partial warning'] } }
  expect(result).toEqual({ ok: true, value: values[surface] })
  if (surface === 'export') {
    expect(downloads).toEqual(['导出.txt']); expect(await blobs[0].text()).toBe('export-as-a')
    expect(revoked).toEqual(['blob:owned-raw-export'])
  }
}
function assertNoBWork(result: Outcome, before: ReturnType<typeof storage>, surface: Surface) {
  expect({ bRefresh: records.filter(r => r.refresh?.includes('refresh-b-')).length,
    bRequest: records.filter(r => r.authorization?.includes('access-b-')).length, storage: storage() })
    .toEqual({ bRefresh: 0, bRequest: 0, storage: before })
  assertDenied(result, surface)
}

beforeEach(async () => {
  vi.resetModules()
  vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  records = []; unexpected = []; outcomes = []; downloads = []; blobs = []; revoked = []
  logoutReasons = []; gates = []; pending = new Set(); controllers = []
  file = new File(['private-a文稿'], 'private-a.txt', { type: 'text/plain' })
  route = async r => { throw new Error(`OFFLINE missing fixture ${r.path}`) }
  // Install before dynamic imports: actual i18n startup cannot reach network.
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href)
    if (url.pathname.startsWith('/locales/') && url.pathname.endsWith('.json')) return json({})
    const headers = new Headers(init?.headers)
    const r: RecordEntry = { path: url.pathname, method: init?.method ?? 'GET',
      authorization: headers.get('Authorization'), language: headers.get('Accept-Language'), signal: init?.signal }
    if (!client || url.origin !== new URL(client.getApiBase()).origin || ![...Object.values(paths), '/api/auth/refresh'].includes(r.path)) {
      unexpected.push(String(input)); throw new Error('OFFLINE unexpected request')
    }
    if (typeof init?.body === 'string') {
      r.body = JSON.parse(init.body); r.refresh = (r.body as { refresh_token?: string }).refresh_token
    }
    if (init?.body instanceof FormData) r.form = init.body
    records.push(r); return route(r)
  })
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL(blob: Blob) { blobs.push(blob); return 'blob:owned-raw-export' }
    static revokeObjectURL(url: string) { revoked.push(url) }
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download) })
  client = await import('../apiClient'); api = await import('../api'); agent = await import('../agentApi')
  const { default: locale } = await import('../i18n')
  if (!locale.isInitialized) await new Promise<void>(resolve => {
    const ready = () => { locale.off('initialized', ready); resolve() }
    locale.on('initialized', ready)
  })
  onLogout = event => logoutReasons.push((event as CustomEvent<{ reason: string }>).detail.reason)
  window.addEventListener('auth:logout', onLogout)
  establish('a'); localStorage.setItem('zenstory-language', 'en')
})
afterEach(async () => {
  const unsettled = gates.filter(g => !g.settled()).length
  for (const gate of gates) gate.release()
  await Promise.all(pending)
  for (const record of records) {
    if (record.form) {
      expect(record.form.get(record.path.endsWith('upload-drafts') ? 'files' : 'file')).toBe(file)
      expect(await file.text()).toBe('private-a文稿')
      expect(Array.from(record.form.keys())).toEqual(record.path.endsWith('upload-drafts') ? ['files', 'parent_id'] : ['file'])
      if (record.path.endsWith('upload-drafts')) expect(record.form.get('parent_id')).toBe('parent-a')
    }
  }
  console.info('RAW_AUTH_OBSERVATION', JSON.stringify({ case: expect.getState().currentTestName,
    records: records.map(({ form, signal, ...r }) => ({ ...r, formKeys: form ? Array.from(form.keys()) : undefined,
      aborted: signal?.aborted })), outcomes, storage: storage(), logoutReasons, downloads,
    cleanup: { unsettled, pending: pending.size }, unexpected }))
  for (const controller of controllers) controller.abort()
  window.removeEventListener('auth:logout', onLogout)
  client.clearAuthStorage('owned_fixture_cleanup'); localStorage.clear(); sessionStorage.clear()
  expect(client.resolveOwnedAuthSession(pair('a').accessToken, pair('a').refreshToken)).toBeNull()
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.resetModules()
  expect(unexpected).toEqual([]); expect(unsettled).toBe(0); expect(pending.size).toBe(0)
  expect(document.querySelectorAll('a[download]')).toHaveLength(0)
})
afterAll(async () => {
  await (window as unknown as { happyDOM: { whenAsyncComplete(): Promise<void> } }).happyDOM.whenAsyncComplete()
  bootFetch.restore()
})

it('stream cancellation keeps the actual AbortController signal and suppresses callbacks', async () => {
  let finish!: () => void
  const finished = new Promise<void>(resolve => { finish = resolve })
  route = async r => new Promise<Response>((_resolve, reject) => {
    r.signal?.addEventListener('abort', () => { reject(new DOMException('Controlled abort', 'AbortError')); finish() }, { once: true })
  })
  const onError = vi.fn(); const onDone = vi.fn()
  const controller = agent.streamAgentRequest({ project_id: 'project-a', message: 'private-a' }, { onError, onDone })
  controllers.push(controller)
  expect(records[0].signal).toBe(controller.signal)
  controller.abort(); await finished
  // Drain the real fetch/catch promise chain, not a replacement stream model.
  await new Promise<void>(resolve => setTimeout(resolve, 0))
  expect(records[0].signal?.aborted).toBe(true)
  expect(onError).not.toHaveBeenCalled(); expect(onDone).not.toHaveBeenCalled()
  expect(records).toHaveLength(1)
})

describe.each(['stream', 'suggestions', 'steer', 'export', 'material', 'draft'] as const)('actual raw %s owner', surface => {
  it('ordinary current200 retains payload, output and session', async () => {
    const before = storage(); route = async r => success(surface, r.authorization)
    await assertSuccess(await start(surface), surface)
    expect(records).toHaveLength(1); expect(records[0].authorization).toBe(`Bearer ${pair('a').accessToken}`)
    expect(storage()).toEqual(before)
    expect(records[0].method).toBe(surface === 'export' ? 'GET' : 'POST')
    if (['stream', 'suggestions', 'steer'].includes(surface)) {
      expect(records[0].language).toBe('en'); expect(records[0].body).toMatchObject(surface === 'steer'
        ? { session_id: 'session-a', message: 'private-a' } : { project_id: 'project-a' })
    }
  })
  it('current401 performs one actual rotation and A1 retry', async () => {
    route = async r => r.path === '/api/auth/refresh' ? rotation('a', 1)
      : r.authorization === `Bearer ${pair('a').accessToken}` ? denied() : success(surface, r.authorization)
    await assertSuccess(await start(surface), surface)
    expect(records.map(r => r.authorization)).toEqual([`Bearer ${pair('a').accessToken}`, null, `Bearer ${pair('a', 1).accessToken}`])
    expect(records[1].refresh).toBe(pair('a').refreshToken)
    if (records[0].form) expect(records[2].form).toBe(records[0].form)
    if (records[0].signal) expect(records[2].signal).toBe(records[0].signal)
  })
  it('confirmed A2 lineage replays once without refreshing A3', async () => {
    const first = deferred(denied()); let generation = 0
    route = async r => r.path === '/api/auth/refresh' ? rotation('a', ++generation)
      : r.authorization === `Bearer ${pair('a').accessToken}` ? first.promise : success(surface, r.authorization)
    const request = start(surface)
    expect(await client.tryRefreshToken()).toBe(true); expect(await client.tryRefreshToken()).toBe(true)
    first.resolve(denied()); await assertSuccess(await request, surface)
    expect(records).toHaveLength(4); expect(generation).toBe(2)
    expect(records.at(-1)?.authorization).toBe(`Bearer ${pair('a', 2).accessToken}`)
  })
  it('A401 after unrelated B establishment cannot refresh, replay or clear B', async () => {
    const first = deferred(denied())
    route = async r => r.path === '/api/auth/refresh' ? rotation('b', 1)
      : r.authorization === `Bearer ${pair('a').accessToken}` ? first.promise : success(surface, r.authorization)
    const request = start(surface); establish('b'); const before = storage(); first.resolve(denied())
    assertNoBWork(await request, before, surface); expect(records).toHaveLength(1)
  })
  it('late401 after actual logout cannot issue refresh/retry', async () => {
    const first = deferred(denied()); route = async () => first.promise
    const request = start(surface); client.clearAuthStorage('controlled_logout'); const before = storage()
    first.resolve(denied()); assertDenied(await request, surface)
    expect(records).toHaveLength(1); expect(storage()).toEqual(before)
  })
  it.each(['response', 'json'] as const)('B replacement during actual A refresh %s cannot clear B', async boundary => {
    const refresh = deferred(rotation('a', 1)); const body = deferred({ access_token: pair('a', 1).accessToken,
      refresh_token: pair('a', 1).refreshToken, user: { id: 'a' } })
    let enter!: () => void; const entered = new Promise<void>(resolve => { enter = resolve })
    const response = rotation('a', 1)
    if (boundary === 'json') Object.defineProperty(response, 'json', { value: () => { enter(); return body.promise } })
    route = async r => {
      if (r.path !== '/api/auth/refresh') return denied()
      if (boundary === 'response') { enter(); return refresh.promise }
      return response
    }
    const request = start(surface); await entered; establish('b'); const before = storage()
    refresh.resolve(response); body.resolve({ access_token: pair('a', 1).accessToken,
      refresh_token: pair('a', 1).refreshToken, user: { id: 'a' } })
    assertNoBWork(await request, before, surface); expect(records).toHaveLength(2)
  })
  it('logout during actual pending refresh cannot resurrect A', async () => {
    const refresh = deferred(rotation('a', 1)); let enter!: () => void
    const entered = new Promise<void>(resolve => { enter = resolve })
    route = async r => { if (r.path !== '/api/auth/refresh') return denied(); enter(); return refresh.promise }
    const request = start(surface); await entered; client.clearAuthStorage('controlled_logout'); const before = storage()
    refresh.resolve(rotation('a', 1)); assertDenied(await request, surface)
    expect(storage()).toEqual(before); expect(records).toHaveLength(2)
  })
  it('another real refresh awaiter replacing B before retry cannot replay B', async () => {
    const first = deferred(denied()); const refresh = deferred(rotation('a', 1))
    route = async r => r.path === '/api/auth/refresh' ? refresh.promise
      : r.authorization === `Bearer ${pair('a').accessToken}` ? first.promise : success(surface, r.authorization)
    const request = start(surface)
    let before!: ReturnType<typeof storage>
    const rotated = client.tryRefreshToken().then(ok => { expect(ok).toBe(true); establish('b'); before = storage() })
    first.resolve(denied())
    await Promise.resolve(); await Promise.resolve()
    refresh.resolve(rotation('a', 1)); await rotated
    assertNoBWork(await request, before, surface); expect(records).toHaveLength(2)
  })
  it.each([503, 403])('current refresh failure%s retains existing failure contract', async status => {
    const before = storage(); route = async r => r.path === '/api/auth/refresh' ? json({ detail: 'refresh failed' }, status) : denied()
    assertDenied(await start(surface), surface); expect(records).toHaveLength(2)
    const clearsOnTransient = surface === 'stream' || surface === 'steer'
    expect(storage()).toEqual(status === 403 || clearsOnTransient
      ? { access: null, refresh: null, user: null, validated: null } : before)
  })
  it('retry401 does not issue a second refresh and preserves failure', async () => {
    route = async r => r.path === '/api/auth/refresh' ? rotation('a', 1) : denied()
    const result = await start(surface)
    if (surface === 'stream') expect(result).toEqual({ ok: false, code: 'HTTP_ERROR', retryable: false })
    else assertDenied(result, surface)
    expect(records).toHaveLength(3); expect(storage().refresh).toBe(pair('a', 1).refreshToken)
  })
})
