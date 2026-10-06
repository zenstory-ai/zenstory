import '@testing-library/jest-dom/vitest'
import { StrictMode } from 'react'
import type { ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import skillsEnglish from '../../../public/locales/en/skills.json'
import commonEnglish from '../../../public/locales/en/common.json'
import SkillsPage from '../../pages/SkillsPage'
import { SkillResourcesSection } from '../skills/SkillResourcesSection'
import { ShareSkillModal } from '../skills/ShareSkillModal'
import { SkillStatsDialog } from '../SkillStatsDialog'
import { skillsApi } from '../../lib/api'
import { toast } from '../../lib/toast'
import type { MySkillsResponse, Skill, SkillUsageStats } from '../../types'

vi.mock('../../lib/api', () => ({
  skillsApi: {
    mySkills: vi.fn(), listResources: vi.fn(), getResourceContent: vi.fn(),
    upsertResource: vi.fn(), deleteResource: vi.fn(), create: vi.fn(), update: vi.fn(),
    delete: vi.fn(), batchUpdate: vi.fn(), importSkill: vi.fn(), exportSkill: vi.fn(),
    share: vi.fn(), getStats: vi.fn(),
  },
  publicSkillsApi: {
    list: vi.fn(async () => ({ skills: [], total: 0, page: 1, page_size: 20 })),
    getCategories: vi.fn(async () => ({ categories: [] })),
  },
}))
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: null }) }))
vi.mock('../../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const i18n = createInstance()
const skill = (id: string): Skill => ({ id, name: `Skill ${id}`, description: null,
  instructions: `Method ${id}`, triggers: [], source: 'user', is_active: true })
const collection = (id = 'initial'): MySkillsResponse => ({ user_skills: [skill(id)], added_skills: [], total: 1 })
const resource = (content = 'owned content') => ({ path: 'references/a.md', content })
const stats = (id: string): SkillUsageStats => ({ total_triggers: 7, builtin_count: 0, user_count: 7,
  top_skills: [{ skill_id: id, skill_name: `Stats ${id}`, skill_source: 'user', count: 7 }], daily_usage: [] })
let gates: { done: () => boolean; settle: () => void }[]
let unexpected: string[]
let oldOverflow: string
function deferred<T>(fallback: T) {
  let done = false
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = value => { if (!done) { done = true; yes(value) } }
    reject = error => { if (!done) { done = true; no(error) } }
  })
  gates.push({ done: () => done, settle: () => resolve(fallback) })
  return { promise, resolve, reject }
}
const flush = async () => { await act(async () => {}) }
const advance = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(301) }) }
function mount(node: ReactNode, strict = false) {
  const wrap = (children: ReactNode) => <I18nextProvider i18n={i18n}>{strict ? <StrictMode>{children}</StrictMode> : children}</I18nextProvider>
  const result = render(wrap(node))
  return { ...result, rerender: (next: ReactNode) => result.rerender(wrap(next)) }
}
async function myPage(strict = false) {
  const result = mount(<SkillsPage />, strict)
  await flush()
  fireEvent.click(screen.getByRole('button', { name: 'My Skills' }))
  await flush()
  return result
}
async function newForm(name: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Create Skill' }))
  await flush()
  const dialog = screen.getByRole('dialog', { name: 'Create Skill' })
  fireEvent.change(within(dialog).getByPlaceholderText(skillsEnglish.form.namePlaceholder), { target: { value: name } })
  fireEvent.change(within(dialog).getByPlaceholderText(skillsEnglish.form.instructionsPlaceholder), { target: { value: `Draft ${name}` } })
  return dialog
}

