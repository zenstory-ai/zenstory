import { StrictMode, useEffect, useState } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider, useNavigate, useParams } from 'react-router-dom'
import { createInstance, type i18n } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setTimeout as realTimeout } from 'node:timers/promises'

const bootstrap = vi.hoisted(() => {
  const paths: string[] = []
  const fence: typeof fetch = async input => {
    const path = String(input)
    if (/\/locales\/(en|zh)\/[^/]+\.json/.test(path)) { paths.push(path); return new Response('{}', { headers: { 'Content-Type': 'application/json' } }) }
    throw new Error(`OFFLINE_BOOTSTRAP_DENIED ${path}`)
  }
  globalThis.fetch = fence
  return { paths, fence }
})
vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn(), identifyUser: vi.fn(), resetAnalytics: vi.fn() }))

import { ChatPanel } from '../ChatPanel'
import { AuthProvider } from '../../contexts/AuthContext'
import { ProjectProvider, useProject } from '../../contexts/ProjectContext'
import { MobileLayoutProvider } from '../../contexts/MobileLayoutContext'
import { MaterialAttachmentProvider } from '../../contexts/MaterialAttachmentContext'
import { TextQuoteProvider } from '../../contexts/TextQuoteContext'
import { SkillTriggerProvider } from '../../contexts/SkillTriggerContext'
import { clearAuthStorage, getApiBase } from '../../lib/apiClient'

type Gate = { promise: Promise<Response>; settle: (status?: number) => void; settled: () => boolean }
type Trace = { path: string; method: string; query: Record<string, string>; body: unknown; authorization: string | null; status?: number; aborted?: boolean }
let queryClient: QueryClient
let router: ReturnType<typeof createMemoryRouter>
let unsubscribe: () => void
let translations: i18n
let requests: Trace[]
let unexpected: string[]
let pending: Set<Promise<Response>>
let gates: Gate[]
let route: (path: string, body: unknown) => Promise<Response>
let routes: string[]
let checkpoints: { label: string; route: string; text: string; requests: number; streams: { aborted: boolean; closed: boolean }[] }[]
let streams: { controller: ReadableStreamDefaultController<Uint8Array>; closed: boolean; aborted: boolean; trace: Trace; dispose: () => void }[]
let originalFetch: typeof fetch
let originalStorage: Storage
let originalSessionStorage: Storage
let originalOverflow: string
const user = { id: 'fixture-user', username: 'local', email: 'fixture@example.invalid', email_verified: true, is_active: true, is_superuser: false,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z' }
const project = (id: string) => ({ id, name: `Project ${id}`, user_id: user.id, project_type: 'novel', status: 'draft', genre: 'fantasy', word_count: 0,
  created_at: '2026-10-01T00:00:00Z', updated_at: id === 'a' ? '2026-10-06T00:00:00Z' : '2026-10-05T00:00:00Z' })
const history = (id: string) => [{ id: `${id}-history`, session_id: `${id}-old-session`, role: 'user', content: `${id.toUpperCase()} HISTORY KEEP`, created_at: '2026-10-06T00:00:00Z' }]
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }) }
function gate(value: unknown): Gate {
  let done = false; let resolve!: (value: Response) => void
  const promise = new Promise<Response>(yes => { resolve = yes })
  const item = { promise, settle: (status = 200) => { if (!done) { done = true; resolve(json(status === 200 ? value : { detail: 'fixture new-session failure' }, status)) } }, settled: () => done }
  gates.push(item); return item
}
async function flush() {
  await act(async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); await vi.advanceTimersByTimeAsync(40); await realTimeout(5) })
}
async function until(predicate: () => boolean) { for (let i = 0; i < 30 && !predicate(); i++) await flush(); expect(predicate()).toBe(true) }
function snapshot(label: string) { checkpoints.push({ label, route: router.state.location.pathname, text: document.body.textContent ?? '', requests: requests.length,
  streams: streams.map(stream => ({ aborted: stream.aborted, closed: stream.closed })) }) }
