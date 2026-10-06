import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Real wrappers/client/refresh ledger and happy-dom native Storage. Only the
// network response boundary is replaced. Actor establishment below is a
// controlled Storage command, not an AuthProvider/App identity-flow proof.
type Surface = 'multipart' | 'screenshot'
type Client = typeof import('../apiClient')
type Feedback = typeof import('../feedbackApi')
type Admin = typeof import('../adminApi')
type Outcome = { outcome: 'fulfilled'; value: unknown } | {
  outcome: 'rejected'; name: string; status?: number; message: string
}
type RequestRecord = {
  url: string; path: string; method: string; headers: Record<string, string>
  authorization: string | null; language: string | null
  refreshToken?: string; form?: Record<string, unknown>; fileContents?: Record<string, string>
}

let client: Client
let feedback: Feedback
let admin: Admin
let requests: RequestRecord[]
let unexpected: string[]
let logoutReasons: string[]
let outcomes: Outcome[]
let bodyReads: Promise<void>[]
let pending: Set<Promise<Outcome>>
let gates: { settled: () => boolean; release: () => void }[]
let route: (request: RequestRecord) => Promise<Response>
let onLogout: EventListener

const authState = () => ({
  access: localStorage.getItem('access_token'),
  refresh: localStorage.getItem('refresh_token'),
  user: localStorage.getItem('user'),
  validated: localStorage.getItem('auth_validated_at'),
})
const pair = (actor: 'a' | 'b', generation = 0) => ({
  accessToken: `access-${actor}-${generation}`, refreshToken: `refresh-${actor}-${generation}`,
})
function establish(actor: 'a' | 'b', generation = 0) {
  const tokens = pair(actor, generation)
  localStorage.setItem('access_token', tokens.accessToken)
  localStorage.setItem('refresh_token', tokens.refreshToken)
  localStorage.setItem('user', JSON.stringify({ id: actor, username: `actor-${actor}` }))
  localStorage.setItem('auth_validated_at', `seed-${actor}-${generation}`)
}
function replaceWithB(logoutFirst = false) {
  if (logoutFirst) client.clearAuthStorage('controlled_actor_logout')
  establish('b')
  return authState()
}
function deferred<T>(fallback: T) {
  let resolve!: (value: T) => void
  let done = false
  const promise = new Promise<T>(fulfill => {
    resolve = value => { if (!done) { done = true; fulfill(value) } }
  })
  gates.push({ settled: () => done, release: () => resolve(fallback) })
  return { promise, resolve }
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const denied = () => json({ detail: 'Session request denied' }, 401)
function refreshResponse(actor: 'a' | 'b', generation: number) {
  const tokens = pair(actor, generation)
  return json({ access_token: tokens.accessToken, refresh_token: tokens.refreshToken,
    user: { id: actor, username: `actor-${actor}` } })
}
const expectedAValue = (surface: Surface) => surface === 'multipart'
  ? { id: 'feedback-as-a', message: 'Submitted', created_at: '2026-10-06T00:00:00Z' }
  : 'private-screenshot-as-a'
function successful(surface: Surface, authorization = 'Bearer access-a-0') {
  const actor = authorization.includes('access-b-') ? 'b' : 'a'
  return surface === 'multipart'
    ? json({ id: `feedback-as-${actor}`, message: 'Submitted', created_at: '2026-10-06T00:00:00Z' })
    : new Response(`private-screenshot-as-${actor}`, { headers: { 'Content-Type': 'image/png' } })
}
async function observe(promise: Promise<unknown>): Promise<Outcome> {
  const settled = promise.then(async value => {
    const outcome: Outcome = { outcome: 'fulfilled', value: value instanceof Blob ? await value.text() : value }
    outcomes.push(outcome)
    return outcome
  }, (error: unknown) => {
    const outcome: Outcome = {
      outcome: 'rejected', name: error instanceof Error ? error.name : 'unknown',
      message: error instanceof Error ? error.message : String(error),
      status: error instanceof client.ApiError ? error.status : undefined,
    }
    outcomes.push(outcome)
    return outcome
  })
  pending.add(settled)
  void settled.then(() => pending.delete(settled))
  return settled
}
function start(surface: Surface) {
  return observe(surface === 'multipart'
    ? feedback.feedbackApi.submit({
      issueText: 'Private issue belonging to A', sourcePage: 'editor', sourceRoute: '/project/a',
      screenshot: new File(['fake-local-png'], 'actor-a.png', { type: 'image/png' }),
      debugContext: { project_id: 'project-a', trace_id: 'trace-a' },
    })
    : admin.adminApi.getFeedbackScreenshotBlob('feedback-owned-by-a'))
}
function assertARequest(record: RequestRecord, surface: Surface) {
  expect(record.authorization).toBe('Bearer access-a-0')
  expect(record.language).toBe('en')
  expect(record.method).toBe(surface === 'multipart' ? 'POST' : 'GET')
  expect(record.path).toBe(surface === 'multipart'
    ? '/api/v1/feedback' : '/api/admin/feedback/feedback-owned-by-a/screenshot')
  expect(record.url).toBe(`${client.getApiBase()}${record.path}`)
  if (surface === 'multipart') expect(record.form).toEqual({
    issue_text: 'Private issue belonging to A', source_page: 'editor', source_route: '/project/a',
    project_id: 'project-a', trace_id: 'trace-a',
    screenshot: { name: 'actor-a.png', type: 'image/png', size: 14 },
  })
}
function assertCanceled(outcome: Outcome) {
  expect(outcome.outcome).toBe('rejected')
  if (outcome.outcome === 'rejected') {
    expect(outcome.name).toBe('ApiError')
    expect(outcome.status).toBe(401)
  }
}
function assertNoBWork(outcome: Outcome, expectedB: ReturnType<typeof authState>) {
  // The aggregate assertion exposes request/response/storage effects even when
  // the obsolete request unexpectedly fulfills. No helper/generation imitation.
  expect({
    outcome: outcome.outcome,
    bRefreshes: requests.filter(item => item.refreshToken?.startsWith('refresh-b-')).length,
    bReplays: requests.filter(item => item.authorization?.includes('access-b-')).length,
    storage: authState(),
  }).toEqual({ outcome: 'rejected', bRefreshes: 0, bReplays: 0, storage: expectedB })
  assertCanceled(outcome)
}

beforeEach(async () => {
  vi.resetModules()
  vi.stubGlobal('localStorage', new window.Storage())
  vi.stubGlobal('sessionStorage', new window.Storage())
  localStorage.clear()
  sessionStorage.clear()
  expect(localStorage).toBe(window.localStorage)
  expect(localStorage).toBeInstanceOf(window.Storage)
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  client = await import('../apiClient')
  feedback = await import('../feedbackApi')
  admin = await import('../adminApi')
  requests = []; unexpected = []; logoutReasons = []; outcomes = []
  gates = []; pending = new Set(); bodyReads = []
  onLogout = event => logoutReasons.push((event as CustomEvent<{ reason: string }>).detail.reason)
  window.addEventListener('auth:logout', onLogout)
  establish('a')
  localStorage.setItem('zenstory-language', 'en')
  route = async record => {
    unexpected.push(record.path)
    throw new Error(`OFFLINE unexpected fixture request: ${record.path}`)
  }
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    const headers = new Headers(init?.headers)
    const record: RequestRecord = {
      url: String(input), path: url.pathname, method: init?.method ?? 'GET',
      headers: Object.fromEntries(headers.entries()),
      authorization: headers.get('Authorization'), language: headers.get('Accept-Language'),
    }
    // Recognize only this test's actual API and refresh paths, never real fetch.
    if (url.origin !== new URL(client.getApiBase()).origin || ![
      '/api/v1/feedback', '/api/admin/feedback/feedback-owned-by-a/screenshot',
      '/api/auth/refresh', '/api/v1/c6-session-control',
    ].includes(record.path)) {
      unexpected.push(String(input))
      throw new Error('OFFLINE: blocked unrecognized request')
    }
    if (typeof init?.body === 'string') {
      const body = JSON.parse(init.body) as { refresh_token?: string }
      record.refreshToken = body.refresh_token
    }
    if (init?.body instanceof FormData) {
      record.form = Object.fromEntries(Array.from(init.body.entries(), ([key, value]) => [key,
        value instanceof File ? { name: value.name, type: value.type, size: value.size } : value,
      ]))
    }
    if (init?.body instanceof FormData) {
      for (const [key, value] of init.body.entries()) {
        if (value instanceof File) bodyReads.push(value.text().then(text => {
          record.fileContents = { ...record.fileContents, [key]: text }
        }))
      }
    }
    requests.push(record)
    return route(record)
  })
})