beforeEach(async () => {
  vi.clearAllMocks()
  vi.stubGlobal('localStorage', new window.Storage())
  vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  gates = []; unexpected = []; oldOverflow = document.body.style.overflow
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    unexpected.push(String(input)); throw new Error('OFFLINE unexpected fetch')
  })
  await i18n.init({ lng: 'en', fallbackLng: 'en', ns: ['skills', 'common'], defaultNS: 'common',
    resources: { en: { skills: skillsEnglish, common: commonEnglish } }, interpolation: { escapeValue: false } })
  vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
    matches: query === '(min-width: 768px)', media: query, onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(),
    removeListener: vi.fn(), dispatchEvent: () => true,
  }))
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  vi.mocked(skillsApi.mySkills).mockResolvedValue(collection())
  vi.mocked(skillsApi.listResources).mockResolvedValue({ resources: [{ path: 'references/a.md', size: 3, updated_at: 'now' }] })
  vi.mocked(skillsApi.getResourceContent).mockResolvedValue(resource())
  vi.mocked(skillsApi.upsertResource).mockResolvedValue({ path: 'references/a.md', size: 3, updated_at: 'now' })
  vi.mocked(skillsApi.deleteResource).mockResolvedValue({ message: 'deleted' })
  vi.mocked(skillsApi.create).mockResolvedValue(skill('created'))
  vi.mocked(skillsApi.update).mockResolvedValue(skill('updated'))
  vi.mocked(skillsApi.share).mockResolvedValue({ success: true, message: 'shared' })
  vi.mocked(skillsApi.importSkill).mockResolvedValue({ skill: skill('imported'), warnings: ['scripts dropped'] })
  vi.mocked(skillsApi.getStats).mockResolvedValue(stats('current'))
})
afterEach(async () => {
  const remaining = gates.filter(g => !g.done()).length
  await act(async () => { for (const gate of gates) gate.settle() })
  cleanup()
  await act(async () => { await vi.runOnlyPendingTimersAsync() })
  console.info('M08_UI_OBSERVATION', JSON.stringify({ case: expect.getState().currentTestName,
    myQueries: vi.mocked(skillsApi.mySkills).mock.calls,
    resourceReads: vi.mocked(skillsApi.getResourceContent).mock.calls,
    writes: vi.mocked(skillsApi.upsertResource).mock.calls,
    shareCalls: vi.mocked(skillsApi.share).mock.calls, statsCalls: vi.mocked(skillsApi.getStats).mock.calls,
    creates: vi.mocked(skillsApi.create).mock.calls, imports: vi.mocked(skillsApi.importSkill).mock.calls.length,
    toasts: vi.mocked(toast.error).mock.calls, cleanup: { remaining, timers: vi.getTimerCount(), portals: document.querySelectorAll('[role="dialog"]').length }, unexpected }))
  expect(remaining).toBe(0)
  expect(vi.getTimerCount()).toBe(0)
  expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(0)
  expect(document.body.style.overflow).toBe(oldOverflow)
  expect(unexpected).toEqual([])
  localStorage.clear(); sessionStorage.clear(); i18n.off('languageChanged'); i18n.off('loaded')
  vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

describe.each([false, true])('resource lifetimes rootStrict=%s', strict => {
  it('closing pending content keeps the editor closed after success', async () => {
    const gate = deferred(resource('obsolete'))
    vi.mocked(skillsApi.getResourceContent).mockReturnValue(gate.promise)
    mount(<SkillResourcesSection skillId="a" />, strict); await flush()
    fireEvent.click(screen.getByText('references/a.md'))
    fireEvent.click(screen.getByRole('button', { name: 'Close editor' }))
    await act(async () => { gate.resolve(resource('obsolete')) })
    expect(screen.queryByRole('textbox', { name: 'File content' })).not.toBeInTheDocument()
  })
  it('a pending save cannot close a newly opened resource editor', async () => {
    const gate = deferred({ path: 'references/a.md', size: 3, updated_at: 'later' })
    vi.mocked(skillsApi.upsertResource).mockReturnValue(gate.promise)
    const changed = vi.fn()
    mount(<SkillResourcesSection skillId="a" onChange={changed} />, strict); await flush()
    fireEvent.click(screen.getByText('references/a.md')); await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add file' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'File content' }), { target: { value: 'New B draft' } })
    await act(async () => { gate.resolve({ path: 'references/a.md', size: 3, updated_at: 'later' }) })
    expect(screen.getByRole('textbox', { name: 'File content' })).toHaveValue('New B draft')
    expect(changed).toHaveBeenCalledTimes(1)
    expect(skillsApi.upsertResource).toHaveBeenCalledTimes(1)
  })
})

it('old resource save after actual unmount makes no parent refresh callback or new GET', async () => {
  const gate = deferred({ path: 'references/a.md', size: 3, updated_at: 'later' })
  vi.mocked(skillsApi.upsertResource).mockReturnValue(gate.promise)
  const changed = vi.fn(); const view = mount(<SkillResourcesSection skillId="a" onChange={changed} />)
  await flush(); fireEvent.click(screen.getByText('references/a.md')); await flush()
  fireEvent.click(screen.getByRole('button', { name: 'Save file' })); view.unmount()
  const reads = vi.mocked(skillsApi.listResources).mock.calls.length
  await act(async () => { gate.resolve({ path: 'references/a.md', size: 3, updated_at: 'later' }) })
  expect(changed).not.toHaveBeenCalled(); expect(skillsApi.listResources).toHaveBeenCalledTimes(reads)
})
it('skill replacement rejects old resource failure and retains new dirty content', async () => {
  const gate = deferred({ path: 'references/a.md', size: 3, updated_at: 'later' })
  vi.mocked(skillsApi.upsertResource).mockReturnValue(gate.promise)
  const view = mount(<SkillResourcesSection skillId="a" />); await flush()
  fireEvent.click(screen.getByText('references/a.md')); await flush()
  fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
  view.rerender(<SkillResourcesSection skillId="b" />); await flush()
  fireEvent.click(screen.getByRole('button', { name: 'Add file' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'File content' }), { target: { value: 'B draft' } })
  await act(async () => { gate.reject(new Error('obsolete A save')) })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'File content' })).toHaveValue('B draft')
  expect(screen.getByRole('button', { name: 'Save file' })).toBeEnabled()
})
it('new editor supersedes pending content; owned save closes and refreshes once', async () => {
  const gate = deferred(resource('obsolete')); const changed = vi.fn()
  vi.mocked(skillsApi.getResourceContent).mockReturnValue(gate.promise)
  mount(<SkillResourcesSection skillId="a" onChange={changed} />); await flush()
  fireEvent.click(screen.getByText('references/a.md'))
  fireEvent.click(screen.getByRole('button', { name: 'Add file' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'File path' }), { target: { value: 'references/b.md' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'File content' }), { target: { value: 'B' } })
  await act(async () => { gate.resolve(resource('obsolete')) })
  expect(screen.getByRole('textbox', { name: 'File content' })).toHaveValue('B')
  const reads = vi.mocked(skillsApi.listResources).mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: 'Save file' })); await flush()
  expect(screen.queryByRole('textbox', { name: 'File content' })).not.toBeInTheDocument()
  expect(changed).toHaveBeenCalledTimes(1); expect(skillsApi.listResources).toHaveBeenCalledTimes(reads + 1)
})
it('current resource failure retains draft/error and permits retry', async () => {
  vi.mocked(skillsApi.upsertResource).mockRejectedValueOnce(new Error('current save failed'))
  mount(<SkillResourcesSection skillId="a" />); await flush()
  fireEvent.click(screen.getByText('references/a.md')); await flush()
  fireEvent.click(screen.getByRole('button', { name: 'Save file' })); await flush()
  expect(screen.getByRole('alert')).toHaveTextContent('current save failed')
  expect(screen.getByRole('textbox', { name: 'File content' })).toHaveValue('owned content')
  expect(screen.getByRole('button', { name: 'Save file' })).toBeEnabled()
})