function Workbench() {
  const { projectId } = useParams(); const navigate = useNavigate(); const { currentProjectId, setCurrentProjectId } = useProject(); const [visible, setVisible] = useState(true)
  useEffect(() => { setCurrentProjectId(projectId ?? null) }, [projectId, setCurrentProjectId])
  return <><p data-testid="actual-project">{currentProjectId}</p><button onClick={() => navigate('/project/b')}>Choose B</button>
    <button onClick={() => navigate('/project/a')}>Choose A</button><button onClick={() => setVisible(value => !value)}>Toggle chat</button>{visible && <ChatPanel />}</>
}
function mount(strict: boolean) {
  router = createMemoryRouter([{ path: '/project/:projectId', element: <Workbench /> }], { initialEntries: ['/project/a'] })
  routes = [router.state.location.pathname]; unsubscribe = router.subscribe(state => routes.push(state.location.pathname))
  const content = <I18nextProvider i18n={translations}><QueryClientProvider client={queryClient}><AuthProvider><ProjectProvider>
    <MobileLayoutProvider isMobile={false}><MaterialAttachmentProvider><TextQuoteProvider><SkillTriggerProvider>
      <RouterProvider router={router} />
    </SkillTriggerProvider></TextQuoteProvider></MaterialAttachmentProvider></MobileLayoutProvider>
  </ProjectProvider></AuthProvider></QueryClientProvider></I18nextProvider>
  return render(strict ? <StrictMode>{content}</StrictMode> : content)
}
const newButton = () => screen.getByTitle('New session')
const has = (text: string) => (document.body.textContent ?? '').includes(text)
const suggestionRequests = () => requests.filter(x => x.path === '/api/v1/agent/suggest')

beforeEach(async () => {
  requests = []; unexpected = []; pending = new Set(); gates = []; checkpoints = []; streams = []
  originalFetch = globalThis.fetch
  originalStorage = localStorage; originalSessionStorage = sessionStorage; originalOverflow = document.body.style.overflow
  vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false); expect(localStorage).toBeInstanceOf(window.Storage)
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date', 'requestAnimationFrame', 'cancelAnimationFrame'] })
  vi.setSystemTime(new Date('2026-10-06T12:00:00Z'))
  localStorage.clear(); sessionStorage.clear(); localStorage.setItem('access_token', 'fake-owned-access'); localStorage.setItem('refresh_token', 'fake-owned-refresh')
  localStorage.setItem('user', JSON.stringify(user)); localStorage.setItem('auth_validated_at', String(Date.now())); localStorage.setItem('zenstory-language', 'en')
  route = async (path, body) => {
    if (path === '/api/auth/me') return json(user)
    if (path === '/api/v1/projects') return json([project('a'), project('b')])
    if (/^\/api\/v1\/chat\/session\/[ab]\/recent$/.test(path)) return json(history(path.split('/')[5]))
    if (/^\/api\/v1\/chat\/session\/[ab]\/new$/.test(path)) return json({ id: 'new-session', project_id: path.split('/')[5] })
    if (path === '/api/v1/agent/suggest') return json({ suggestions: [`${(body as { project_id: string }).project_id.toUpperCase()} OWN SUGGESTION`] })
    if (path === '/api/v1/subscription/quota') return json({ ai_conversations: { used: 0, limit: 100 }, plan: 'free' })
    if (/\/locales\/(en|zh)\/[^/]+\.json/.test(path)) return json({})
    unexpected.push(path); throw new Error(`OFFLINE_API_DENIED ${path}`)
  }
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://localhost')
    if (url.origin !== new URL(getApiBase()).origin && !url.pathname.startsWith('/locales/')) { unexpected.push(url.href); return Promise.reject(new Error('Unexpected origin')) }
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    const record: Trace = { path: url.pathname, method: init?.method ?? 'GET', query: Object.fromEntries(url.searchParams), body,
      authorization: new Headers(init?.headers).get('Authorization') }; requests.push(record)
    if (url.pathname === '/api/v1/agent/stream') {
      record.status = 200
      return Promise.resolve(new Response(new ReadableStream<Uint8Array>({ start(controller) {
        const signal = init?.signal
        const stream = { controller, closed: false, aborted: false, trace: record, dispose: () => signal?.removeEventListener('abort', onAbort) }
        const onAbort = () => { stream.aborted = true; record.aborted = true; if (!stream.closed) { stream.closed = true; controller.close() } }
        streams.push(stream); signal?.addEventListener('abort', onAbort, { once: true })
        controller.enqueue(new TextEncoder().encode('event: content_start\ndata: {}\n\nevent: content\ndata: {"text":"B LIVE STREAM KEEP"}\n\n'))
      } }), { headers: { 'Content-Type': 'text/event-stream' } }))
    }
    const response = route(url.pathname, body).then(value => { record.status = value.status; return value })
    pending.add(response); void response.then(() => pending.delete(response), () => pending.delete(response)); return response
  })
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } })
  translations = createInstance()
  const resources = { chat: { panel: { newSession: 'New session', aiMemory: 'AI memory' }, input: { staticSuggestions: ['LOCAL FALLBACK', 'LOCAL FALLBACK TWO'] } }, common: { send: 'Send', cancel: 'Cancel', loading: 'Loading' }, settings: {}, dashboard: {}, home: {}, editor: {}, versions: {} }
  await translations.init({ lng: 'en', fallbackLng: 'en', defaultNS: 'chat', resources: { en: resources } })
})

