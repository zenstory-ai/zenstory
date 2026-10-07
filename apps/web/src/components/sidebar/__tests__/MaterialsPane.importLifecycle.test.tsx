import '@testing-library/jest-dom/vitest'
import { StrictMode, useEffect, useState } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { MaterialsPane } from '../MaterialsPane'
import { ProjectProvider, useProject } from '../../../contexts/ProjectContext'
import { MaterialLibraryProvider } from '../../../contexts/MaterialLibraryContext'
import { MaterialAttachmentProvider } from '../../../contexts/MaterialAttachmentContext'
import { projectApi } from '../../../lib/api'
import { materialsApi, type BatchImportResponse } from '../../../lib/materialsApi'
import { toast } from '../../../lib/toast'
import editorEn from '../../../../public/locales/en/editor.json'
import materialsEn from '../../../../public/locales/en/materials.json'
import commonEn from '../../../../public/locales/en/common.json'

const actor = vi.hoisted(() => ({ id: 'material-actor', is_superuser: false }))
vi.mock('../../../contexts/AuthContext', () => ({ useAuth: () => ({ user: actor }) }))
vi.mock('../../../lib/analytics', () => ({ trackEvent: vi.fn() }))
vi.mock('../../../lib/api', () => ({ projectApi: { getAll: vi.fn(), get: vi.fn() } }))
vi.mock('../../../lib/subscriptionApi', async importOriginal => {
  const real = await importOriginal<typeof import('../../../lib/subscriptionApi')>()
  return { ...real, subscriptionApi: { getStatus: vi.fn(async () => ({ tier: 'pro', features: { materials_library: true } })) } }
})
vi.mock('../../../lib/materialsApi', () => ({ materialsApi: {
  getLibrarySummary: vi.fn(), getCharacters: vi.fn(), getPreview: vi.fn(),
  importToProject: vi.fn(), batchImport: vi.fn(),
} }))
vi.mock('../../../lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const i18n = createInstance()
let client: QueryClient
let releases: (() => void)[]
let unexpected: string[]
let mounts: number
const imported = { file_id: 'created-file', title: 'Hero', folder_name: 'Characters', file_type: 'character' }
const full = { results: [imported], failed_count: 0 }
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) }) }
function gate<T>(fallback: T) {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no }); releases.push(() => resolve(fallback))
  return { promise, resolve, reject }
}
function PaneProbe() { useEffect(() => { mounts++ }, []); return <MaterialsPane /> }
function Harness() {
  const project = useProject()
  const [visible, setVisible] = useState(true)
  return <>
    <output data-testid="project">{project.currentProjectId}</output><output data-testid="version">{project.fileTreeVersion}</output>
    <button onClick={() => project.setCurrentProjectId('B')}>Choose B</button>
    <button onClick={() => setVisible(false)}>Unmount pane</button>
    {visible && <PaneProbe />}
  </>
}
async function page(strict = false) {
  const tree = <QueryClientProvider client={client}><I18nextProvider i18n={i18n}>
    <ProjectProvider><MaterialLibraryProvider><MaterialAttachmentProvider><Harness /></MaterialAttachmentProvider></MaterialLibraryProvider></ProjectProvider>
  </I18nextProvider></QueryClientProvider>
  render(strict ? <StrictMode>{tree}</StrictMode> : tree)
  await waitFor(() => expect(screen.getByTestId('project')).toHaveTextContent('A'))
  fireEvent.click(await screen.findByText('Novel One'))
  fireEvent.click(await screen.findByText(i18n.t('editor:fileTree.referenceCharacters')))
  expect(await screen.findByText('Hero')).toBeInTheDocument()
}
function quick() { fireEvent.click(screen.getAllByTitle(i18n.t('editor:fileTree.addToProject'))[0]) }
function select() {
  fireEvent.click(screen.getByTitle(i18n.t('editor:fileTree.batchSelect')))
  fireEvent.click(screen.getAllByRole('checkbox')[0])
}
function batch() { fireEvent.click(screen.getByRole('button', { name: i18n.t('editor:fileTree.batchImport') })) }
beforeEach(async () => {
  vi.resetAllMocks(); releases = []; unexpected = []; mounts = 0
  vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  localStorage.setItem('zenstory_current_project_id:material-actor', 'A')
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => { unexpected.push(String(input)); throw new Error('OFFLINE unexpected') })
  await i18n.init({ lng: 'en', fallbackLng: 'en', ns: ['editor', 'common', 'materials'], defaultNS: 'common',
    resources: { en: { editor: editorEn, common: commonEn, materials: materialsEn } } })
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  vi.mocked(projectApi.getAll).mockResolvedValue([{ id: 'A', name: 'Project A' }, { id: 'B', name: 'Project B' }])
  vi.mocked(materialsApi.getLibrarySummary).mockResolvedValue([{ id: 1, title: 'Novel One', status: 'completed', counts: {
    characters: 2, worldview: 0, golden_fingers: 0, storylines: 0, stories: 0, relationships: 0,
  } }])
  vi.mocked(materialsApi.getCharacters).mockResolvedValue([{ id: '1', name: 'Hero', novel_id: '1', created_at: '2026-10-06T00:00:00Z' }, { id: '2', name: 'Villain', novel_id: '1', created_at: '2026-10-06T00:00:00Z' }])
  vi.mocked(materialsApi.importToProject).mockResolvedValue(imported)
  vi.mocked(materialsApi.batchImport).mockResolvedValue(full)
})
afterEach(async () => {
  cleanup(); releases.forEach(resolve => resolve()); await flush(); await client.cancelQueries(); client.clear()
  expect(unexpected).toEqual([]); expect(client.getQueryCache().getAll()).toHaveLength(0)
  localStorage.clear(); sessionStorage.clear(); i18n.off('languageChanged'); i18n.off('loaded')
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})
describe.each([false, true])('real project refresh rootStrict=%s', strict => {
  it('quick successful import increments real fileTreeVersion exactly once', async () => {
    await page(strict); quick(); await flush()
    expect(materialsApi.importToProject).toHaveBeenCalledExactlyOnceWith({ project_id: 'A', novel_id: 1, entity_type: 'characters', entity_id: 1 })
    expect(toast.success).toHaveBeenCalledExactlyOnceWith(i18n.t('materials:toast.importSuccess'))
    expect(screen.getByTestId('version')).toHaveTextContent(/^1$/)
  })
  it.each(['full', 'partial', 'failed', 'thrown'] as const)('%s batch preserves selection/toast/refresh policy', async mode => {
    const result: BatchImportResponse = mode === 'partial' ? { results: [imported], failed_count: 1 } : mode === 'failed' ? { results: [], failed_count: 1 } : full
    if (mode === 'thrown') vi.mocked(materialsApi.batchImport).mockRejectedValue(new Error('owned failure'))
    else vi.mocked(materialsApi.batchImport).mockResolvedValue(result)
    await page(strict); select(); if (mode === 'partial') fireEvent.click(screen.getAllByRole('checkbox')[1]); batch(); await flush()
    expect(materialsApi.batchImport).toHaveBeenCalledExactlyOnceWith('A', [{ novel_id: 1, entity_type: 'characters', entity_id: 1 }, ...(mode === 'partial' ? [{ novel_id: 1, entity_type: 'characters', entity_id: 2 }] : [])])
    expect.soft(screen.getByTestId('version')).toHaveTextContent(mode === 'full' || mode === 'partial' ? /^1$/ : /^0$/)
    if (mode === 'full') { expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(toast.success).toHaveBeenCalledTimes(1) }
    else { expect(screen.getAllByRole('checkbox')[0]).toBeChecked(); expect(toast.error).toHaveBeenCalledTimes(1); expect(toast.success).not.toHaveBeenCalled() }
  })
  it('old A batch cannot clear new B selection or refresh B', async () => {
    const post = gate(full); vi.mocked(materialsApi.batchImport).mockReturnValue(post.promise)
    await page(strict); const mounted = mounts; select(); batch(); await flush()
    fireEvent.click(screen.getByRole('button', { name: 'Choose B' })); await flush()
    expect(mounts).toBe(mounted); expect(screen.getByTestId('project')).toHaveTextContent('B')
    fireEvent.click(screen.getByTitle(commonEn.cancel)); select()
    expect(screen.getAllByRole('checkbox')[0]).toBeChecked()
    await act(async () => post.resolve(full)); await flush()
    expect(screen.getByTestId('version')).toHaveTextContent(/^0$/)
    expect(screen.getAllByRole('checkbox')[0]).toBeChecked()
    expect(toast.success).toHaveBeenCalledTimes(1)
    vi.mocked(materialsApi.batchImport).mockResolvedValue(full); batch(); await flush()
    expect(vi.mocked(materialsApi.batchImport).mock.calls.at(-1)?.[0]).toBe('B')
    expect(screen.getByTestId('version')).toHaveTextContent(/^1$/)
  })
  it.each(['switch', 'unmount'] as const)('quick A completion after %s keeps completed notification but no current refresh', async change => {
    const post = gate(imported); vi.mocked(materialsApi.importToProject).mockReturnValue(post.promise)
    await page(strict); quick(); fireEvent.click(screen.getByRole('button', { name: change === 'switch' ? 'Choose B' : 'Unmount pane' })); await flush()
    await act(async () => post.resolve(imported)); await flush()
    expect(screen.getByTestId('version')).toHaveTextContent(/^0$/)
    expect(materialsApi.importToProject).toHaveBeenCalledTimes(1); expect(toast.success).toHaveBeenCalledTimes(1)
  })
})
