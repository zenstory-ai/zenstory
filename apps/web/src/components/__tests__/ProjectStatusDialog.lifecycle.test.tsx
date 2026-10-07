import { useState } from 'react'
import { act, cleanup, configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'

// Offline translation startup runs during imports, before the per-case API fence.
// This file owns the boundary; the repository's shared setup remains unchanged.
vi.hoisted(() => {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path.startsWith('/locales/')) {
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    throw new Error(`Unexpected request before offline fixture: ${path}`);
  });
});
import { ProjectStatusDialog } from '../ProjectStatusDialog'
import { dispatchProjectStatusUpdated } from '../../lib/projectStatusEvents'
import { subscribeToast, type ToastEvent } from '../../lib/toast'
import { inspirationsConfig } from '../../config/inspirations'
import i18n from '../../lib/i18n'
import { logger } from '../../lib/logger'

// No dialog/Modal/router/events/client/context replicas or module mocks.
// All projectApi get/patch work passes through the real API/client to this fetch fence.
const locales = import.meta.glob<Record<string, unknown>>('../../../public/locales/en/*.json', { eager: true, import: 'default' })
const placeholders = {
  summary: 'Genre, world, protagonist and other core details',
  style: 'Voice, pacing, prose style, etc.',
  phase: 'Where you are now and what comes next',
  notes: 'Key points, things to watch, open questions, etc.',
}
type Request = { projectId: string; method: string; body: Record<string, unknown> | null; promise: Promise<Response>; resolve: (response: Response) => void }
let requests: Request[]
let unexpected: string[]
let toasts: ToastEvent[]
let unsubscribeToast: () => void
let bodyOverflow: string
let errorLogSpy: MockInstance<typeof logger.error>

function project(id: string, version = '0') {
  return { id, name: `Project ${id}`, project_type: 'novel', description: `description ${id}`, owner_id: 'offline-user',
    summary: `${id} summary ${version}`, writing_style: `${id} style ${version}`, current_phase: `${id} phase ${version}`,
    notes: `${id} notes ${version}`, created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:00Z' }
}
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}
function reads(id: string) { return requests.filter(request => request.method === 'GET' && request.projectId === id) }
function writes() { return requests.filter(request => request.method === 'PATCH') }
function fields() {
  return Object.fromEntries(Object.entries(placeholders).map(([key, placeholder]) => [key, (screen.getByPlaceholderText(placeholder) as HTMLTextAreaElement).value]))
}
function change(field: keyof typeof placeholders, value: string) {
  fireEvent.change(screen.getByPlaceholderText(placeholders[field]), { target: { value } })
}
async function finish(requestsToFinish: Request[], data: unknown, status = 200) {
  await act(async () => { requestsToFinish.forEach(request => request.resolve(json(data, status))) })
}
async function click(user: ReturnType<typeof userEvent.setup>, element: Element) {
  await act(async () => { await user.click(element) })
}
function event(id: string) {
  act(() => dispatchProjectStatusUpdated({ projectId: id, updatedFields: ['summary', 'notes'] }))
}

