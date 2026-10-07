import '@testing-library/jest-dom/vitest'
import { StrictMode, useEffect } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import PromptEditor from '../PromptEditor'
import PromptManagement from '../PromptManagement'
import SkillReviewPage from '../SkillReviewPage'
import { adminApi, type PendingSkill } from '../../../lib/adminApi'
import { toast } from '../../../lib/toast'
import adminEn from '../../../../public/locales/en/admin.json'
import adminZh from '../../../../public/locales/zh/admin.json'
import commonEn from '../../../../public/locales/en/common.json'
import commonZh from '../../../../public/locales/zh/common.json'

vi.mock('../../../lib/adminApi', () => ({ adminApi: {
  getPrompt: vi.fn(), upsertPrompt: vi.fn(), deletePrompt: vi.fn(), getPrompts: vi.fn(), reloadPrompts: vi.fn(),
  getPendingSkills: vi.fn(), approveSkill: vi.fn(), rejectSkill: vi.fn(), unpublishSkill: vi.fn(), getSkillReviewResources: vi.fn(),
} }))
vi.mock('../../../lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const i18n = createInstance()
let client: QueryClient
let router: ReturnType<typeof createMemoryRouter> | undefined
let completions: (() => void)[]
let unexpected: string[]
let mounts: number
function gate<T>(fallback: T) {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  completions.push(() => resolve(fallback))
  return { promise, resolve, reject }
}
const flush = async (ms = 15) => { await act(async () => { await new Promise(resolve => setTimeout(resolve, ms)) }) }
const skill = (id: string, status: PendingSkill['status'] = 'pending'): PendingSkill => ({
  id, name: `Skill ${id}`, instructions: 'Raw instructions', description: null, category: 'writing',
  tags: [], skill_metadata: {}, resource_count: 0, source: 'community', author_id: null, author_name: null,
  status, reviewed_by: null, reviewer_name: null, reviewed_at: null, rejection_reason: null, created_at: '2026-10-06T00:32:00Z',
})
const config = (project_type = 'novel', version = 7) => ({ id: project_type, project_type, role_definition: 'Existing role',
  capabilities: 'Existing capabilities', directory_structure: 'Directory', content_structure: 'Content', file_types: 'Files',
  writing_guidelines: 'Guidelines', primary_content_type: 'novel', include_dialogue_guidelines: true, is_active: true, version,
  created_at: '2026-10-06T00:32:00Z', updated_at: '2026-10-06T00:32:00Z' })
function ProbeEditor() { useEffect(() => { mounts++ }, []); return <PromptEditor /> }
function mount(element: React.ReactNode, strict = false) {
  render(<QueryClientProvider client={client}><I18nextProvider i18n={i18n}>
    {strict ? <StrictMode>{element}</StrictMode> : element}
  </I18nextProvider></QueryClientProvider>)
}
async function editor(path = '/admin/prompts/novel') {
  router = createMemoryRouter([
    { path: '/admin/prompts/:projectType', element: <ProbeEditor /> },
    { path: '/admin/prompts', element: <p>Prompt list destination</p> },
    { path: '/elsewhere', element: <p>Manual destination</p> },
  ], { initialEntries: [path] })
  mount(<RouterProvider router={router} />)
  await flush()
}
const changeStatus = (status: string) => fireEvent.change(screen.getByRole('combobox'), { target: { value: status } })
const label = (key: string) => i18n.t(`admin:${key}`)
function expand(id: string) {
  const card = screen.getByText(`Skill ${id}`).closest('.rounded-xl')!
  fireEvent.click(within(card as HTMLElement).getAllByRole('button').at(-1)!)
}
beforeEach(async () => {
  vi.resetAllMocks(); completions = []; unexpected = []; router = undefined; mounts = 0
  vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  localStorage.setItem('zenstory-language', 'en')
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => { unexpected.push(String(input)); throw new Error('OFFLINE fetch') })
  await i18n.init({ lng: 'en', fallbackLng: 'en', ns: ['admin', 'common'], defaultNS: 'common',
    resources: { en: { admin: adminEn, common: commonEn }, zh: { admin: adminZh, common: commonZh } } })
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false, refetchOnMount: false } } })
  vi.mocked(adminApi.getPrompt).mockResolvedValue(config())
  vi.mocked(adminApi.upsertPrompt).mockResolvedValue(config('novel', 8))
  vi.mocked(adminApi.deletePrompt).mockResolvedValue({ message: 'deleted' })
  vi.mocked(adminApi.getPrompts).mockResolvedValue([config()])
  vi.mocked(adminApi.getPendingSkills).mockImplementation(async status => [skill(status ?? 'pending', status)])
  vi.mocked(adminApi.approveSkill).mockResolvedValue({ message: 'approved', skill_id: 'pending' })
  vi.mocked(adminApi.rejectSkill).mockResolvedValue({ message: 'rejected', skill_id: 'pending' })
  vi.mocked(adminApi.unpublishSkill).mockResolvedValue({ message: 'unpublished', skill_id: 'approved' })
  vi.mocked(adminApi.getSkillReviewResources).mockResolvedValue([])
})
afterEach(async () => {
  cleanup(); router?.dispose(); completions.forEach(resolve => resolve()); await flush()
  await client.cancelQueries(); client.clear()
  expect(client.getQueryCache().getAll()).toHaveLength(0); expect(unexpected).toEqual([])
  localStorage.clear(); sessionStorage.clear(); i18n.off('languageChanged'); i18n.off('loaded')
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs()
})

