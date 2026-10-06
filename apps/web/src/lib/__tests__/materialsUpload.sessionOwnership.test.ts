import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// Actual materials upload + actual client refresh/lineage; actor switches here
// are controlled native Storage commands, not AuthProvider identity proofs.
type Client = typeof import('../apiClient')
let client: Client
let materials: typeof import('../materialsApi')
type Call = { url: string; path: string; auth: string | null; method: string; refresh?: string; form?: FormData }
let calls: Call[]
let route: (call: Call) => Promise<Response>
let file: File
let pending: Promise<unknown>[]
let release: (() => void)[]
let unexpected: string[]
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const denied = () => json({ detail: 'Original upload denied' }, 401)
const pair = (actor: string, version = 0) => ({ access_token: `upload-${actor}-access-${version}`, refresh_token: `upload-${actor}-refresh-${version}`, user: { id: actor } })
const establish = (actor: string, version = 0) => {
  const p = pair(actor, version)
  localStorage.setItem('access_token', p.access_token); localStorage.setItem('refresh_token', p.refresh_token)
  localStorage.setItem('user', JSON.stringify(p.user)); localStorage.setItem('auth_validated_at', `seed-${actor}-${version}`)
}
const storage = () => ({ access: localStorage.getItem('access_token'), refresh: localStorage.getItem('refresh_token'), user: localStorage.getItem('user'), validated: localStorage.getItem('auth_validated_at') })
function gate<T>(fallback: T) {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes }); release.push(() => resolve(fallback))
  return { promise, resolve }
}
const success = () => json({ novel_id: 'novel-a', message: 'Uploaded A', status: 'pending' })
function start(title?: string) {
  const observed = materials.materialsApi.upload(file, title).then(value => ({ ok: true, value }), (error: unknown) => ({
    ok: false, typed: error instanceof client.ApiError,
    status: error instanceof client.ApiError ? error.status : undefined,
    rawMessage: error instanceof client.ApiError ? error.rawMessage : String(error),
  }))
  pending.push(observed); return observed
}
const assertDenied = (result: unknown) => expect(result).toEqual({ ok: false, typed: true, status: 401, rawMessage: 'Original upload denied' })
beforeEach(async () => {
  vi.resetModules(); vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  calls = []; pending = []; release = []; unexpected = []; file = new File(['本地 fake bytes'], 'fake-a.txt', { type: 'text/plain' })
  route = async () => { throw new Error('OFFLINE unspecified route') }
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.href)
    if (!client || url.origin !== new URL(client.getApiBase()).origin || !['/api/v1/materials/upload', '/api/auth/refresh'].includes(url.pathname)) {
      unexpected.push(String(input)); throw new Error('OFFLINE unexpected endpoint')
    }
    const call: Call = { url: String(input), path: url.pathname, auth: new Headers(init?.headers).get('Authorization'), method: init?.method ?? 'GET' }
    if (init?.body instanceof FormData) call.form = init.body
    if (typeof init?.body === 'string') call.refresh = JSON.parse(init.body).refresh_token
    calls.push(call); return route(call)
  })
  client = await import('../apiClient'); materials = await import('../materialsApi'); establish('a')
})
afterEach(async () => {
  release.forEach(resolve => resolve()); await Promise.all(pending)
  for (const call of calls.filter(call => call.form)) {
    expect(Array.from(call.form!.keys())).toEqual(['file']); expect(call.form!.get('file')).toBe(file)
    expect(await file.text()).toBe('本地 fake bytes'); expect(call.method).toBe('POST')
  }
  expect(unexpected).toEqual([])
  client.clearAuthStorage('test_cleanup'); localStorage.clear(); sessionStorage.clear()
  vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.resetModules()
})
it('does not refresh/replay old A upload after unrelated B replacement', async () => {
  const first = gate(denied()); route = async call => call.path === '/api/auth/refresh' ? json(pair('b', 1)) : calls.filter(c => c.form).length === 1 ? first.promise : success()
  const work = start('Actor A & 标题'); establish('b'); const before = storage(); first.resolve(denied())
  const result = await work
  expect({ result, requests: calls.map(c => ({ auth: c.auth, refresh: c.refresh })), storage: storage() }).toEqual({
    result: { ok: false, typed: true, status: 401, rawMessage: 'Original upload denied' },
    requests: [{ auth: 'Bearer upload-a-access-0', refresh: undefined }], storage: before,
  })
})
it('retains same-session refresh and exact title/body/base with one retry', async () => {
  route = async call => call.path === '/api/auth/refresh' ? json(pair('a', 1)) : call.auth === 'Bearer upload-a-access-0' ? denied() : success()
  expect(await start('Actor A & 标题')).toEqual({ ok: true, value: { novel_id: 'novel-a', message: 'Uploaded A', status: 'pending' } })
  expect(calls.map(c => c.auth)).toEqual(['Bearer upload-a-access-0', null, 'Bearer upload-a-access-1'])
  expect(calls[1].refresh).toBe('upload-a-refresh-0'); expect(calls[0].form).toBe(calls[2].form)
  expect(calls[0].url).toBe(`${client.getApiBase()}/api/v1/materials/upload?title=Actor+A+%26+%E6%A0%87%E9%A2%98`)
})
it('reuses confirmed multigeneration descendant without a third refresh', async () => {
  const first = gate(denied()); let rotates = 0
  route = async call => call.path === '/api/auth/refresh' ? json(pair('a', ++rotates)) : calls.filter(c => c.form).length === 1 ? first.promise : success()
  const work = start(); expect(await client.tryRefreshToken()).toBe(true); expect(await client.tryRefreshToken()).toBe(true)
  first.resolve(denied()); expect(await work).toMatchObject({ ok: true })
  expect(calls).toHaveLength(4); expect(rotates).toBe(2); expect(calls.at(-1)?.auth).toBe('Bearer upload-a-access-2')
})
it('does not replay replacement B while actual A refresh is pending', async () => {
  const refresh = gate(json(pair('a', 1))); route = async call => call.path === '/api/auth/refresh' ? refresh.promise : denied()
  const work = start(); while (calls.length < 2) await Promise.resolve()
  establish('b'); const before = storage(); refresh.resolve(json(pair('a', 1))); assertDenied(await work)
  expect(calls).toHaveLength(2); expect(storage()).toEqual(before)
})
it.each(['logout', 'no-refresh', 'no-access'])('retains original401 after %s without retry', async command => {
  if (command === 'no-refresh') localStorage.removeItem('refresh_token')
  if (command === 'no-access') localStorage.removeItem('access_token')
  const first = gate(denied()); route = async call => call.path === '/api/auth/refresh' ? json(pair('a', 1)) : calls.filter(c => c.form).length === 1 ? first.promise : success()
  const work = start(); if (command === 'logout') client.clearAuthStorage('test_logout')
  first.resolve(denied()); assertDenied(await work); expect(calls).toHaveLength(1)
})
it('returns ordinary200 without introducing a success-session cancellation policy', async () => {
  route = async () => success(); expect(await start()).toMatchObject({ ok: true }); expect(calls).toHaveLength(1)
})
it('preserves400 decoder and malformed error fallback', async () => {
  route = async () => json({ detail: 'Invalid text file' }, 400)
  expect(await start()).toEqual({ ok: false, typed: true, status: 400, rawMessage: 'Invalid text file' })
  route = async () => new Response('not JSON', { status: 400 })
  expect(await start()).toEqual({ ok: false, typed: true, status: 400, rawMessage: 'ERR_MATERIAL_UPLOAD_FAILED' })
})
it.each([503, 401])('preserves own refresh failure %s semantics and original upload401', async status => {
  route = async call => call.path === '/api/auth/refresh' ? json({ detail: 'refresh failure' }, status) : denied()
  const before = storage(); assertDenied(await start()); expect(calls).toHaveLength(2)
  if (status === 503) expect(storage()).toEqual(before)
  else expect(localStorage.getItem('access_token')).toBeNull()
})
