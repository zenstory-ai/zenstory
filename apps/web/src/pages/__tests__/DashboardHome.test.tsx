import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
let mockProjects: Array<{ id: string; name: string; description?: string; project_type: 'novel'; updated_at?: string | null }> = []
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
let mockActivationGuide: {
  user_id: string;
  window_hours: number;
  within_first_day: boolean;
  total_steps: number;
  completed_steps: number;
  completion_rate: number;
  is_activated: boolean;
  next_event_name: string | null;
  next_action: string | null;
  steps: Array<{
    event_name: string;
    label: string;
    completed: boolean;
    completed_at: string | null;
    action_path: string;
  }>;
} | null = null
let mockPersonaRecommendations: Array<{
  id: string;
  title: string;
  description: string;
  action: string;
}> = []
let mockDashboardInspirations: Array<{
  id: string;
  title: string;
  hook: string;
  tags: string[];
  source: string;
}> = []

const {
  mockDashboardOnboardingFlags,
  mockInspirationsConfig,
  mockUseFeaturedInspirations,
  mockUseDashboardInspirations,
  mockGetActivationGuide,
  mockGetRecommendations,
} = vi.hoisted(() => ({
  mockDashboardOnboardingFlags: {
    todayActionPlanEnabled: true,
    firstDayActivationGuideEnabled: true,
  },
  mockInspirationsConfig: {
    enabled: false,
  },
  mockUseFeaturedInspirations: vi.fn(),
  mockUseDashboardInspirations: vi.fn(),
  mockGetActivationGuide: vi.fn(),
  mockGetRecommendations: vi.fn(),
}))

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
    'projects.empty': '还没有任何项目',
    'projects.emptyHint': '在上方输入灵感，点击「开始创作」创建你的第一个项目',
    'common.createButton': '开始创作',
    'projectType.novel.name': '长篇小说',
    'inspiration.novelDesc': 'AI 将根据你的灵感，帮你构思故事框架、设定世界观和人物角色',
    'inspiration.novelPlaceholder': '请输入灵感',
    'dashboard:inspiration.dashboardPlaceholder': '想到什么写什么：一个人物、一个画面、一句台词都行，也可以从下方灵感里挑一个。没想好就直接点「开始创作」。',
    'dashboard:inspiration.dashboardPlaceholderWithoutInspirations': '想到什么写什么：一个人物、一个画面、一句台词都行。没想好也没关系，直接点「开始创作」，和 AI 边聊边想。',
    'activationGuide.steps.signup_success': '完成注册',
    'activationGuide.steps.project_created': '创建项目',
    'activationGuide.steps.first_file_saved': '保存第一个文件',
    'activationGuide.steps.first_ai_action_accepted': '采纳一次 AI 修改',
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

vi.mock('../../lib/api', () => ({
  projectApi: {
    getTemplates: vi.fn().mockResolvedValue(null),
  },
}))

vi.mock('../../lib/writingStatsApi', () => ({
  writingStatsApi: {
    getActivationGuide: mockGetActivationGuide,
  },
}))

vi.mock('../../lib/onboardingPersonaApi', () => ({
  onboardingPersonaApi: {
    getRecommendations: mockGetRecommendations,
  },
}))

vi.mock('../../config/dashboardOnboarding', () => ({
  dashboardOnboardingFlags: mockDashboardOnboardingFlags,
}))

vi.mock('../../config/inspirations', () => ({
  inspirationsConfig: mockInspirationsConfig,
}))