describe.each([false, true])('live page rootStrict=%s', strict => {
  it('newer my-search result owns rows after older result arrives', async () => {
    await myPage(strict)
    const a = deferred(collection('obsolete')); const b = deferred(collection('latest'))
    vi.mocked(skillsApi.mySkills).mockImplementation(options => options?.search === 'a' ? a.promise : options?.search === 'b' ? b.promise : Promise.resolve(collection()))
    const input = screen.getByPlaceholderText('Search my skills...')
    fireEvent.change(input, { target: { value: 'a' } }); await advance()
    fireEvent.change(input, { target: { value: 'b' } }); await advance()
    await act(async () => { b.resolve(collection('latest')) })
    expect(screen.getByText('Skill latest')).toBeInTheDocument()
    await act(async () => { a.resolve(collection('obsolete')) })
    expect(screen.getByText('Skill latest')).toBeInTheDocument()
    expect(screen.queryByText('Skill obsolete')).not.toBeInTheDocument()
  })
  it('canceled save cannot reset replacement form but still commits one write', async () => {
    await myPage(strict); const gate = deferred(skill('created'))
    vi.mocked(skillsApi.create).mockReturnValue(gate.promise)
    let dialog = await newForm('A'); fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    dialog = await newForm('B')
    await act(async () => { gate.resolve(skill('created')) })
    expect(screen.getByRole('dialog', { name: 'Create Skill' })).toBeInTheDocument()
    expect(within(dialog).getByPlaceholderText(skillsEnglish.form.namePlaceholder)).toHaveValue('B')
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(skillsApi.create).toHaveBeenCalledTimes(1)
  })
})
it('owned create and import keep normal close/list/warnings behavior', async () => {
  await myPage(); const dialog = await newForm('current')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' })); await flush()
  expect(screen.queryByRole('dialog', { name: 'Create Skill' })).not.toBeInTheDocument()
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['text'], 'skill.md')] } }); await flush()
  expect(screen.getByRole('dialog', { name: 'Skill imported' })).toBeInTheDocument()
  expect(screen.getByText('scripts dropped')).toBeInTheDocument()
})
it('import finishing after actual page departure starts no new collection request', async () => {
  const gate = deferred({ skill: skill('imported'), warnings: [] })
  vi.mocked(skillsApi.importSkill).mockReturnValue(gate.promise)
  const view = await myPage()
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['text'], 'skill.md')] } })
  view.unmount(); const reads = vi.mocked(skillsApi.mySkills).mock.calls.length
  await act(async () => { gate.resolve({ skill: skill('imported'), warnings: [] }) })
  expect(skillsApi.mySkills).toHaveBeenCalledTimes(reads)
  expect(skillsApi.importSkill).toHaveBeenCalledTimes(1)
})
it('current main-form error retains fields and releases loading', async () => {
  await myPage(); vi.mocked(skillsApi.create).mockRejectedValueOnce(new Error('current form error'))
  const dialog = await newForm('current')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' })); await flush()
  expect(within(dialog).getByPlaceholderText(skillsEnglish.form.namePlaceholder)).toHaveValue('current')
  expect(within(dialog).getByRole('button', { name: 'Save' })).toBeEnabled()
  expect(toast.error).toHaveBeenCalledWith('current form error')
})