afterEach(async () => {
  const unsettledAtAssertion = gates.filter(x => !x.settled()).length
  gates.forEach(x => x.settle()); await flush(); snapshot('after assertion')
  cleanup(); await queryClient.cancelQueries(); queryClient.clear()
  for (const stream of streams) { if (!stream.closed) { stream.closed = true; stream.controller.close() }; stream.dispose() }
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) }); await Promise.allSettled([...pending])
  const remainingTimers = vi.getTimerCount(); vi.clearAllTimers()
  console.info('M07_NEW_SESSION_OBSERVATION', JSON.stringify({ case: expect.getState().currentTestName, requests, routes, checkpoints, bootstrapLocaleRequestCount: bootstrap.paths.length,
    streams: streams.map(x => ({ closed: x.closed, aborted: x.aborted })), cleanup: { unsettledAtAssertion, pending: pending.size, unexpected, remainingTimers, timersAfterClear: vi.getTimerCount(), dialogs: screen.queryAllByRole('dialog').length,
      bodyOverflowRestored: document.body.style.overflow === originalOverflow } }))
  unsubscribe?.(); router?.dispose(); translations.off('languageChanged'); clearAuthStorage('owned_chat_proof_cleanup'); localStorage.clear(); sessionStorage.clear()
  vi.unstubAllGlobals(); globalThis.fetch = originalFetch; vi.useRealTimers()
  expect(globalThis.fetch).toBe(originalFetch); expect(localStorage).toBe(originalStorage); expect(sessionStorage).toBe(originalSessionStorage)
  expect(document.body.style.overflow).toBe(originalOverflow); expect(remainingTimers).toBe(0)
  expect(unexpected).toEqual([]); expect(unsettledAtAssertion).toBe(0); expect(pending.size).toBe(0); expect(screen.queryAllByRole('dialog')).toHaveLength(0)
})