describe.each([false, true])('review ownership rootStrict=%s', strict => {
  it.each(['success', 'error'] as const)('keeps selected B after older A %s', async outcome => {
    const a: ReturnType<typeof gate<PendingSkill[]>>[] = []
    const b = gate<PendingSkill[]>([])
    vi.mocked(adminApi.getPendingSkills).mockImplementation(status => {
      if (status === 'approved') return b.promise
      const request = gate<PendingSkill[]>([]); a.push(request); return request.promise
    })
    mount(<SkillReviewPage />, strict); await flush(); changeStatus('approved'); await flush()
    await act(async () => b.resolve([skill('B', 'approved')])); await flush()
    expect(screen.getByText('Skill B')).toBeInTheDocument()
    await act(async () => a.forEach(request => outcome === 'success' ? request.resolve([skill('A')]) : request.reject(new Error('obsolete A error'))))
    await flush()
    console.info('M18_REVIEW_TRACE', JSON.stringify({ strict, requests: vi.mocked(adminApi.getPendingSkills).mock.calls, rows: document.body.textContent, post: vi.mocked(adminApi.approveSkill).mock.calls }))
    expect(screen.getByRole('combobox')).toHaveValue('approved')
    expect(screen.getByText('Skill B')).toBeInTheDocument()
    expect(screen.queryByText('Skill A')).not.toBeInTheDocument()
    expect(screen.queryByText('obsolete A error')).not.toBeInTheDocument()
    expect(screen.queryByText(commonEn.loading)).not.toBeInTheDocument()
  })
  it('does not let completed A decision refresh overwrite B filter', async () => {
    const post = gate({ message: 'approved', skill_id: 'pending' })
    vi.mocked(adminApi.approveSkill).mockReturnValue(post.promise)
    mount(<SkillReviewPage />, strict); await flush()
    fireEvent.click(screen.getByRole('button', { name: label('skills.approve') }))
    fireEvent.click(screen.getByRole('button', { name: label('skills.confirmApprove') }))
    expect(adminApi.approveSkill).toHaveBeenCalledExactlyOnceWith('pending')
    changeStatus('approved'); await flush(); expect(screen.getByText('Skill approved')).toBeInTheDocument()
    await act(async () => post.resolve({ message: 'approved', skill_id: 'pending' })); await flush()
    console.info('M18_REVIEW_TRACE', JSON.stringify({ strict, requests: vi.mocked(adminApi.getPendingSkills).mock.calls, rows: document.body.textContent, post: vi.mocked(adminApi.approveSkill).mock.calls }))
    expect(screen.getByRole('combobox')).toHaveValue('approved')
    expect(screen.getByText('Skill approved')).toBeInTheDocument()
    expect(screen.queryByText('Skill pending')).not.toBeInTheDocument()
  })
  it('retains ordinary owned success and current error retry', async () => {
    vi.mocked(adminApi.getPendingSkills).mockRejectedValueOnce(new Error('current load failure'))
    mount(<SkillReviewPage />, strict); await flush()
    if (screen.queryByText('current load failure')) fireEvent.click(screen.getByRole('button', { name: commonEn.retry }))
    await flush(); expect(screen.getByText('Skill pending')).toBeInTheDocument()
    vi.mocked(adminApi.getPendingSkills).mockResolvedValue([])
    fireEvent.click(screen.getByRole('button', { name: label('skills.approve') }))
    fireEvent.click(screen.getByRole('button', { name: label('skills.confirmApprove') })); await flush()
    expect(adminApi.approveSkill).toHaveBeenCalledExactlyOnceWith('pending')
    expect(screen.queryByText('Skill pending')).not.toBeInTheDocument()
  })
})