describe.each([false, true])('share completion rootStrict=%s', strict => {
  it('canceled old share must not close actual parent replacement B', async () => {
    const gate = deferred({ success: true, message: 'shared' }); vi.mocked(skillsApi.share).mockReturnValue(gate.promise)
    vi.mocked(skillsApi.mySkills).mockResolvedValue({ user_skills: [skill('a'), skill('b')], added_skills: [], total: 2 })
    await myPage(strict)
    fireEvent.click(screen.getAllByRole('button', { name: 'Share Skill' })[0]); await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Share Skill' })[1]); await flush()
    await act(async () => { gate.resolve({ success: true, message: 'shared' }) })
    expect(screen.getByRole('dialog', { name: 'Share Skill' })).toBeInTheDocument()
    expect(within(screen.getByRole('dialog', { name: 'Share Skill' })).getByText('Skill b')).toBeInTheDocument()
    expect(skillsApi.share).toHaveBeenCalledTimes(1)
  })
})
it('current share success calls parent once and failure remains retryable', async () => {
  const close = vi.fn(); const success = vi.fn()
  vi.mocked(skillsApi.share).mockResolvedValueOnce({ success: false, message: 'Already shared' })
  mount(<ShareSkillModal skill={skill('a')} onClose={close} onSuccess={success} />); await flush()
  fireEvent.click(screen.getByRole('button', { name: 'Submit for review' })); await flush()
  expect(screen.getByText('Already shared')).toBeInTheDocument(); expect(close).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Submit for review' })); await flush()
  expect(close).toHaveBeenCalledTimes(1); expect(success).toHaveBeenCalledTimes(1)
})