describe.each([false, true])('actual mounted ChatPanel new session rootStrict=%s', strict => {
  it('control: ordinary current A new session clears A history using enabled toolbar and actual API', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({ id: 'a-new-session', project_id: 'a' }); const normal = route; route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    expect((newButton() as HTMLButtonElement).disabled).toBe(false); fireEvent.click(newButton()); await flush(); expect(has('A HISTORY KEEP')).toBe(true)
    session.settle(); await flush(); snapshot('healthy A completion')
    expect(has('A HISTORY KEEP')).toBe(false); expect(screen.getByTestId('actual-project').textContent).toBe('a')
    expect(requests.filter(x => x.path === '/api/v1/chat/session/a/new')).toHaveLength(1)
    expect(requests.find(x => x.path.endsWith('/new'))?.query.title).toBe('New Chat'); expect(has('A OWN SUGGESTION')).toBe(true)
  })

  it('retains real B history and B suggestions after pending A new session succeeds', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({ id: 'a-new-session', project_id: 'a' }); const normal = route; route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); fireEvent.click(screen.getByText('Choose B')); await until(() => has('B HISTORY KEEP') && has('B OWN SUGGESTION'))
    const before = suggestionRequests().length; snapshot('B real history before A completion'); session.settle(); await flush(); snapshot('A success after committed B')
    expect(router.state.location.pathname).toBe('/project/b'); expect(has('B HISTORY KEEP')).toBe(true); expect(has('B OWN SUGGESTION')).toBe(true)
    expect(has('A OWN SUGGESTION')).toBe(false); expect(suggestionRequests()).toHaveLength(before)
  })

  it('retains current B suggestion presentation after pending A new session fails', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({}); const normal = route; route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); fireEvent.click(screen.getByText('Choose B')); await until(() => has('B HISTORY KEEP') && has('B OWN SUGGESTION'))
    session.settle(500); await flush(); snapshot('A failure after committed B')
    expect(has('B HISTORY KEEP')).toBe(true); expect(has('B OWN SUGGESTION')).toBe(true); expect(has('LOCAL FALLBACK')).toBe(false)
  })

  it('does not repopulate old same-project history after new session succeeds during initial history load', async () => {
    const oldHistory = gate(history('a')); const normal = route; route = (path, body) => path === '/api/v1/chat/session/a/recent' ? oldHistory.promise : normal(path, body)
    mount(strict); await until(() => requests.some(x => x.path.endsWith('/a/recent')) && Boolean(screen.queryByTitle('New session')))
    expect((newButton() as HTMLButtonElement).disabled).toBe(false); fireEvent.click(newButton()); await until(() => requests.some(x => x.path.endsWith('/a/new') && x.status === 200))
    await flush(); const inputEnabledBeforeOldHistory = !(screen.getByTestId('chat-input') as HTMLTextAreaElement).disabled
    expect(has('A HISTORY KEEP')).toBe(false); snapshot('new session while old history pending'); oldHistory.settle(); await flush(); snapshot('old A history after new session success')
    expect(inputEnabledBeforeOldHistory).toBe(true); expect(has('A HISTORY KEEP')).toBe(false)
  })

  it('control: actual old panel unmount and B replacement retain B after A completion', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({ id: 'a-new-session', project_id: 'a' }); const normal = route; route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); fireEvent.click(screen.getByText('Toggle chat')); fireEvent.click(screen.getByText('Choose B')); await flush()
    fireEvent.click(screen.getByText('Toggle chat')); await until(() => has('B HISTORY KEEP') && has('B OWN SUGGESTION'))
    const before = requests.length; session.settle(); await flush(); snapshot('old unmounted A completion with new B panel')
    expect(has('B HISTORY KEEP')).toBe(true); expect(has('B OWN SUGGESTION')).toBe(true); expect(requests).toHaveLength(before)
  })

  it('does not abort or erase the actual B stream when pending A new session succeeds', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({ id: 'a-new-session', project_id: 'a' }); const normal = route; route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); fireEvent.click(screen.getByText('Choose B')); await until(() => has('B HISTORY KEEP') && has('B OWN SUGGESTION'))
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'B owner message' } }); fireEvent.click(screen.getByTestId('send-button'))
    await until(() => has('B LIVE STREAM KEEP')); expect(streams).toHaveLength(1); expect(streams[0].aborted).toBe(false)
    snapshot('actual B stream before old A success'); session.settle(); await flush(); snapshot('actual B stream after old A success')
    expect(streams[0].aborted).toBe(false); expect(has('B LIVE STREAM KEEP')).toBe(true)
    expect(requests.filter(x => x.path === '/api/v1/agent/stream')).toHaveLength(1)
  })


  it('retains returned A history after A to B to A supersedes pending new session', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({ id: 'a-new-session', project_id: 'a' }); const normal = route
    route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); fireEvent.click(screen.getByText('Choose B')); await until(() => has('B HISTORY KEEP'))
    route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : path === '/api/v1/chat/session/a/recent'
      ? Promise.resolve(json([{ ...history('a')[0], id: 'a-returned', content: 'A RETURNED HISTORY KEEP' }])) : normal(path, body)
    fireEvent.click(screen.getByText('Choose A')); await until(() => has('A RETURNED HISTORY KEEP'))
    snapshot('returned A before old A completion'); session.settle(); await flush(); snapshot('returned A after old A completion')
    expect(router.state.location.pathname).toBe('/project/a'); expect(has('A RETURNED HISTORY KEEP')).toBe(true)
    expect(has('A OWN SUGGESTION')).toBe(true); expect(requests.filter(x => x.path.endsWith('/a/new'))).toHaveLength(1)
  })

  it('does not request uncached A suggestions after actual old panel unmount', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    localStorage.removeItem('zenstory_suggestions_cache_a')
    const session = gate({ id: 'a-new-session', project_id: 'a' }); const normal = route
    route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); fireEvent.click(screen.getByText('Toggle chat')); fireEvent.click(screen.getByText('Choose B')); await flush()
    fireEvent.click(screen.getByText('Toggle chat')); await until(() => has('B HISTORY KEEP') && has('B OWN SUGGESTION'))
    const before = requests.length; snapshot('uncached actual unmount before A completion'); session.settle(); await flush(); snapshot('uncached actual unmount after A completion')
    expect(has('B HISTORY KEEP')).toBe(true); expect(has('B OWN SUGGESTION')).toBe(true); expect(requests).toHaveLength(before)
    expect(suggestionRequests().filter(x => (x.body as { project_id: string }).project_id === 'a')).toHaveLength(1)
  })

  it('control: obsolete A completion cannot settle a newer B history loader', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({ id: 'a-new-session', project_id: 'a' }); const bHistory = gate(history('b')); const normal = route
    route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : path === '/api/v1/chat/session/b/recent' ? bHistory.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); fireEvent.click(screen.getByText('Choose B')); await until(() => requests.some(x => x.path.endsWith('/b/recent')))
    expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).disabled).toBe(true)
    session.settle(); await flush(); snapshot('obsolete A after B loader started')
    expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).disabled).toBe(true)
    bHistory.settle(); await until(() => has('B HISTORY KEEP') && has('B OWN SUGGESTION')); snapshot('current B loader settled')
    expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).disabled).toBe(false)
  })

  it('control: failed current creation retains pending own history recovery', async () => {
    const oldHistory = gate(history('a')); const session = gate({}); const normal = route
    route = (path, body) => path === '/api/v1/chat/session/a/recent' ? oldHistory.promise : path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    mount(strict); await until(() => requests.some(x => x.path.endsWith('/a/recent')) && Boolean(screen.queryByTitle('New session')))
    fireEvent.click(newButton()); await flush(); session.settle(500); await flush(); snapshot('failed creation with own history pending')
    expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).disabled).toBe(true)
    oldHistory.settle(); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION')); snapshot('own history recovers after failed creation')
    expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).disabled).toBe(false)
    expect(requests.filter(x => x.path.endsWith('/a/new'))).toHaveLength(1)
  })

  it('control: current A new-session failure preserves history and ordinary retry succeeds', async () => {
    mount(strict); await until(() => has('A HISTORY KEEP') && has('A OWN SUGGESTION'))
    const session = gate({}); const normal = route; route = (path, body) => path === '/api/v1/chat/session/a/new' ? session.promise : normal(path, body)
    fireEvent.click(newButton()); await flush(); session.settle(500); await flush(); snapshot('current A failure')
    expect(has('A HISTORY KEEP')).toBe(true); expect(has('LOCAL FALLBACK')).toBe(true)
    route = normal; fireEvent.click(newButton()); await flush(); snapshot('ordinary current A retry')
    expect(has('A HISTORY KEEP')).toBe(false); expect(has('A OWN SUGGESTION')).toBe(true)
    expect(requests.filter(x => x.path === '/api/v1/chat/session/a/new')).toHaveLength(2)
  })
})
