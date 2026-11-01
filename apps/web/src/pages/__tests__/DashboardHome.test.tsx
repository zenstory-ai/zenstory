import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ApiError } from '../../lib/apiClient'
import zhDashboard from '../../../public/locales/zh/dashboard.json'
import enDashboard from '../../../public/locales/en/dashboard.json'

let mockLanguage = 'zh-CN'
const defaultMockUser: { id: string; username: string; nickname: string | null; email: string } = {
  id: 'user-1',
  username: 'tester',
  nickname: null,
  email: 'tester@example.com',
}
let mockUser = defaultMockUser
let mockIsMobile = false
let mockIsTablet = false
let mockProjects: Array<{ id: string; name: string; description?: string; project_type: 'novel' | 'short' | 'screenplay'; updated_at?: string | null }> = []
let mockProjectsLoading = false
let mockFeaturedState: {
  featured: Array<{ id: string; name: string; description?: string; project_type: 'novel' }>;
  isLoading: boolean;
  isFetching: boolean;
} = {
  featured: [],
  isLoading: false,
  isFetching: false,
}
let mockDashboardInspirations: Array<{
  id: string;
  title: string;
  hook: string;
  tags: string[];
  source: string;
}> = []

const {
  mockInspirationsConfig,
  mockUseFeaturedInspirations,
  mockUseDashboardInspirations,
  mockGetProgress,
} = vi.hoisted(() => ({
  mockInspirationsConfig: {
    enabled: false,
  },
  mockUseFeaturedInspirations: vi.fn(),
  mockUseDashboardInspirations: vi.fn(),
  mockGetProgress: vi.fn(async () => [] as unknown[]),
}))

const mockFirstWork = vi.hoisted(() => ({ get: vi.fn(), initialize: vi.fn() }))
vi.mock('../../lib/firstWorkApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../lib/firstWorkApi')>(),
  firstWorkApi: mockFirstWork,
}))
const firstWorkState = (status = 'ineligible') => ({ status, project_id: null, file_id: null, available: false,
  static_guidance: false, opened_at: null, first_manual_write_at: null, first_ai_requested_at: null })

const mockNavigate = vi.fn()
const mockCreateProject = vi.fn()
const mockDeleteProject = vi.fn()

const mockT = (
  key: string,
  options?: { defaultValue?: string; name?: string } | string,
) => {
  const optionObj = typeof options === 'string' ? undefined : options
  const translations: Record<string, string> = {
    'hero.greeting': `你好，${optionObj?.name ?? '{{name}}'}`,
    'hero.defaultName': '作者',
    'hero.question': '今天想创作些什么呢？',
    'inspirations.featured': '精选灵感',
    'inspirations.viewAll': '查看全部',
    'inspirations.emptyTitle': '暂无精选灵感',
    'inspirations.emptyHint': '去灵感库看看更多故事开头。',
    'inspirations.emptyCta': '查看灵感库',
    'projects.viewAll': '浏览全部',
    'projects.recent': '最近项目',
    'dashboard:projects.openProject': `Open project ${optionObj?.name ?? ''}`,
    'projects.empty': '还没有任何项目',
    'projects.emptyHint': '在上方输入灵感，点击「开始创作」创建你的第一个项目',
    'common.createButton': '开始创作',
    'firstWork.title': '你的第一部作品已备好',
    'firstWork.open': '打开作品',
    'common:retry': '重试',
    'projectType.novel.name': '长篇小说',
    'inspiration.novelDesc': 'AI 将根据你的灵感，帮你构思故事框架、设定世界观和人物角色',
    'inspiration.novelPlaceholder': '请输入灵感',
    'dashboard:inspiration.example.novel': '例如：主角穿越成王朝里最不起眼的六皇子，只想悄悄攒下兵权自保，却被父皇和几位兄长一步步推到台前。',
    'defaults.novel': '我的小说',
  }
  if (typeof options === 'string') {
    return translations[key] || options || key
  }
  return translations[key] || options?.defaultValue || key
}

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: mockT,
    i18n: {
      get language() {
        return mockLanguage
      },
      get resolvedLanguage() {
        return mockLanguage
      },
    },
  }),
}))

const mockReportActivationEvent = vi.fn()
vi.mock('../../lib/activationEvents', () => ({
  reportActivationEvent: (...args: unknown[]) => mockReportActivationEvent(...args),
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockUser,
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: mockProjects,
    loading: mockProjectsLoading,
    createProject: mockCreateProject,
    deleteProject: mockDeleteProject,
    refreshProjects: vi.fn(async () => {}),
  }),
}))

vi.mock('../../hooks/useInspirations', () => ({
  useFeaturedInspirations: mockUseFeaturedInspirations,
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => mockIsMobile,
  useIsTablet: () => mockIsTablet,
}))

vi.mock('../../hooks/useDashboardInspirations', () => ({
  useDashboardInspirations: mockUseDashboardInspirations,
}))

vi.mock('../../components/subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: ({ open, title }: { open: boolean; title: string }) =>
    open ? <div data-testid="upgrade-modal">{title}</div> : null,
}))

const mockUploadDraft = vi.hoisted(() => vi.fn())
vi.mock('../../lib/api', () => ({
  projectApi: {
    getTemplates: vi.fn().mockResolvedValue(null),
    getProgress: mockGetProgress,
  },
  fileApi: { uploadDraft: mockUploadDraft },
}))