describe.each([false, true])('stats ownership rootStrict=%s', strict => {
  it('range7 remains current after obsolete range30 response', async () => {
    const a = deferred(stats('obsolete')); const b = deferred(stats('latest'))
    vi.mocked(skillsApi.getStats).mockImplementation((_id, days) => days === 7 ? b.promise : a.promise)
    mount(<SkillStatsDialog isOpen onClose={() => {}} projectId="a" />, strict); await flush()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '7' } })
    await act(async () => { b.resolve(stats('latest')) })
    expect(screen.getByText('Stats latest')).toBeInTheDocument()
    await act(async () => { a.resolve(stats('obsolete')) })
    expect(screen.getByText('Stats latest')).toBeInTheDocument()
    expect(screen.queryByText('Stats obsolete')).not.toBeInTheDocument()
  })
})
it('stats project replacement success owns loading and older error cannot reveal prior data', async () => {
  const a = deferred(stats('a')); const b = deferred(stats('b'))
  vi.mocked(skillsApi.getStats).mockImplementation(id => id === 'a' ? a.promise : b.promise)
  const view = mount(<SkillStatsDialog isOpen onClose={() => {}} projectId="a" />); await flush()
  view.rerender(<SkillStatsDialog isOpen onClose={() => {}} projectId="b" />)
  await act(async () => { a.reject(new Error('obsolete stats error')) })
  const stillLoading = screen.getByRole('dialog').querySelector('.animate-spin') !== null
  await act(async () => { b.resolve(stats('b')) })
  expect(stillLoading).toBe(true)
  expect(screen.getByText('Stats b')).toBeInTheDocument()
})
it('owned stats error retains existing empty policy and dialog can close', async () => {
  vi.mocked(skillsApi.getStats).mockRejectedValueOnce(new Error('current stats error'))
  const close = vi.fn(); mount(<SkillStatsDialog isOpen onClose={close} projectId="a" />); await flush()
  expect(screen.queryByText('Total Uses')).not.toBeInTheDocument()
  expect(screen.getByRole('dialog').querySelector('.animate-spin')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Close modal' }))
  expect(close).toHaveBeenCalledTimes(1)
})

it('resource Close also suppresses obsolete content rejection', async () => {
  const gate = deferred(resource()); vi.mocked(skillsApi.getResourceContent).mockReturnValue(gate.promise)
  mount(<SkillResourcesSection skillId="a" />); await flush()
  fireEvent.click(screen.getByText('references/a.md'))
  fireEvent.click(screen.getByRole('button', { name: 'Close editor' }))
  await act(async () => { gate.reject(new Error('obsolete content error')) })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
it('actual edit same ID reopened with a new draft survives old update completion', async () => {
  await myPage(); const gate = deferred(skill('initial')); vi.mocked(skillsApi.update).mockReturnValue(gate.promise)
  fireEvent.click(screen.getByRole('button', { name: 'Edit Skill' })); await flush()
  let dialog = screen.getByRole('dialog', { name: 'Edit Skill' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  fireEvent.click(screen.getByRole('button', { name: 'Edit Skill' })); await flush()
  dialog = screen.getByRole('dialog', { name: 'Edit Skill' })
  fireEvent.change(within(dialog).getByPlaceholderText(skillsEnglish.form.namePlaceholder), { target: { value: 'Reopened draft' } })
  await act(async () => { gate.resolve(skill('initial')) })
  expect(screen.getByRole('dialog', { name: 'Edit Skill' })).toBeInTheDocument()
  expect(within(dialog).getByPlaceholderText(skillsEnglish.form.namePlaceholder)).toHaveValue('Reopened draft')
  expect(skillsApi.update).toHaveBeenCalledTimes(1)
})

it('resource delete completion cannot close a replacement editor', async () => {
  const gate = deferred({ message: 'deleted' }); vi.mocked(skillsApi.deleteResource).mockReturnValue(gate.promise)
  const changed = vi.fn(); mount(<SkillResourcesSection skillId="a" onChange={changed} />); await flush()
  fireEvent.click(screen.getByText('references/a.md')); await flush()
  fireEvent.click(screen.getByRole('button', { name: 'Delete references/a.md' }))
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  fireEvent.click(screen.getByRole('button', { name: 'Add file' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'File content' }), { target: { value: 'Replacement draft' } })
  await act(async () => { gate.resolve({ message: 'deleted' }) })
  expect(screen.getByRole('textbox', { name: 'File content' })).toHaveValue('Replacement draft')
  expect(changed).toHaveBeenCalledTimes(1); expect(skillsApi.deleteResource).toHaveBeenCalledTimes(1)
})