afterEach(async () => {
  const unsettledAtEnd = gates.filter(gate => !gate.settled()).length
  for (const gate of gates) gate.release()
  await Promise.all(pending)
  await Promise.all(bodyReads)
  for (const record of requests) {
    if (record.form) expect(record.fileContents).toEqual({ screenshot: 'fake-local-png' })
  }
  console.info('M19_SESSION_OBSERVATION', JSON.stringify({
    case: expect.getState().currentTestName, identityCommand: 'real clearAuthStorage / controlled native Storage establishment',
    requests, outcomes, storage: authState(), logoutReasons, unexpected,
    lineageFromA0: client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0'),
    cleanup: { unsettledAtEnd, pending: pending.size, nativeStorage: !vi.isMockFunction(localStorage.getItem) },
  }))
  window.removeEventListener('auth:logout', onLogout)
  client.clearAuthStorage('owned_fixture_cleanup')
  localStorage.clear(); sessionStorage.clear()
  expect(authState()).toEqual({ access: null, refresh: null, user: null, validated: null })
  expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toBeNull()
  vi.unstubAllGlobals()
  vi.resetModules()
  expect(unexpected).toEqual([])
  expect(unsettledAtEnd).toBe(0)
  expect(pending.size).toBe(0)
})

describe.each(['multipart', 'screenshot'] as const)('actual %s custom-fetch ownership', surface => {
  it('control: ordinary current A200 retains payload/blob and credentials', async () => {
    const before = authState()
    route = async record => successful(surface, record.authorization ?? '')
    const outcome = await start(surface)
    expect(outcome).toEqual({ outcome: 'fulfilled', value: expectedAValue(surface) })
    assertARequest(requests[0], surface)
    expect(requests).toHaveLength(1)
    expect(authState()).toEqual(before)
    expect(logoutReasons).toEqual([])
  })

  it('control: own A401 refreshes and replays with its actual confirmed A1 pair', async () => {
    route = async record => record.path === '/api/auth/refresh'
      ? refreshResponse('a', 1)
      : record.authorization === 'Bearer access-a-0' ? denied() : successful(surface, record.authorization ?? '')
    const outcome = await start(surface)
    expect(outcome).toEqual({ outcome: 'fulfilled', value: expectedAValue(surface) })
    assertARequest(requests[0], surface)
    expect(requests.map(item => item.authorization)).toEqual(['Bearer access-a-0', null, 'Bearer access-a-1'])
    expect(requests[1].refreshToken).toBe('refresh-a-0')
    expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toEqual(pair('a', 1))
    if (surface === 'multipart') expect(requests[2].form).toEqual(requests[0].form)
  })

  it('control: delayed A401 retains healthy multigeneration refresh lineage', async () => {
    const first = deferred(denied())
    let generation = 0
    route = async record => record.path === '/api/auth/refresh'
      ? refreshResponse('a', ++generation)
      : record.authorization === 'Bearer access-a-0' ? first.promise : successful(surface, record.authorization ?? '')
    const request = start(surface)
    expect(await client.tryRefreshToken()).toBe(true)
    expect(await client.tryRefreshToken()).toBe(true)
    expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toEqual(pair('a', 2))
    first.resolve(denied())
    const outcome = await request
    expect(outcome).toEqual({ outcome: 'fulfilled', value: expectedAValue(surface) })
    expect(requests).toHaveLength(4)
    expect(generation).toBe(2)
    expect(requests.at(-1)?.authorization).toBe('Bearer access-a-2')
    if (surface === 'multipart') expect(requests.at(-1)?.form).toEqual(requests[0].form)
    const latest = authState()
    expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toEqual({
      accessToken: latest.access, refreshToken: latest.refresh,
    })
    expect(requests.some(item => item.authorization?.includes('access-b-'))).toBe(false)
  })

  it.each(['direct-establishment', 'explicit-logout'] as const)(
    'rejects late A401 after B %s without B refresh/replay/storage effects', async mode => {
      const first = deferred(denied())
      route = async record => record.path === '/api/auth/refresh'
        ? refreshResponse('b', 1)
        : record.authorization === 'Bearer access-a-0' ? first.promise : successful(surface, record.authorization ?? '')
      const request = start(surface)
      assertARequest(requests[0], surface)
      const beforeB = replaceWithB(mode === 'explicit-logout')
      expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toBeNull()
      first.resolve(denied())
      assertNoBWork(await request, beforeB)
    },
  )

  it('rejects B replay when another real refresh awaiter establishes B before wrapper resumes', async () => {
    const first = deferred(denied())
    const refresh = deferred(refreshResponse('a', 1))
    route = async record => record.path === '/api/auth/refresh' ? refresh.promise
      : record.authorization === 'Bearer access-a-0' ? first.promise : successful(surface, record.authorization ?? '')
    const request = start(surface)
    const foreground = client.tryRefreshToken().then(saved => {
      expect(saved).toBe(true)
      expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toEqual(pair('a', 1))
      return replaceWithB()
    })
    first.resolve(denied())
    // Let the real custom wrapper join the existing refresh promise.
    await new Promise<void>(resolve => queueMicrotask(resolve))
    refresh.resolve(refreshResponse('a', 1))
    const beforeB = await foreground
    assertNoBWork(await request, beforeB)
  })

  it('control: replacement B during actual refresh JSON read is preserved and obsolete A rejects', async () => {
    const body = deferred({ access_token: 'access-a-1', refresh_token: 'refresh-a-1', user: { id: 'a' } })
    const refresh = refreshResponse('a', 1)
    // Delaying response parsing is the fake network boundary, not auth logic.
    let bodyReading = false
    refresh.json = () => { bodyReading = true; return body.promise }
    route = async record => record.path === '/api/auth/refresh' ? refresh : denied()
    const request = start(surface)
    await vi.waitFor(() => expect(bodyReading).toBe(true))
    expect(requests).toHaveLength(2)
    const beforeB = replaceWithB()
    body.resolve({ access_token: 'access-a-1', refresh_token: 'refresh-a-1', user: { id: 'a' } })
    assertNoBWork(await request, beforeB)
    expect(requests[1].refreshToken).toBe('refresh-a-0')
  })

  it('control: old A logout with no replacement rejects without refresh or delivery', async () => {
    const first = deferred(denied())
    route = async () => first.promise
    const request = start(surface)
    client.clearAuthStorage('controlled_actor_logout')
    first.resolve(denied())
    assertCanceled(await request)
    expect(requests).toHaveLength(1)
    expect(authState()).toEqual({ access: null, refresh: null, user: null, validated: null })
  })

  it.each(['access', 'refresh', 'both'] as const)(
    'control: missing entry %s credentials retains original 401 without refresh or clear', async missing => {
      if (missing !== 'refresh') localStorage.removeItem('access_token')
      if (missing !== 'access') localStorage.removeItem('refresh_token')
      const before = authState()
      route = async () => denied()
      assertCanceled(await start(surface))
      expect(requests).toHaveLength(1)
      expect(requests[0].authorization).toBe(missing === 'refresh' ? 'Bearer access-a-0' : null)
      expect(authState()).toEqual(before)
      expect(logoutReasons).toEqual([])
    },
  )

  it('control: replay 401 keeps existing decoder without another refresh or clear', async () => {
    route = async record => record.path === '/api/auth/refresh' ? refreshResponse('a', 1) : denied()
    assertCanceled(await start(surface))
    expect(requests).toHaveLength(3)
    expect(requests.at(-1)?.authorization).toBe('Bearer access-a-1')
    expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toEqual(pair('a', 1))
    expect(logoutReasons).toEqual([])
  })

  it.each(['503', 'network'] as const)('control: current %s refresh failure retains A and avoids replay', async failure => {
    const before = authState()
    route = async record => {
      if (record.path !== '/api/auth/refresh') return denied()
      if (failure === 'network') throw new TypeError('Offline fixture refresh transport failure')
      return json({ detail: 'Transient refresh failure' }, 503)
    }
    assertCanceled(await start(surface))
    expect(requests).toHaveLength(2)
    expect(authState()).toEqual(before)
    expect(logoutReasons).toEqual([])
  })

  it.each([401, 403])('control: current definitive refresh %s clears only A and avoids replay', async status => {
    route = async record => record.path === '/api/auth/refresh'
      ? json({ detail: 'Refresh definitively denied' }, status) : denied()
    assertCanceled(await start(surface))
    expect(requests).toHaveLength(2)
    expect(authState()).toEqual({ access: null, refresh: null, user: null, validated: null })
    expect(logoutReasons).toEqual(['refresh_failed'])
  })
})