it('resets existing-to-new same-instance form and never sends old expected_version', async () => {
  await editor(); expect(mounts).toBe(1)
  expect(screen.getByLabelText(label('promptEditor.roleDefinition'))).toHaveValue('Existing role')
  fireEvent.change(screen.getByLabelText(label('promptEditor.roleDefinition')), { target: { value: 'Old dirty role' } })
  await act(async () => router!.navigate('/admin/prompts/new')); await flush()
  expect(mounts).toBe(1)
  expect.soft(screen.getByLabelText(label('promptEditor.roleDefinition'))).toHaveValue('')
  expect.soft(screen.getByLabelText(label('promptEditor.capabilities'))).toHaveValue('')
  fireEvent.change(screen.getByLabelText(label('promptEditor.roleDefinition')), { target: { value: 'New role' } })
  fireEvent.change(screen.getByLabelText(label('promptEditor.capabilities')), { target: { value: 'New capabilities' } })
  fireEvent.click(screen.getByRole('button', { name: label('promptEditor.save') })); await flush()
  expect(adminApi.upsertPrompt).toHaveBeenCalledWith('novel', expect.not.objectContaining({ expected_version: expect.anything() }))
})
it.each(['new', 'novel'])('retains healthy %s form save contract', async type => {
  await editor(`/admin/prompts/${type}`)
  fireEvent.change(screen.getByLabelText(label('promptEditor.roleDefinition')), { target: { value: '  Live role  ' } })
  fireEvent.change(screen.getByLabelText(label('promptEditor.capabilities')), { target: { value: '  Live capabilities  ' } })
  fireEvent.click(screen.getByRole('button', { name: label('promptEditor.save') })); await flush()
  expect(adminApi.upsertPrompt).toHaveBeenCalledWith('novel', expect.objectContaining({ role_definition: 'Live role', capabilities: 'Live capabilities', ...(type === 'novel' ? { expected_version: 7 } : {}) }))
  expect(toast.success).toHaveBeenCalledExactlyOnceWith(label('promptEditor.saveSuccess'))
})
it.each(['save', 'delete'] as const)('old completed %s timer does not navigate after departure', async operation => {
  await editor()
  if (operation === 'save') fireEvent.click(screen.getByRole('button', { name: label('promptEditor.save') }))
  else { fireEvent.click(screen.getAllByRole('button', { name: label('prompts.delete') })[0]); fireEvent.click(screen.getByRole('button', { name: commonEn.confirm })) }
  await flush(); expect(toast.success).toHaveBeenCalledTimes(1)
  await act(async () => router!.navigate('/elsewhere')); await flush(550)
  expect(router!.state.location.pathname).toBe('/elsewhere')
})
it.each(['save', 'delete'] as const)('live completed %s retains 500ms destination', async operation => {
  await editor()
  if (operation === 'save') fireEvent.click(screen.getByRole('button', { name: label('promptEditor.save') }))
  else { fireEvent.click(screen.getAllByRole('button', { name: label('prompts.delete') })[0]); fireEvent.click(screen.getByRole('button', { name: commonEn.confirm })) }
  await flush(); expect(router!.state.location.pathname).toBe('/admin/prompts/novel')
  await flush(550); expect(router!.state.location.pathname).toBe('/admin/prompts')
})