vi.mock('../../lib/announcementApi', () => ({
  announcementApi: { getCurrent: vi.fn().mockResolvedValue(null) },
}))

vi.mock('../../config/inspirations', () => ({
  inspirationsConfig: mockInspirationsConfig,
}))

vi.mock('../../components/subscription/QuotaBadge', () => ({
  QuotaBadge: () => <div data-testid="quota-badge" />,
}))

import DashboardHome from '../DashboardHome'
import { projectApi } from '../../lib/api'
import { subscriptionApi } from '../../lib/subscriptionApi'
import { toast } from '../../lib/toast'
import type { QuotaResponse } from '../../types/subscription'
import {
  PREFERRED_PROJECT_TYPE_STORAGE_KEY,
  PREFERRED_PROJECT_TYPE_TTL_MS,
  getPreferredProjectType,
  setPreferredProjectType,
} from '../../lib/preferredProjectType'

const renderDashboardHome = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DashboardHome />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('DashboardHome featured inspirations section', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFirstWork.get.mockResolvedValue(firstWorkState())
    mockFirstWork.initialize.mockReset()
    mockCreateProject.mockResolvedValue({ id: 'project-created' })
    mockLanguage = 'zh-CN'
    mockUser = defaultMockUser
    mockIsMobile = false
    mockIsTablet = false
    mockProjects = []
    mockProjectsLoading = false
    mockFeaturedState = {
      featured: [],
      isLoading: false,
      isFetching: false,
    }
    mockInspirationsConfig.enabled = false
    mockUseFeaturedInspirations.mockImplementation(() => mockFeaturedState)
    mockUseDashboardInspirations.mockImplementation(() => mockDashboardInspirations)
    mockDashboardInspirations = []
    vi.mocked(projectApi.getTemplates).mockResolvedValue(null)
    localStorage.removeItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY)
    localStorage.removeItem(`${PREFERRED_PROJECT_TYPE_STORAGE_KEY}:${defaultMockUser.id}`)
    localStorage.removeItem(`zenstory_held_dashboard_idea:${defaultMockUser.id}`)
    vi.spyOn(subscriptionApi, 'getQuota').mockResolvedValue(quotaWith(2))
  })

  const quotaWith = (used: number, limit = 10) =>
    ({
      ai_conversations: { used, limit, reset_at: '2026-10-09T16:00:00Z' },
    }) as unknown as QuotaResponse

  it('reports that the account reached the home page, with the layout only', () => {
    mockIsMobile = true
    renderDashboardHome()
    expect(mockReportActivationEvent).toHaveBeenCalledWith(defaultMockUser.id, { event_name: 'dashboard_viewed', is_mobile: true })
    expect(mockReportActivationEvent).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ event_name: 'ai_send_blocked_quota' }))
  })

  it('does not create a project when Enter confirms an IME composition', async () => {
    renderDashboardHome()
    const input = screen.getByTestId('dashboard-inspiration-input')
    fireEvent.change(input, { target: { value: '灯塔守夜人' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 })
    expect(mockCreateProject).not.toHaveBeenCalled()
    expect(input).toHaveValue('灯塔守夜人')
  })

  it('keeps the idea and explains, instead of creating an empty project, when today\'s AI messages are used up', async () => {
    vi.mocked(subscriptionApi.getQuota).mockResolvedValue(quotaWith(10))
    const { unmount } = renderDashboardHome()
    await waitFor(() => expect(subscriptionApi.getQuota).toHaveBeenCalled())

    const input = screen.getByTestId('dashboard-inspiration-input')
    fireEvent.change(input, { target: { value: '写一个关于灯塔守夜人的短故事' } })
    await waitFor(() =>
      expect(localStorage.getItem(`zenstory_held_dashboard_idea:${defaultMockUser.id}`)).toBe('写一个关于灯塔守夜人的短故事'),
    )
    fireEvent.click(screen.getByTestId('create-project-button'))

    expect(await screen.findByTestId('upgrade-modal')).toHaveTextContent('今天的免费 AI 消息用完了')
    expect(mockCreateProject).not.toHaveBeenCalled()
    expect(mockReportActivationEvent).toHaveBeenCalledWith(defaultMockUser.id, {
      event_name: 'ai_send_blocked_quota',
      surface: 'dashboard',
      is_mobile: false,
    })
    expect(mockNavigate).not.toHaveBeenCalled()
    expect(input).toHaveValue('写一个关于灯塔守夜人的短故事')

    // A reload brings the idea back.
    unmount()
    renderDashboardHome()
    expect(screen.getByTestId('dashboard-inspiration-input')).toHaveValue('写一个关于灯塔守夜人的短故事')
  })

  const heldIdeaKey = `zenstory_held_dashboard_idea:${defaultMockUser.id}`
  /** First-day guide whose next step opens the create dialog (the "checklist" path). */
  it('saves edits to a restored idea after the reset, so a reload brings back the latest version', async () => {
    localStorage.setItem(heldIdeaKey, '灯塔守夜人')
    const { unmount } = renderDashboardHome()
    await waitFor(() => expect(subscriptionApi.getQuota).toHaveBeenCalled())
    const input = screen.getByTestId('dashboard-inspiration-input')
    expect(input).toHaveValue('灯塔守夜人')

    fireEvent.change(input, { target: { value: '灯塔守夜人，第三夜看见了船' } })
    await waitFor(() => expect(localStorage.getItem(heldIdeaKey)).toBe('灯塔守夜人，第三夜看见了船'))

    unmount()
    renderDashboardHome()
    expect(screen.getByTestId('dashboard-inspiration-input')).toHaveValue('灯塔守夜人，第三夜看见了船')
  })

  it('sends a held idea the next day and then forgets it', async () => {
    localStorage.setItem(heldIdeaKey, '灯塔守夜人')
    renderDashboardHome()
    await waitFor(() => expect(subscriptionApi.getQuota).toHaveBeenCalled())
    expect(screen.getByTestId('dashboard-inspiration-input')).toHaveValue('灯塔守夜人')

    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created'))
    expect(JSON.parse(localStorage.getItem('zenstory_inspiration_project-created') ?? '{}').content).toBe('灯塔守夜人')
    await waitFor(() => expect(localStorage.getItem(heldIdeaKey)).toBeNull())
  })

  it('still creates a project with no idea while the day is used up (nothing is sent to the AI)', async () => {
    vi.mocked(subscriptionApi.getQuota).mockResolvedValue(quotaWith(10))
    renderDashboardHome()
    await waitFor(() => expect(subscriptionApi.getQuota).toHaveBeenCalled())

    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => expect(mockCreateProject).toHaveBeenCalled())
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it.each([
    ['messages remain', quotaWith(9)],
    ['Pro has no daily count', quotaWith(999, -1)],
  ])('creates the project and hands the idea to the chat when %s', async (_label, quota) => {
    vi.mocked(subscriptionApi.getQuota).mockResolvedValue(quota)
    renderDashboardHome()
    await waitFor(() => expect(subscriptionApi.getQuota).toHaveBeenCalled())

    fireEvent.change(screen.getByTestId('dashboard-inspiration-input'), { target: { value: '灯塔守夜人' } })
    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created'))
    expect(JSON.parse(localStorage.getItem('zenstory_inspiration_project-created') ?? '{}').content).toBe('灯塔守夜人')
    expect(localStorage.getItem(`zenstory_held_dashboard_idea:${defaultMockUser.id}`)).toBeNull()
  })

  it.each(['desktop', 'tablet', 'mobile'])('anchors recent project metadata to the card bottom on %s', async (viewport) => {
    mockIsMobile = viewport === 'mobile'
    mockIsTablet = viewport === 'tablet'
    mockProjects = ['', 'Short description', 'A longer description that wraps across multiple lines. '.repeat(8)].map(
      (description, index) => ({
        id: `layout-${index}`,
        name: `Layout project ${index}`,
        description,
        project_type: 'novel',
        updated_at: '2026-04-07T00:00:00Z',
      }),
    )

    renderDashboardHome()

    for (const project of mockProjects) {
      const card = (await screen.findByRole('button', { name: `Open project ${project.name}` })).closest('article')!
      const content = card.querySelector(':scope > .relative')
      const footer = content?.lastElementChild
      const meta = footer?.lastElementChild

      expect(card).toHaveClass('flex', 'flex-col')
      expect(content).toHaveClass('flex', 'flex-1', 'flex-col')
      // One bottom-anchored footer holds the progress line and the type/time row.
      expect(footer).toHaveClass('mt-auto')
      expect(footer).toHaveAttribute('data-testid', 'project-card-footer')
      expect(meta).toHaveClass('flex', 'items-center', 'justify-between')
      expect(meta?.children).toHaveLength(2)
      expect(meta?.firstElementChild).toHaveTextContent('长篇小说')
      expect(meta?.lastElementChild).not.toBeEmptyDOMElement()
    }
  })

  it('shows how far each project has got, or that its outline is ready to write from', async () => {
    mockProjects = [
      { id: 'p-writing', name: 'Writing', description: '', project_type: 'novel', updated_at: '2026-04-07T00:00:00Z' },
      { id: 'p-outline', name: 'Outline', description: '', project_type: 'novel', updated_at: '2026-04-07T00:00:00Z' },
      { id: 'p-empty', name: 'Empty', description: '', project_type: 'novel', updated_at: '2026-04-07T00:00:00Z' },
    ]
    mockGetProgress.mockResolvedValueOnce([
      { project_id: 'p-writing', written_units: 12, word_count: 32_480, framework_ready: false },
      { project_id: 'p-outline', written_units: 0, word_count: 0, framework_ready: true },
      { project_id: 'p-empty', written_units: 0, word_count: 0, framework_ready: false },
    ])

    renderDashboardHome()

    const writing = await screen.findByRole('button', { name: 'Open project Writing' })
    await waitFor(() => expect(within(writing.closest('article')!).getByTestId('project-progress')).toHaveTextContent('dashboard:projectProgress.chapters'))
    expect(within(screen.getByRole('button', { name: 'Open project Outline' }).closest('article')!).getByTestId('project-progress'))
      .toHaveTextContent('dashboard:projectProgress.frameworkReady')
    expect(within(screen.getByRole('button', { name: 'Open project Empty' }).closest('article')!).queryByTestId('project-progress')).toBeNull()
  })

  it('retains the baseline templates and reports a template API failure', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(projectApi.getTemplates).mockRejectedValueOnce(new Error('templates unavailable'))

    renderDashboardHome()

    await waitFor(() => {
      expect(projectApi.getTemplates).toHaveBeenCalledTimes(1)
      expect(consoleError).toHaveBeenCalledWith(
        '[ERROR]',
        'Failed to load templates:',
        expect.objectContaining({ message: 'templates unavailable' }),
      )
    })
    expect(screen.getByRole('button', { name: '长篇小说' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'projectType.short.name' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'projectType.screenplay.name' })).toBeInTheDocument()

    consoleError.mockRestore()
  })

  it('does not mount or fetch featured inspirations when the feature is disabled', () => {
    renderDashboardHome()

    expect(screen.queryByTestId('featured-inspirations-section')).not.toBeInTheDocument()
    expect(screen.queryByTestId('dashboard-real-inspirations')).not.toBeInTheDocument()
    expect(screen.queryByText('精选灵感')).not.toBeInTheDocument()
    expect(screen.queryByText('如果你还没想好，可以从这里开始：')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '换一批' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看灵感库' })).not.toBeInTheDocument()
    expect(mockUseFeaturedInspirations).not.toHaveBeenCalled()
    expect(mockUseDashboardInspirations).not.toHaveBeenCalled()
    // The placeholder is one example premise, marked as an example rather than text already filled in.
    expect(screen.getByTestId('dashboard-inspiration-input').getAttribute('placeholder')).toMatch(/^例如：主角穿越成.+推到台前。$/)
  })

  it('keeps manual idea creation available when inspirations are disabled', async () => {
    renderDashboardHome()

    fireEvent.change(screen.getByTestId('dashboard-inspiration-input'), {
      target: { value: '失忆侦探发现自己就是凶手' },
    })
    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalledWith(expect.any(String), undefined, 'novel')
      expect(mockNavigate).toHaveBeenCalledWith('/project/project-created')
    })
  })

  it('shows featured section and renders empty CTA when explicitly enabled', () => {
    mockInspirationsConfig.enabled = true

    renderDashboardHome()

    expect(screen.getByTestId('featured-inspirations-section')).toBeInTheDocument()
    expect(mockUseFeaturedInspirations).toHaveBeenCalledWith(3)
    expect(screen.getByText('暂无精选灵感')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '查看灵感库' }))
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard/inspirations')
  })

  it('renders real inspiration cards and fills the input when clicked', () => {
    mockInspirationsConfig.enabled = true
    mockDashboardInspirations = [
      {
        id: 'qimao:1983473',
        title: '狂兽战神',
        hook: '被皇朝与挚爱联手陷害的神将，流放后反掌万兽',
        tags: ['玄幻奇幻', '东方玄幻'],
        source: 'qimao_detail',
      },
    ]

    renderDashboardHome()

    expect(screen.getByTestId('dashboard-real-inspirations')).toBeInTheDocument()
    expect(mockUseDashboardInspirations).toHaveBeenCalledWith('novel', 2, 0)
    // The placeholder is one example premise, marked as an example rather than text already filled in.
    expect(screen.getByTestId('dashboard-inspiration-input').getAttribute('placeholder')).toMatch(/^例如：主角穿越成.+推到台前。$/)
    fireEvent.click(screen.getByRole('button', { name: /狂兽战神/i }))

    expect(screen.getByTestId('dashboard-inspiration-input')).toHaveValue(
      '《狂兽战神》：被皇朝与挚爱联手陷害的神将，流放后反掌万兽',
    )

    fireEvent.click(screen.getByRole('button', { name: '换一批' }))
    expect(mockUseDashboardInspirations).toHaveBeenLastCalledWith('novel', 2, 1)
  })

  it('keeps existing featured cards when featured inspirations are available', () => {
    mockInspirationsConfig.enabled = true
    mockFeaturedState = {
      featured: [
        {
          id: 'insp-1',
          name: '赛博修仙都市',
          description: '未来都市与修仙体系结合',
          project_type: 'novel',
        },
      ],
      isLoading: false,
      isFetching: false,
    }

    renderDashboardHome()

    expect(screen.getByText('赛博修仙都市')).toBeInTheDocument()
    expect(screen.queryByText('暂无精选灵感')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('赛博修仙都市'))
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard/inspirations/insp-1')
  })

  it('shows loading skeleton while featured inspirations are loading', () => {
    mockInspirationsConfig.enabled = true
    mockFeaturedState = {
      featured: [],
      isLoading: true,
      isFetching: true,
    }

    renderDashboardHome()

    expect(screen.getByTestId('featured-inspirations-loading')).toBeInTheDocument()
  })

  it('shows project quota upgrade modal when quick create hits limit', async () => {
    mockCreateProject.mockRejectedValueOnce(new ApiError(402, 'ERR_QUOTA_PROJECTS_EXCEEDED'))

    renderDashboardHome()

    fireEvent.change(screen.getByTestId('dashboard-inspiration-input'), {
      target: { value: '灵感测试' },
    })
    fireEvent.click(screen.getByTestId('create-project-button'))

    expect(await screen.findByTestId('upgrade-modal')).toBeInTheDocument()
  })
  it('keeps 「开始创作」 usable with an empty idea and says so while the project is being created', async () => {
    let resolveCreate: (value: { id: string }) => void = () => {}
    mockCreateProject.mockImplementationOnce(
      () => new Promise<{ id: string }>((resolve) => { resolveCreate = resolve }),
    )

    renderDashboardHome()

    // An empty idea still creates a blank project, so the button is a real, enabled control.
    const cta = screen.getByTestId('create-project-button')
    expect(cta).toBeEnabled()
    expect(cta).toHaveTextContent('开始创作')

    fireEvent.click(cta)

    await waitFor(() => expect(screen.getByTestId('create-project-button')).toBeDisabled())
    expect(screen.getByTestId('create-project-button')).toHaveTextContent('正在创建项目…')

    resolveCreate({ id: 'project-created' })
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created'))
  })

  it('announces which project type is selected', () => {
    renderDashboardHome()

    expect(screen.getByRole('button', { name: '长篇小说' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('greets an author by nickname, then username, before the localized default name', async () => {
    mockUser = { ...defaultMockUser, nickname: '青柠' }
    const { unmount } = renderDashboardHome()
    expect(await screen.findByRole('heading', { level: 1, name: '你好，青柠' })).toBeInTheDocument()
    unmount()

    mockUser = { ...defaultMockUser, nickname: null, username: '' }
    renderDashboardHome()
    expect(await screen.findByRole('heading', { level: 1, name: '你好，作者' })).toBeInTheDocument()
    expect(screen.queryByText(/hero\.defaultName/)).not.toBeInTheDocument()
    expect(screen.queryByText('你好，创作者')).not.toBeInTheDocument()
    expect(zhDashboard.hero.defaultName).toBe('作者')
    expect(enDashboard.hero.defaultName).toBe('writer')
  })
  it('opens on the project type picked on the landing page and keeps it after creating', async () => {
    setPreferredProjectType('screenplay', defaultMockUser.id)

    const { unmount } = renderDashboardHome()

    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalledWith(expect.any(String), undefined, 'screenplay')
      expect(mockNavigate).toHaveBeenCalledWith('/project/project-created')
    })
    expect(getPreferredProjectType(defaultMockUser.id)).toBe('screenplay')
    unmount()

    // Coming back to the dashboard: the screenwriter is not switched to 长篇小说.
    renderDashboardHome()
    fireEvent.click(screen.getByTestId('create-project-button'))
    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenLastCalledWith(expect.any(String), undefined, 'screenplay')
    })
  })

  it('without a stored preference opens on the type of the most recently active project', async () => {
    mockProjects = [
      { id: 'old-novel', name: '旧长篇', project_type: 'novel', updated_at: '2026-10-01T08:00:00Z' },
      { id: 'new-short', name: '订婚宴前夜', project_type: 'short', updated_at: '2026-10-09T08:00:00Z' },
    ]

    renderDashboardHome()
    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenLastCalledWith(expect.any(String), undefined, 'short')
    })
  })

  it('keeps the preferred type when project creation fails', async () => {
    setPreferredProjectType('short', defaultMockUser.id)
    mockCreateProject.mockRejectedValueOnce(new ApiError(500, 'boom'))

    renderDashboardHome()
    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalledWith(expect.any(String), undefined, 'short')
    })
    expect(getPreferredProjectType(defaultMockUser.id)).toBe('short')
  })

  it('falls back to novel when the stored preference is stale or invalid', async () => {
    localStorage.setItem(
      `${PREFERRED_PROJECT_TYPE_STORAGE_KEY}:${defaultMockUser.id}`,
      JSON.stringify({ type: 'screenplay', ts: Date.now() - PREFERRED_PROJECT_TYPE_TTL_MS - 1000 }),
    )
    const { unmount } = renderDashboardHome()
    fireEvent.click(screen.getByTestId('create-project-button'))
    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenLastCalledWith(expect.any(String), undefined, 'novel')
    })
    unmount()

    localStorage.setItem(`${PREFERRED_PROJECT_TYPE_STORAGE_KEY}:${defaultMockUser.id}`, JSON.stringify({ type: 'poem', ts: Date.now() }))
    renderDashboardHome()
    fireEvent.click(screen.getByTestId('create-project-button'))
    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenLastCalledWith(expect.any(String), undefined, 'novel')
    })
  })

  it('shows the AI message quota next to the page title', () => {
    renderDashboardHome()

    const heading = screen.getByRole('heading', { level: 1 })
    const header = heading.closest('div.flex.items-center.justify-between, div.flex.flex-col')
    expect(header).not.toBeNull()
    expect(within(header as HTMLElement).getByTestId('quota-badge')).toBeInTheDocument()
  })

  it('confirms project deletion in an in-app dialog instead of window.confirm', async () => {
    const nativeConfirm = vi.fn(() => true)
    vi.stubGlobal('confirm', nativeConfirm)
    mockDeleteProject.mockResolvedValue(undefined)
    mockProjects = [{ id: 'p-1', name: 'Doomed', project_type: 'novel', updated_at: '2026-04-07T00:00:00Z' }]

    renderDashboardHome()

    fireEvent.click(screen.getByTitle('projects.deleteProject'))

    const dialog = await screen.findByRole('dialog')
    expect(nativeConfirm).not.toHaveBeenCalled()
    expect(within(dialog).getByText('projects.deleteConfirm')).toBeInTheDocument()
    expect(mockDeleteProject).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'common:cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockDeleteProject).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTitle('projects.deleteProject'))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'common:delete' }))

    await waitFor(() => expect(mockDeleteProject).toHaveBeenCalledWith('p-1'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    // Opening the project card must not have been triggered by the delete click.
    expect(mockNavigate).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
  it('keeps an oversized idea editable and rejects button and Enter before creating a project', () => {
    renderDashboardHome()
    const input = screen.getByTestId('dashboard-inspiration-input')
    fireEvent.change(input, { target: { value: '文'.repeat(20001) } })
    expect(screen.getByRole('alert')).toHaveTextContent('chat:input.tooLong')
    expect(screen.getByTestId('create-project-button')).toBeDisabled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(mockCreateProject).not.toHaveBeenCalled()
    expect(input).toHaveValue('文'.repeat(20001))
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('starts a work from an imported draft, named after the file and opened on chapter 1', async () => {
    mockUploadDraft.mockResolvedValueOnce({ files: [{ id: 'chapter-1', title: '第一章', file_type: 'draft' }], total: 1, errors: [] })
    renderDashboardHome()
    const file = new File(['第一章\n雨夜'], '重生之都市复仇.txt', { type: 'text/plain' })

    fireEvent.change(screen.getByTestId('dashboard-import-input'), { target: { files: [file] } })

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created?file=chapter-1'))
    expect(mockCreateProject).toHaveBeenCalledWith('重生之都市复仇', undefined, expect.any(String))
    expect(mockUploadDraft).toHaveBeenCalledWith('project-created', file)
    // A draft is not an idea: nothing is queued for the AI.
    expect(localStorage.getItem('zenstory_inspiration_project-created')).toBeNull()
  })

  it('opens the import picker from its button and ignores a cancelled pick', () => {
    renderDashboardHome()
    const input = screen.getByTestId('dashboard-import-input') as HTMLInputElement
    const pick = vi.spyOn(input, 'click')

    fireEvent.click(screen.getByTestId('dashboard-import-drafts'))
    fireEvent.change(input, { target: { files: [] } })

    expect(pick).toHaveBeenCalledTimes(1)
    expect(mockCreateProject).not.toHaveBeenCalled()
  })

  it('opens the new work without a chapter when the upload created nothing, named by its type', async () => {
    mockUploadDraft.mockRejectedValueOnce(new Error('offline'))
    renderDashboardHome()

    fireEvent.change(screen.getByTestId('dashboard-import-input'), {
      target: { files: [new File([''], '.txt', { type: 'text/plain' })] },
    })

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created'))
    expect(mockCreateProject).toHaveBeenCalledWith('我的小说', undefined, 'novel')
  })

  it('stops when the created work has no id', async () => {
    mockCreateProject.mockResolvedValueOnce({})
    renderDashboardHome()

    fireEvent.change(screen.getByTestId('dashboard-import-input'), {
      target: { files: [new File(['正文'], 'a.txt', { type: 'text/plain' })] },
    })

    await waitFor(() => expect(mockCreateProject).toHaveBeenCalled())
    expect(mockUploadDraft).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('does not pull the author back after they left during an import', async () => {
    let finishUpload: (value: unknown) => void = () => {}
    mockUploadDraft.mockReturnValueOnce(new Promise((resolve) => { finishUpload = resolve }))
    const { unmount } = renderDashboardHome()

    fireEvent.change(screen.getByTestId('dashboard-import-input'), {
      target: { files: [new File(['正文'], 'a.txt', { type: 'text/plain' })] },
    })
    await waitFor(() => expect(mockUploadDraft).toHaveBeenCalled())
    unmount()
    finishUpload({ files: [{ id: 'chapter-1', title: '第一章', file_type: 'draft' }], total: 1 })

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('shows the project quota prompt when an import cannot create a work', async () => {
    mockCreateProject.mockRejectedValueOnce(new ApiError(402, 'ERR_QUOTA_PROJECTS_EXCEEDED'))
    renderDashboardHome()

    fireEvent.change(screen.getByTestId('dashboard-import-input'), {
      target: { files: [new File(['正文'], 'a.txt', { type: 'text/plain' })] },
    })

    expect(await screen.findByTestId('upgrade-modal')).toBeInTheDocument()
    expect(mockUploadDraft).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('numbers a default name the author already uses, so the list never shows twins', async () => {
    mockProjects = [
      { id: 'p-1', name: '我的小说', project_type: 'novel' },
      { id: 'p-2', name: '我的小说 2', project_type: 'novel' },
    ]
    renderDashboardHome()

    fireEvent.click(screen.getByTestId('create-project-button'))

    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledWith('我的小说 3', undefined, 'novel'))
    mockProjects = []
  })

  it('lets the return key add a line on phones instead of creating a work', () => {
    mockIsMobile = true
    renderDashboardHome()
    const input = screen.getByTestId('dashboard-inspiration-input')
    fireEvent.change(input, { target: { value: '主角是外卖员' } })

    fireEvent.keyDown(input, { key: 'Enter' })

    expect(mockCreateProject).not.toHaveBeenCalled()
    mockIsMobile = false
  })

  it.each([
    ['phone', true, true],
    ['desktop', false, false],
  ])('on a %s, an idea-less new work asks to open on the AI tab: %s', async (_name, isMobile, expected) => {
    mockIsMobile = isMobile
    renderDashboardHome()
    fireEvent.click(screen.getByTestId('create-project-button'))
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created'))
    expect(localStorage.getItem('zenstory_open_chat_project-created') !== null).toBe(expected)
    localStorage.removeItem('zenstory_open_chat_project-created')
    mockIsMobile = false
  })

  it('creates a project with an idea at the 20000 character boundary', async () => {
    renderDashboardHome()
    const idea = '文'.repeat(20000)
    fireEvent.change(screen.getByTestId('dashboard-inspiration-input'), { target: { value: idea } })
    fireEvent.click(screen.getByTestId('create-project-button'))
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created'))
    expect(JSON.parse(localStorage.getItem('zenstory_inspiration_project-created')!).content).toBe(idea)
  })

})

describe('first work entry', () => {
  beforeEach(() => { vi.clearAllMocks(); mockFirstWork.get.mockResolvedValue(firstWorkState()); mockFirstWork.initialize.mockReset(); mockLanguage = 'zh-CN'; mockUser = defaultMockUser; mockProjects = []; mockProjectsLoading = false; mockUseFeaturedInspirations.mockReturnValue(mockFeaturedState); vi.spyOn(subscriptionApi, 'getQuota').mockResolvedValue({ ai_conversations: { used: 0, limit: 3, reset_at: null } } as QuotaResponse); localStorage.clear() })
  it('initializes pending work with the existing type preference and opens its exact manuscript', async () => {
    setPreferredProjectType('screenplay', defaultMockUser.id)
    mockFirstWork.get.mockResolvedValue(firstWorkState('pending'))
    mockFirstWork.initialize.mockResolvedValue({ ...firstWorkState('created'), project_id: 'first-project', file_id: 'first-script', available: true, static_guidance: true })
    renderDashboardHome()
    const open = await screen.findByRole('button', { name: '打开作品' })
    expect(mockFirstWork.initialize).toHaveBeenCalledTimes(1)
    expect(mockFirstWork.initialize).toHaveBeenCalledWith('screenplay', 'zh-CN')
    expect(mockCreateProject).not.toHaveBeenCalled()
    fireEvent.click(open)
    expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-script')
  })

  it('leaves the type to the server (onboarding persona) when this device has no type preference', async () => {
    mockFirstWork.get.mockResolvedValue(firstWorkState('pending'))
    mockFirstWork.initialize.mockResolvedValue({ ...firstWorkState('created'), project_id: 'first-project', project_type: 'screenplay', file_id: 'first-script', available: true, static_guidance: true })
    renderDashboardHome()
    await screen.findByRole('button', { name: '打开作品' })
    expect(mockFirstWork.initialize).toHaveBeenCalledWith(null, 'zh-CN')
    // The tab follows the type the server chose, so 「开始创作」 opens that work.
    await waitFor(() => expect(screen.getByRole('button', { name: 'projectType.screenplay.name' })).toHaveAttribute('aria-pressed', 'true'))
    fireEvent.click(screen.getByTestId('create-project-button'))
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-script'))
    expect(mockCreateProject).not.toHaveBeenCalled()
  })

  it('does not recreate deleted first work or initialize an existing author', async () => {
    mockFirstWork.get.mockResolvedValue({ ...firstWorkState('created'), project_id: 'gone', file_id: 'gone-file' })
    renderDashboardHome()
    await waitFor(() => expect(mockFirstWork.get).toHaveBeenCalled())
    expect(mockFirstWork.initialize).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '打开作品' })).not.toBeInTheDocument()
  })

  it('reports initialization failure and retries only when requested, preserving the idea path', async () => {
    mockFirstWork.get.mockResolvedValue(firstWorkState('pending'))
    mockFirstWork.initialize.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ...firstWorkState('created'), project_id: 'first-project', file_id: 'first-file', available: true, static_guidance: true })
    renderDashboardHome()
    const retry = await screen.findByRole('button', { name: '重试' })
    expect(mockFirstWork.initialize).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('dashboard-inspiration-input')).toBeEnabled()
    fireEvent.click(retry)
    await screen.findByRole('button', { name: '打开作品' })
    expect(mockFirstWork.initialize).toHaveBeenCalledTimes(2)
  })

  describe('「开始创作」 while the first work is still untouched', () => {
    const fresh = { ...firstWorkState('created'), project_id: 'first-project', project_type: 'novel', file_id: 'first-file', available: true, static_guidance: true }
    beforeEach(() => {
      mockIsMobile = false
      mockCreateProject.mockResolvedValue({ id: 'project-created' })
      mockFirstWork.get.mockResolvedValue(fresh)
      mockProjects = [{ id: 'first-project', name: '我的小说', project_type: 'novel' }]
      setPreferredProjectType('novel', defaultMockUser.id)
    })
    const start = async (idea = '') => {
      renderDashboardHome()
      await screen.findByRole('button', { name: '打开作品' })
      if (idea) fireEvent.change(screen.getByTestId('dashboard-inspiration-input'), { target: { value: idea } })
      fireEvent.click(screen.getByTestId('create-project-button'))
    }

    it('opens the first work instead of creating a second empty one', async () => {
      await start()
      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-file'))
      expect(mockCreateProject).not.toHaveBeenCalled()
      expect(localStorage.getItem('zenstory_inspiration_first-project')).toBeNull()
    })

    it('hands a typed idea to the first work, where it is sent as the first message', async () => {
      await start('灯塔守夜人捡到一封寄给未来的信')
      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-file'))
      expect(mockCreateProject).not.toHaveBeenCalled()
      expect(JSON.parse(localStorage.getItem('zenstory_inspiration_first-project')!).content).toBe('灯塔守夜人捡到一封寄给未来的信')
      expect(screen.getByTestId('dashboard-inspiration-input')).toHaveValue('')
    })

    it('opens it on the AI tab on phones when there is no idea, like any new work', async () => {
      mockIsMobile = true
      await start()
      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-file'))
      expect(localStorage.getItem('zenstory_open_chat_first-project')).not.toBeNull()
    })

    it('opens it even before the project list has refreshed after initialization', async () => {
      mockProjects = []
      await start('灯塔守夜人捡到一封寄给未来的信')
      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-file'))
      expect(mockCreateProject).not.toHaveBeenCalled()
    })

    it('creates a new work with the idea once the first work is deleted from the dashboard', async () => {
      mockDeleteProject.mockImplementation(async () => {
        mockProjects = []
        mockFirstWork.get.mockResolvedValue({ ...fresh, available: false, project_type: null })
      })
      renderDashboardHome()
      await screen.findByRole('button', { name: '打开作品' })
      fireEvent.click(screen.getByTitle('projects.deleteProject'))
      fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'common:delete' }))
      await waitFor(() => expect(mockDeleteProject).toHaveBeenCalledWith('first-project'))
      await waitFor(() => expect(screen.queryByRole('button', { name: '打开作品' })).not.toBeInTheDocument())

      fireEvent.change(screen.getByTestId('dashboard-inspiration-input'), { target: { value: '灯塔守夜人' } })
      fireEvent.click(screen.getByTestId('create-project-button'))
      await waitFor(() => expect(mockCreateProject).toHaveBeenCalled())
      expect(mockNavigate).not.toHaveBeenCalledWith(expect.stringContaining('/project/first-project'))
      expect(localStorage.getItem('zenstory_inspiration_first-project')).toBeNull()
    })

    it('falls back to the project list when the server does not report the type', async () => {
      mockFirstWork.get.mockResolvedValue({ ...fresh, project_type: undefined })
      await start()
      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-file'))
      expect(mockCreateProject).not.toHaveBeenCalled()
    })

    it('promises the idea goes into the first work only while 「开始创作」 opens it', async () => {
      renderDashboardHome()
      await screen.findByRole('button', { name: '打开作品' })
      const entry = screen.getByTestId('first-work-entry')
      expect(entry).toHaveTextContent('firstWork.description')
      expect(entry).not.toHaveTextContent('firstWork.descriptionOpen')
      fireEvent.click(screen.getByRole('button', { name: 'projectType.short.name' }))
      expect(screen.getByTestId('first-work-entry')).toHaveTextContent('firstWork.descriptionOpen')
    })

    it('keeps the idea and explains when it cannot be stored, without opening the first work', async () => {
      const toastError = vi.spyOn(toast, 'error').mockImplementation(() => '')
      const setItem = vi.mocked(localStorage.setItem).getMockImplementation()!
      vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
        if (key.startsWith('zenstory_inspiration_')) throw new DOMException('full', 'QuotaExceededError')
        setItem(key, value)
      })
      try {
        await start('灯塔守夜人捡到一封寄给未来的信')
        await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1))
        expect(mockNavigate).not.toHaveBeenCalled()
        expect(mockCreateProject).not.toHaveBeenCalled()
        expect(screen.getByTestId('dashboard-inspiration-input')).toHaveValue('灯塔守夜人捡到一封寄给未来的信')
      } finally {
        vi.mocked(localStorage.setItem).mockImplementation(setItem)
        toastError.mockRestore()
      }
    })

    it('shows the quota prompt first when today\'s AI messages are used up, keeping the idea', async () => {
      vi.mocked(subscriptionApi.getQuota).mockResolvedValue({ ai_conversations: { used: 3, limit: 3, reset_at: '2026-10-31T16:00:00Z' } } as QuotaResponse)
      renderDashboardHome()
      await screen.findByRole('button', { name: '打开作品' })
      await waitFor(() => expect(subscriptionApi.getQuota).toHaveBeenCalled())
      await act(async () => { await Promise.resolve() })
      fireEvent.change(screen.getByTestId('dashboard-inspiration-input'), { target: { value: '灯塔守夜人' } })
      fireEvent.click(screen.getByTestId('create-project-button'))
      expect(await screen.findByTestId('upgrade-modal')).toBeInTheDocument()
      expect(mockNavigate).not.toHaveBeenCalled()
      expect(localStorage.getItem('zenstory_inspiration_first-project')).toBeNull()
      expect(screen.getByTestId('dashboard-inspiration-input')).toHaveValue('灯塔守夜人')
    })

    it('does not ask phones for the AI tab when an idea is sent, since the chat opens to send it', async () => {
      mockIsMobile = true
      await start('灯塔守夜人')
      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/first-project?file=first-file'))
      expect(localStorage.getItem('zenstory_open_chat_first-project')).toBeNull()
      expect(localStorage.getItem('zenstory_inspiration_first-project')).not.toBeNull()
    })

    it('still creates a new work for another type, after writing has started, or without an untouched first work', async () => {
      for (const [state, type] of [
        [fresh, 'screenplay'],
        [{ ...fresh, first_ai_requested_at: '2026-10-31T01:00:00Z', static_guidance: false }, 'novel'],
        [{ ...fresh, first_manual_write_at: '2026-10-31T01:00:00Z' }, 'novel'],
        [{ ...fresh, available: false }, 'novel'],
        [firstWorkState(), 'novel'],
      ] as const) {
        mockCreateProject.mockClear(); mockNavigate.mockClear()
        mockFirstWork.get.mockResolvedValue(state)
        setPreferredProjectType(type, defaultMockUser.id)
        const view = renderDashboardHome()
        await waitFor(() => expect(mockFirstWork.get).toHaveBeenCalled())
        await act(async () => { await Promise.resolve() })
        fireEvent.click(screen.getByTestId('create-project-button'))
        await waitFor(() => expect(mockCreateProject).toHaveBeenCalledTimes(1))
        expect(mockCreateProject.mock.calls[0][2]).toBe(type)
        await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/project/project-created'))
        view.unmount()
      }
    })
  })
})