// Minimal caller plumbing matches ChatPanel's conditional mount at :1881:
// close actually unmounts the dialog; changing current project while open keeps
// the same unkeyed instance. onClose commits a real parent visibility update.
function Host() {
  const [selected, setSelected] = useState('A')
  const [open, setOpen] = useState(true)
  const [closeCount, setCloseCount] = useState(0)
  const location = useLocation()
  return <>
    <div data-testid="caller-controls">
      <button onClick={() => setSelected('B')}>Select B</button>
      <button onClick={() => { setSelected('A'); setOpen(true) }}>Open A</button>
      <button onClick={() => { setSelected('B'); setOpen(true) }}>Open B</button>
    </div>
    <output data-testid="caller-state">{JSON.stringify({ selected, open, closeCount, pathname: location.pathname })}</output>
    {open && selected && <ProjectStatusDialog isOpen={open} projectId={selected} onClose={() => { setCloseCount(count => count + 1); setOpen(false) }} />}
  </>
}
function state() {
  return JSON.parse(screen.getByTestId('caller-state').textContent!) as { selected: string; open: boolean; closeCount: number; pathname: string }
}
async function mount(strict: boolean) {
  configure({ reactStrictMode: strict }) // Actual RTL root StrictMode.
  render(<MemoryRouter initialEntries={['/project/A']}><Host /></MemoryRouter>)
  await waitFor(() => expect(reads('A')).toHaveLength(strict ? 2 : 1))
  return userEvent.setup()
}
async function initial(strict: boolean) {
  const user = await mount(strict)
  await finish(reads('A'), project('A'))
  await waitFor(() => expect(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(project('A').summary))
  return user
}
function observe(scenario: string, strict: boolean, extra: Record<string, unknown> = {}) {
  console.info('PROJECT_STATUS_OBSERVATION', JSON.stringify({ scenario, strict, state: state(), fields: screen.queryByPlaceholderText(placeholders.summary) ? fields() : null,
    requests: requests.map(({ projectId, method, body }) => ({ projectId, method, body })), errorLogCount: errorLogSpy.mock.calls.length, toasts, ...extra }))
}

beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage());
  vi.stubGlobal('sessionStorage', new window.Storage());
  vi.clearAllMocks()
  errorLogSpy = vi.spyOn(logger, 'error') // Call-through observation; logger behavior is unchanged.
  requests = []
  unexpected = []
  toasts = []
  unsubscribeToast = subscribeToast(value => { toasts.push(value) })
  bodyOverflow = document.body.style.overflow
  expect(localStorage).toBeInstanceOf(Storage)
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  localStorage.clear()
  sessionStorage.clear()
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), window.location.origin).pathname
    if (path.startsWith('/locales/')) return json(locales[`../../../public/locales/en/${path.split('/').at(-1)}`] ?? {})
    const match = path.match(/^\/api\/v1\/projects\/(A|B)$/)
    const method = options?.method ?? 'GET'
    if (!match || !['GET', 'PATCH'].includes(method)) {
      unexpected.push(`${method} ${path}`)
      throw new Error(`Unexpected offline project status request: ${method} ${path}`)
    }
    let resolve!: (value: Response) => void
    const promise = new Promise<Response>(accept => { resolve = accept })
    requests.push({ projectId: match[1], method, body: typeof options?.body === 'string' ? JSON.parse(options.body) : null, promise, resolve })
    return promise
  })
  for (const [path, data] of Object.entries(locales)) {
    i18n.addResourceBundle('en', path.split('/').at(-1)!.replace('.json', ''), data, true, true)
  }
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  cleanup()
  await finish(requests, { detail: 'Offline teardown' }, 503)
  unsubscribeToast()
  configure({ reactStrictMode: false })
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  expect(unexpected).toEqual([])
  expect(document.body.style.overflow).toBe(bodyOverflow)
})