it.each(['en', 'zh'])('formats naive/Z/offset prompt and skill dates as UTC in %s locale', async lng => {
  vi.stubEnv('TZ', 'America/Los_Angeles')
  expect(new Date('2026-10-06T00:32:00').getTimezoneOffset()).not.toBe(0)
  localStorage.setItem('zenstory-language', lng); await i18n.changeLanguage(lng)
  const dates = ['2026-10-06T00:32:00', '2026-10-06T00:32:00Z', '2026-10-06T08:32:00+08:00']
  const locale = lng === 'en' ? 'en-US' : 'zh-CN'
  const dateTime = new Date(dates[1]).toLocaleString(locale, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  vi.mocked(adminApi.getPrompts).mockResolvedValue(dates.map((updated_at, index) => ({ ...config(`type${index}`), updated_at })))
  mount(<MemoryRouter><PromptManagement /></MemoryRouter>); await flush()
  expect.soft(screen.getAllByText(dateTime)).toHaveLength(3)
  cleanup()
  vi.mocked(adminApi.getPendingSkills).mockResolvedValue(dates.map((date, index) => ({ ...skill(`date${index}`, 'approved'), created_at: date, reviewed_at: date })))
  mount(<SkillReviewPage />); await flush()
  expect.soft(screen.getAllByText(new Date(dates[1]).toLocaleDateString(locale, { year: 'numeric', month: '2-digit', day: '2-digit' }))).toHaveLength(3)
  expect(screen.getAllByText(`${i18n.t('admin:skills.reviewedAt')}: ${dateTime}`)).toHaveLength(3)
})
it('keeps prompt missing/invalid date fallback', async () => {
  vi.mocked(adminApi.getPrompts).mockResolvedValue([{ ...config('none'), updated_at: undefined }, { ...config('invalid'), updated_at: 'broken' }])
  mount(<MemoryRouter><PromptManagement /></MemoryRouter>); await flush(); expect(screen.getAllByText('-')).toHaveLength(2)
})
it('recovers raw resources after owned locale-change retry succeeds', async () => {
  vi.mocked(adminApi.getPendingSkills).mockResolvedValue([{ ...skill('resource'), resource_count: 1 }])
  vi.mocked(adminApi.getSkillReviewResources).mockRejectedValueOnce(new Error('first resources failure')).mockResolvedValue([{ path: 'guide.md', size: 4, content: 'Recovered raw content' }])
  mount(<SkillReviewPage />); await flush(); expand('resource'); await flush()
  expect(screen.getByText('first resources failure')).toBeInTheDocument()
  await act(async () => i18n.changeLanguage('zh')); await flush()
  expect(adminApi.getSkillReviewResources).toHaveBeenCalledTimes(2)
  expect(screen.getByText('Recovered raw content')).toBeInTheDocument()
  expect(screen.queryByText('first resources failure')).not.toBeInTheDocument()
})
it('keeps live raw-resource success and rendered toggle', async () => {
  vi.mocked(adminApi.getPendingSkills).mockResolvedValue([{ ...skill('resource'), resource_count: 1 }])
  vi.mocked(adminApi.getSkillReviewResources).mockResolvedValue([{ path: 'guide.md', size: 4, content: 'Raw guide' }])
  mount(<SkillReviewPage />); await flush(); expand('resource'); await flush()
  expect(screen.getByTestId('skill-review-raw-instructions')).toHaveTextContent('Raw instructions')
  expect(screen.getByText('Raw guide')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: label('skills.renderedView') })); await flush()
  expect(screen.queryByTestId('skill-review-raw-instructions')).not.toBeInTheDocument()
})
it('keeps pending reason dialog locked against replacement', async () => {
  vi.mocked(adminApi.getPendingSkills).mockResolvedValue([skill('A'), skill('B')])
  const post = gate({ message: 'rejected', skill_id: 'A' }); vi.mocked(adminApi.rejectSkill).mockReturnValue(post.promise)
  mount(<SkillReviewPage />); await flush()
  fireEvent.click(within(screen.getByText('Skill A').closest('.rounded-xl') as HTMLElement).getByRole('button', { name: label('skills.reject') }))
  fireEvent.change(screen.getByPlaceholderText(label('skills.rejectPlaceholder')), { target: { value: 'A reason' } })
  fireEvent.click(screen.getByRole('button', { name: label('skills.confirmReject') })); await flush()
  const cancel = screen.getByRole('button', { name: commonEn.cancel })
  expect.soft(cancel).toBeDisabled()
  fireEvent.click(cancel)
  if (!screen.queryByPlaceholderText(label('skills.rejectPlaceholder'))) {
    fireEvent.click(within(screen.getByText('Skill B').closest('.rounded-xl') as HTMLElement).getByRole('button', { name: label('skills.reject') }))
    fireEvent.change(screen.getByPlaceholderText(label('skills.rejectPlaceholder')), { target: { value: 'B reason' } })
  }
  await act(async () => post.resolve({ message: 'rejected', skill_id: 'A' })); await flush()
  expect(adminApi.rejectSkill).toHaveBeenCalledExactlyOnceWith('A', 'A reason')
  // A completion must not silently erase a B dialog if pending close was allowed.
  const bReason = screen.queryByDisplayValue('B reason')
  if (!cancel.hasAttribute('disabled')) expect(bReason).toBeInTheDocument()
})