import DashboardHome from '../DashboardHome'
import { projectApi } from '../../lib/api'

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
    mockActivationGuide = null
    mockPersonaRecommendations = []
    mockDashboardOnboardingFlags.todayActionPlanEnabled = true
    mockDashboardOnboardingFlags.firstDayActivationGuideEnabled = true
    mockDashboardInspirations = []
    mockGetActivationGuide.mockImplementation(() => Promise.resolve(mockActivationGuide))
    mockGetRecommendations.mockImplementation(() => Promise.resolve(mockPersonaRecommendations))
    vi.mocked(projectApi.getTemplates).mockResolvedValue(null)
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
      const card = await screen.findByRole('button', { name: `Open project ${project.name}` })
      const content = card.querySelector(':scope > .relative')
      const footer = content?.lastElementChild

      expect(card).toHaveClass('flex', 'flex-col')
      expect(content).toHaveClass('flex', 'flex-1', 'flex-col')
      expect(footer).toHaveClass('mt-auto', 'flex', 'items-center', 'justify-between')
      expect(footer?.children).toHaveLength(2)
      expect(footer?.firstElementChild).toHaveTextContent('长篇小说')
      expect(footer?.lastElementChild).not.toBeEmptyDOMElement()
    }
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
    expect(screen.getByTestId('dashboard-inspiration-input')).toHaveAttribute(
      'placeholder',
      '想到什么写什么：一个人物、一个画面、一句台词都行。没想好也没关系，直接点「开始创作」，和 AI 边聊边想。',
    )
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
    expect(screen.getByTestId('dashboard-inspiration-input')).toHaveAttribute(
      'placeholder',
      '想到什么写什么：一个人物、一个画面、一句台词都行，也可以从下方灵感里挑一个。没想好就直接点「开始创作」。',
    )
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

  it('shows activation guide card for first-day users and next action is actionable', async () => {
    mockActivationGuide = {
      user_id: 'u-1',
      window_hours: 24,
      within_first_day: true,
      total_steps: 4,
      completed_steps: 2,
      completion_rate: 0.5,
      is_activated: false,
      next_event_name: 'first_file_saved',
      next_action: '/dashboard',
      steps: [
        {
          event_name: 'signup_success',
          label: 'Signup Success',
          completed: true,
          completed_at: '2026-03-08T00:00:00Z',
          action_path: '/dashboard',
        },
        {
          event_name: 'project_created',
          label: 'Project Created',
          completed: true,
          completed_at: '2026-03-08T00:01:00Z',
          action_path: '/dashboard',
        },
        {
          event_name: 'first_file_saved',
          label: 'First File Saved',
          completed: false,
          completed_at: null,
          action_path: '/dashboard',
        },
        {
          event_name: 'first_ai_action_accepted',
          label: 'First AI Action Accepted',
          completed: false,
          completed_at: null,
          action_path: '/dashboard',
        },
      ],
    }

    mockProjects = [
      { id: 'project-1', name: '最近项目', project_type: 'novel', updated_at: '2026-03-08T00:02:00Z' },
    ]

    renderDashboardHome()

    const guideCard = await screen.findByTestId('activation-guide-card')
    const guide = within(guideCard)
    expect(guideCard).toBeInTheDocument()
    expect(guide.getByText('新手上手清单')).toBeInTheDocument()

    // Language adaptation: show localized step labels instead of raw backend English.
    expect(guide.getByText('完成注册')).toBeInTheDocument()
    expect(guide.getByText('创建项目')).toBeInTheDocument()
    expect(guide.getByText('保存第一个文件')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '继续下一步' }))
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')
  })

  it('shows today action plan entry and executes activation action', async () => {
    // Preference A: if activation guide is shown, today-action-plan is hidden.
    // Use a non-first-day (or no activation guide) scenario to validate the action plan.
    mockActivationGuide = null
    mockProjects = []
    mockPersonaRecommendations = []

    renderDashboardHome()

    const todayActionCard = await screen.findByTestId('today-action-plan-card')
    const todayAction = within(todayActionCard)
    expect(todayActionCard).toBeInTheDocument()
    expect(await todayAction.findByText('创建第一个项目')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('today-action-execute-1'))

    await waitFor(() => {
      expect(mockCreateProject).toHaveBeenCalled()
      expect(mockNavigate).toHaveBeenCalledWith('/project/project-created')
    })
  })

  it('does not flash today action plan while projects are still loading', async () => {
    mockActivationGuide = null
    mockPersonaRecommendations = []
    mockProjects = []
    mockProjectsLoading = true

    renderDashboardHome()

    await waitFor(() => {
      expect(screen.queryByTestId('today-action-plan-card')).not.toBeInTheDocument()
    })
  })

  it('hides today action plan for returning users who already have projects', () => {
    mockActivationGuide = null
    mockPersonaRecommendations = []
    mockProjectsLoading = false
    mockProjects = [
      { id: 'project-1', name: '已有项目', project_type: 'novel', updated_at: '2026-03-08T00:02:00Z' },
    ]

    renderDashboardHome()

    expect(screen.queryByTestId('today-action-plan-card')).not.toBeInTheDocument()
  })

  it('keeps onboarding panels hidden when dashboard onboarding flags are off', async () => {
    mockDashboardOnboardingFlags.todayActionPlanEnabled = false
    mockDashboardOnboardingFlags.firstDayActivationGuideEnabled = false
    mockActivationGuide = {
      user_id: 'u-1',
      window_hours: 24,
      within_first_day: true,
      total_steps: 4,
      completed_steps: 1,
      completion_rate: 0.25,
      is_activated: false,
      next_event_name: 'project_created',
      next_action: '/dashboard',
      steps: [
        {
          event_name: 'signup_success',
          label: 'Signup Success',
          completed: true,
          completed_at: '2026-03-08T00:00:00Z',
          action_path: '/dashboard',
        },
        {
          event_name: 'project_created',
          label: 'Project Created',
          completed: false,
          completed_at: null,
          action_path: '/dashboard',
        },
      ],
    }
    mockPersonaRecommendations = [
      {
        id: 'level_beginner_path',
        title: '新手先看灵感',
        description: '先熟悉灵感广场，再开始创作。',
        action: '/dashboard',
      },
    ]

    renderDashboardHome()

    await waitFor(() => {
      expect(screen.queryByTestId('activation-guide-card')).not.toBeInTheDocument()
      expect(screen.queryByTestId('today-action-plan-card')).not.toBeInTheDocument()
    })
    expect(mockGetActivationGuide).not.toHaveBeenCalled()
    expect(mockGetRecommendations).not.toHaveBeenCalled()
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
})