for (const strict of [false, true]) {
  describe(strict ? 'actual project-status dialog root StrictMode' : 'actual project-status dialog default root', () => {
    it.each(['success', 'error'] as const)('latest event settles initial loader; old initial %s cannot overwrite or log', async outcome => {
      await mount(strict)
      const oldInitial = [...reads('A')]
      event('A')
      const latest = reads('A').at(-1)!
      await finish([latest], project('A', 'event-latest'))
      observe(`initial-event-latest-before-old-${outcome}`, strict)
      expect.soft(screen.queryAllByRole('textbox')).toHaveLength(4)
      const logs = errorLogSpy.mock.calls.length
      await finish(oldInitial, outcome === 'success' ? project('A', 'obsolete-initial') : { detail: 'Obsolete initial load rejected' }, outcome === 'success' ? 200 : 503)
      observe(`initial-event-latest-after-old-${outcome}`, strict)
      expect.soft(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(project('A', 'event-latest').summary)
      expect.soft(screen.getByPlaceholderText(placeholders.notes)).toHaveValue(project('A', 'event-latest').notes)
      expect.soft(errorLogSpy.mock.calls).toHaveLength(logs)
    })

    it.each(['success', 'error'] as const)('old initial %s cannot release loader while newer event is pending', async outcome => {
      await mount(strict)
      const oldInitial = [...reads('A')]
      event('A')
      const latest = reads('A').at(-1)!
      const logs = errorLogSpy.mock.calls.length
      await finish(oldInitial, outcome === 'success' ? project('A', 'obsolete-initial') : { detail: 'Obsolete initial load rejected' }, outcome === 'success' ? 200 : 503)
      observe(`old-initial-${outcome}-new-event-pending`, strict)
      expect.soft(screen.queryAllByRole('textbox')).toHaveLength(0)
      expect.soft(errorLogSpy.mock.calls).toHaveLength(logs)
      await finish([latest], project('A', 'event-latest'))
      expect(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(project('A', 'event-latest').summary)
    })

    it.each([
      ['success', 'success'], ['success', 'error'], ['error', 'success'], ['error', 'error'],
    ] as const)('new B save owns presentation against old A %s, current B %s', async (aOutcome, bOutcome) => {
      const user = await initial(strict)
      change('notes', 'A pending save')
      await click(user, screen.getByRole('button', { name: 'Save' }))
      const aWrite = writes()[0]
      expect(aWrite).toMatchObject({ projectId: 'A', body: { notes: 'A pending save' } })
      await click(user, screen.getByRole('button', { name: 'Select B' }))
      await waitFor(() => expect(reads('B')).toHaveLength(1))
      await finish(reads('B'), project('B'))
      change('notes', 'B pending save')
      const bSave = screen.getByRole('button', { name: /^(Save|Saving\.\.\.)$/ })
      // Untouched source blocks B with A's saving flag. Record that genuine
      // failing boundary without forcing a disabled click or inventing B work.
      expect.soft(bSave).toBeEnabled()
      if (bSave) await click(user, bSave)
      const bWrite = writes().find(request => request.projectId === 'B')
      expect.soft(bWrite).toBeDefined()
      if (!bWrite) {
        observe(`B-save-blocked-before-A-${aOutcome}`, strict, { overlapReached: false, bOutcome })
        await finish([aWrite], aOutcome === 'success' ? { ...project('A'), notes: 'A pending save' } : { detail: 'Old A save rejected' }, aOutcome === 'success' ? 200 : 503)
        return // The above soft assertions keep this case RED; no skip/PASS claim.
      }
      expect(bWrite.body).toEqual({ notes: 'B pending save' })
      expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
      const logs = errorLogSpy.mock.calls.length
      const toastCount = toasts.length
      await finish([aWrite], aOutcome === 'success' ? { ...project('A'), notes: 'A pending save' } : { detail: 'Old A save rejected' }, aOutcome === 'success' ? 200 : 503)
      observe(`A-${aOutcome}-while-B-save-active`, strict, { overlapReached: true, bOutcome })
      expect(state()).toMatchObject({ selected: 'B', open: true, closeCount: 0 })
      expect(screen.getByPlaceholderText(placeholders.notes)).toHaveValue('B pending save')
      expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
      expect(errorLogSpy.mock.calls).toHaveLength(logs)
      expect(toasts).toHaveLength(toastCount)
      await finish([bWrite], bOutcome === 'success' ? { ...project('B'), notes: 'B pending save' } : { detail: 'Current B save rejected' }, bOutcome === 'success' ? 200 : 503)
      if (bOutcome === 'success') {
        expect(state()).toMatchObject({ selected: 'B', open: false, closeCount: 1 })
        expect(toasts).toHaveLength(toastCount)
      } else {
        expect(state()).toMatchObject({ selected: 'B', open: true, closeCount: 0 })
        expect(screen.getByPlaceholderText(placeholders.notes)).toHaveValue('B pending save')
        expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
        expect(toasts.slice(toastCount)).toMatchObject([{ type: 'error', message: "Couldn't save. Please try again." }])
        expect(errorLogSpy.mock.calls).toHaveLength(logs + 1)
        await click(user, screen.getByRole('button', { name: 'Save' }))
        const retry = writes().at(-1)!
        expect(retry.projectId).toBe('B')
        await finish([retry], { ...project('B'), notes: 'B pending save' })
        expect(state()).toMatchObject({ selected: 'B', open: false, closeCount: 1 })
      }
    })

    it('failed B load exposes no actionable A form or save baseline after identity switch', async () => {
      const user = await initial(strict)
      change('notes', 'A unsaved notes')
      await click(user, screen.getByRole('button', { name: 'Select B' }))
      await waitFor(() => expect(reads('B')).toHaveLength(1))
      await finish(reads('B'), { detail: 'Current B load rejected' }, 503)
      observe('B-load-failed-after-dirty-A', strict)
      expect.soft(fields()).toEqual({ summary: '', style: '', phase: '', notes: '' })
      const save = screen.getByRole('button', { name: 'Save' })
      expect.soft(save).toBeDisabled()
      await click(user, save) // Ordinary click: stale A form is actionable on old source.
      observe('B-load-failed-save-attempt', strict)
      expect.soft(writes()).toHaveLength(0)
      expect(toasts).toHaveLength(0) // Current load failure still logs without new toast.
    })

    it('keeps B form and B save base after delayed A load completes', async () => {
      const user = await mount(strict)
      const oldA = [...reads('A')]
      await click(user, screen.getByRole('button', { name: 'Select B' }))
      await waitFor(() => expect(reads('B')).toHaveLength(1))
      await finish(reads('B'), project('B'))
      expect(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(project('B').summary)
      await finish(oldA, project('A'))
      observe('load-A-after-current-B', strict)
      expect.soft(fields()).toEqual({ summary: project('B').summary, style: project('B').writing_style, phase: project('B').current_phase, notes: project('B').notes })
      change('notes', 'B local notes')
      await click(user, screen.getByRole('button', { name: 'Save' }))
      await waitFor(() => expect(writes()).toHaveLength(1))
      expect(writes()[0].projectId).toBe('B') // Actual save target is B; old request itself is not forbidden.
      expect(writes()[0].body).toEqual({ notes: 'B local notes' })
    })

    it.each(['A', 'B'])('control: conditional unmount/reopen %s preserves new dirty input after old load', async next => {
      const user = await mount(strict)
      const oldA = [...reads('A')]
      await click(user, screen.getByRole('button', { name: 'Cancel' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      const oldCount = reads(next).length
      await click(user, screen.getByRole('button', { name: `Open ${next}` }))
      await waitFor(() => expect(reads(next)).toHaveLength(oldCount + (strict ? 2 : 1)))
      await finish(reads(next).slice(oldCount), project(next, 'reopened'))
      change('summary', `${next} reopened dirty`)
      await finish(oldA, project('A', 'obsolete'))
      observe(`unmount-load-reopen-${next}`, strict)
      expect(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(`${next} reopened dirty`)
      expect(screen.getByPlaceholderText(placeholders.notes)).toHaveValue(project(next, 'reopened').notes)
      expect(state()).toMatchObject({ selected: next, open: true, closeCount: 1 })
    })

    it('keeps latest requested status-event response when older clean refresh finishes last', async () => {
      await initial(strict)
      const initialCount = reads('A').length
      event('A')
      event('A')
      expect(reads('A')).toHaveLength(initialCount + 2)
      const [older, latest] = reads('A').slice(initialCount)
      await finish([latest], project('A', '2'))
      expect(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(project('A', '2').summary)
      await finish([older], project('A', '1'))
      observe('event-clean-out-of-order', strict)
      expect.soft(fields()).toEqual({ summary: project('A', '2').summary, style: project('A', '2').writing_style, phase: project('A', '2').current_phase, notes: project('A', '2').notes })
    })

    it('preserves dirty event fields and latest base when refreshes complete out of order', async () => {
      const user = await initial(strict)
      change('notes', 'local unsaved notes')
      const start = reads('A').length
      event('A')
      event('A')
      const [older, latest] = reads('A').slice(start)
      await finish([latest], project('A', '2'))
      expect(screen.getByPlaceholderText(placeholders.notes)).toHaveValue('local unsaved notes')
      await finish([older], project('A', '1'))
      observe('event-dirty-out-of-order', strict)
      expect.soft(screen.getByPlaceholderText(placeholders.notes)).toHaveValue('local unsaved notes')
      expect.soft(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(project('A', '2').summary)
      change('notes', project('A', '2').notes)
      const save = screen.getByRole('button', { name: 'Save' })
      expect.soft(save).toBeDisabled() // Returning dirty notes to latest server base should be no-op.
      if (!(save as HTMLButtonElement).disabled) await click(user, save)
      observe('event-old-base-noop-check', strict)
      expect.soft(writes()).toHaveLength(0)
    })

    it('does not close current B when allowed pending A save finishes', async () => {
      const user = await initial(strict)
      change('notes', 'A pending save')
      await click(user, screen.getByRole('button', { name: 'Save' }))
      expect(writes()).toHaveLength(1)
      expect(writes()[0]).toMatchObject({ projectId: 'A', body: { notes: 'A pending save' } })
      await click(user, screen.getByRole('button', { name: 'Select B' }))
      await waitFor(() => expect(reads('B')).toHaveLength(1))
      await finish(reads('B'), project('B'))
      change('notes', 'B current dirty')
      await finish([writes()[0]], { ...project('A'), notes: 'A pending save' })
      observe('save-A-after-select-B', strict)
      expect.soft(state()).toMatchObject({ selected: 'B', open: true, closeCount: 0 })
      expect.soft(screen.queryByPlaceholderText(placeholders.notes)).toHaveValue('B current dirty')
    })

    it('does not close reopened B from captured old unmounted A save callback', async () => {
      const user = await initial(strict)
      change('notes', 'A pending final save')
      await click(user, screen.getByRole('button', { name: 'Save' }))
      await click(user, screen.getByRole('button', { name: 'Cancel' }))
      await click(user, screen.getByRole('button', { name: 'Open B' }))
      await waitFor(() => expect(reads('B')).toHaveLength(strict ? 2 : 1))
      await finish(reads('B'), project('B'))
      change('summary', 'B reopened dirty')
      await finish([writes()[0]], { ...project('A'), notes: 'A pending final save' })
      observe('save-A-after-unmount-reopen-B', strict)
      expect.soft(state()).toMatchObject({ selected: 'B', open: true, closeCount: 1 })
      expect.soft(screen.queryByPlaceholderText(placeholders.summary)).toHaveValue('B reopened dirty')
    })

    it('control: current load/no-op/trim/dirty save targets only changed fields and closes once', async () => {
      const user = await initial(strict)
      expect(screen.getAllByRole('textbox')).toHaveLength(4)
      const save = screen.getByRole('button', { name: 'Save' })
      expect(save).toBeDisabled()
      await click(user, save)
      expect(writes()).toHaveLength(0)
      change('summary', `  ${project('A').summary}  `)
      expect(save).toBeDisabled()
      change('summary', '  current summary  ')
      change('style', ' current style ')
      change('phase', ' current phase ')
      change('notes', ' current notes ')
      await click(user, save)
      expect(writes()).toHaveLength(1)
      expect(writes()[0]).toMatchObject({ projectId: 'A', body: { summary: 'current summary', writing_style: 'current style', current_phase: 'current phase', notes: 'current notes' } })
      expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
      await finish(writes(), { ...project('A'), summary: 'current summary', writing_style: 'current style', current_phase: 'current phase', notes: 'current notes' })
      observe('current-save-control', strict)
      expect(state()).toMatchObject({ selected: 'A', open: false, closeCount: 1, pathname: '/project/A' })
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('control: load error exits loading; matching event can recover current data', async () => {
      await mount(strict)
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
      await finish(reads('A'), { detail: 'Offline load rejected' }, 503)
      await waitFor(() => expect(screen.getAllByRole('textbox')).toHaveLength(4))
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
      expect(toasts).toHaveLength(0) // Current load failures log without inventing a toast.
      const start = reads('A').length
      event('A')
      await finish(reads('A').slice(start), project('A'))
      expect(screen.getByPlaceholderText(placeholders.summary)).toHaveValue(project('A').summary)
      expect(state()).toMatchObject({ open: true, closeCount: 0 })
    })

    it('control: save error preserves dirty data, releases saving and permits ordinary retry', async () => {
      const user = await initial(strict)
      change('notes', 'retry notes')
      await click(user, screen.getByRole('button', { name: 'Save' }))
      await finish(writes(), { detail: 'Offline save rejected' }, 503)
      expect(toasts).toMatchObject([{ type: 'error', message: "Couldn't save. Please try again." }])
      expect(screen.getByPlaceholderText(placeholders.notes)).toHaveValue('retry notes')
      expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
      expect(state()).toMatchObject({ open: true, closeCount: 0 })
      await click(user, screen.getByRole('button', { name: 'Save' }))
      expect(writes()).toHaveLength(2)
      await finish([writes()[1]], { ...project('A'), notes: 'retry notes' })
      expect(state()).toMatchObject({ open: false, closeCount: 1 })
    })

    it('control: real event project isolation and listener cleanup across close/reopen/unmount', async () => {
      const user = await initial(strict)
      const start = requests.length
      event('B')
      expect(requests).toHaveLength(start)
      event('A')
      expect(requests).toHaveLength(start + 1) // StrictMode leaves one live matching listener.
      await finish([requests.at(-1)!], project('A', 'event'))
      await click(user, screen.getByRole('button', { name: 'Cancel' }))
      const closedCount = requests.length
      event('A')
      expect(requests).toHaveLength(closedCount)
      await click(user, screen.getByRole('button', { name: 'Open A' }))
      await waitFor(() => expect(requests).toHaveLength(closedCount + (strict ? 2 : 1)))
      await finish(requests.slice(closedCount), project('A', 'reopened'))
      const reopenedCount = requests.length
      event('A')
      expect(requests).toHaveLength(reopenedCount + 1)
      await finish([requests.at(-1)!], project('A', 'final-event'))
      cleanup()
      const finalCount = requests.length
      event('A')
      expect(requests).toHaveLength(finalCount)
    })

    it('control: disabled inspiration feature makes no inspiration action or navigation available', async () => {
      await initial(strict)
      expect(inspirationsConfig.enabled).toBe(false)
      const dialog = within(screen.getByRole('dialog'))
      expect(dialog.queryByRole('button', { name: 'Submit to Inspiration Library' })).not.toBeInTheDocument()
      expect(dialog.queryByRole('button', { name: 'View Inspiration Library' })).not.toBeInTheDocument()
      expect(dialog.getByRole('button', { name: 'Save' })).toBeInTheDocument()
      expect(state().pathname).toBe('/project/A')
      expect(requests.every(request => request.method === 'GET')).toBe(true)
    })
  })
}