describe('actual native-Storage C6 standard-client comparator', () => {
  it('control: standard request rejects late A401 after B without refresh/replay', async () => {
    const first = deferred(denied())
    route = async () => first.promise
    const request = observe(client.api.get('/api/v1/c6-session-control'))
    const beforeB = replaceWithB()
    first.resolve(denied())
    assertNoBWork(await request, beforeB)
    expect(requests).toHaveLength(1)
  })

  it('control: standard delayed A401 uses its healthy A2 descendant without another refresh', async () => {
    const first = deferred(denied())
    let generation = 0
    route = async record => record.path === '/api/auth/refresh'
      ? refreshResponse('a', ++generation)
      : record.authorization === 'Bearer access-a-0' ? first.promise : json({ actor: 'a' })
    const request = observe(client.api.get('/api/v1/c6-session-control'))
    expect(await client.tryRefreshToken()).toBe(true)
    expect(await client.tryRefreshToken()).toBe(true)
    first.resolve(denied())
    expect((await request).outcome).toBe('fulfilled')
    expect(requests).toHaveLength(4)
    expect(requests.at(-1)?.authorization).toBe('Bearer access-a-2')
    expect(client.resolveOwnedAuthSession('access-a-0', 'refresh-a-0')).toEqual(pair('a', 2))
  })
})
