import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

let mockIsMobile = false;
let mockIsTablet = false;
let mockIsDesktop = true;
const mockTheme: 'dark' | 'light' = 'light';
const mockLanguage = 'zh-CN';
const mockResolvedLanguage: string | undefined = 'zh-CN';
let mockHasPersonaOnboarding = true;
let mockProjects: Array<{ id: string; name: string; project_type: 'novel'; updated_at?: string | null }> = [];
let mockProjectsLoading = false;

const mockChangeLanguage = vi.fn();
const mockSetTheme = vi.fn();
const mockLogout = vi.fn();

const { mockDashboardOnboardingFlags } = vi.hoisted(() => ({
  mockDashboardOnboardingFlags: {
    todayActionPlanEnabled: false,
    firstDayActivationGuideEnabled: false,
    coachmarkTourEnabled: true,
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => {
      const translations: Record<string, string> = {
        'dashboard:userPanel.quickSettings': 'Quick settings',
        'dashboard:userPanel.openPanel': 'Open user settings panel',
        'dashboard:userPanel.adminPanel': 'Admin panel',
        'dashboard:userPanel.replayTour': 'Replay guide',
        'common:nav.openMenu': '打开菜单',
        'common:nav.closeMenu': '关闭菜单',
        'common:nav.mobileMenu': '移动端导航菜单',
        'common:a11y.switchToChinese': '切换到中文',
        'common:a11y.switchToEnglish': '切换到英文',
        'common:a11y.toggleTheme': '切换深色或浅色模式',
        'common:a11y.toggleLanguage': '切换界面语言',
        'settings:theme.mode': 'Theme',
        'settings:theme.dark': 'Dark mode',
        'settings:theme.light': 'Light mode',
        'settings:language.label': 'Language',
        'nav.logout': 'Logout',
        'nav.home': 'Home',
        'nav.projects': 'Projects',
        'nav.materials': 'Materials',
        'nav.inspirations': 'Inspirations',
        'nav.skills': 'Skills',
        'nav.billing': 'Billing',
        'dashboardTour.common.skip': '跳过引导',
        'dashboardTour.common.next': '下一步',
        'dashboardTour.common.finish': '开始使用',
      };
      return translations[key] || options?.defaultValue || key;
    },
    i18n: {
      language: mockLanguage,
      resolvedLanguage: mockResolvedLanguage,
      changeLanguage: mockChangeLanguage,
    },
  }),
}));

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => mockIsMobile,
  useIsTablet: () => mockIsTablet,
  useIsDesktop: () => mockIsDesktop,
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'tour-user-1',
      username: 'test-user',
      email: 'test@example.com',
      avatar_url: null,
      is_superuser: false,
      // Keep the account "new" (within the 7-day window) relative to the
      // current date so the tour stays eligible regardless of when the test runs.
      created_at: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
    },
    logout: mockLogout,
  }),
}));

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: mockProjects,
    loading: mockProjectsLoading,
  }),
}));

vi.mock('../../contexts/ThemeContext', () => ({
  useTheme: () => ({
    theme: mockTheme,
    setTheme: mockSetTheme,
  }),
}));

vi.mock('../../components/SettingsDialog', () => ({
  SettingsDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="settings-dialog">Settings Dialog</div> : null,
}));

vi.mock('../../components/UserMenu', () => ({
  UserAvatar: ({ username }: { username: string }) => <span>{username}</span>,
}));

vi.mock('../../config/dashboardOnboarding', () => ({
  dashboardOnboardingFlags: mockDashboardOnboardingFlags,
}));

vi.mock('../../config/inspirations', () => ({
  inspirationsConfig: { enabled: true },
}));

vi.mock('../../lib/onboardingPersona', () => ({
  getPersonaOnboardingData: () =>
    mockHasPersonaOnboarding
      ? {
          version: 1,
          completed_at: '2026-04-06T00:00:00Z',
          selected_personas: ['explorer'],
          selected_goals: ['finishBook'],
          experience_level: 'beginner',
          skipped: false,
        }
      : null,
}));

import Dashboard from '../Dashboard';
import { DASHBOARD_FIRST_RUN_TOUR } from '../../config/productTours/dashboardFirstRun';

const enabledTourSteps = DASHBOARD_FIRST_RUN_TOUR.steps;

const renderDashboard = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/dashboard']}>
        <Routes>
          <Route path="/dashboard" element={<Dashboard />}>
            <Route
              index
              element={(
                <div>
                  <div data-tour-id="dashboard-project-type-tabs">
                    <button type="button">长篇小说</button>
                    <button type="button">短篇小说</button>
                    <button type="button">短剧剧本</button>
                  </div>
                  <textarea data-tour-id="dashboard-inspiration-input" />
                  <button type="button" data-tour-id="dashboard-create-project">开始创作</button>
                  <div data-tour-id="dashboard-inspirations-section">
                    <div data-tour-id="dashboard-inspirations-entry">
                      <div data-tour-id="dashboard-inspirations-heading">精选灵感</div>
                      <button type="button" data-tour-id="dashboard-inspirations-link">查看全部</button>
                    </div>
                  </div>
                </div>
              )}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Dashboard coachmark tour', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mockHasPersonaOnboarding = true;
    mockIsMobile = false;
    mockIsTablet = false;
    mockIsDesktop = true;
    mockProjects = [];
    mockProjectsLoading = false;
    mockDashboardOnboardingFlags.coachmarkTourEnabled = true;
    DASHBOARD_FIRST_RUN_TOUR.steps = enabledTourSteps;
  });

  it('auto-starts for eligible users and advances through the configured steps', async () => {
    renderDashboard();

    expect(await screen.findByText('先选你要写什么')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));

    expect(await screen.findByText('从一个想法开始')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(await screen.findByText('没想法就先来这里')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(await screen.findByText('点这里就开始')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开始创作' }));

    await waitFor(() => {
      expect(screen.queryByText('点这里就开始')).not.toBeInTheDocument();
    });
  });

  it('goes directly to create-project when the user already entered an idea', async () => {
    renderDashboard();

    expect(await screen.findByText('先选你要写什么')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));

    const inputStep = await screen.findByText('从一个想法开始');
    expect(inputStep).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '一个关于复仇的短剧开场' } });
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(await screen.findByText('点这里就开始')).toBeInTheDocument();
    expect(screen.queryByText('没想法就先来这里')).not.toBeInTheDocument();
  });

  it('continues to project creation when the library step is disabled and the idea is empty', async () => {
    DASHBOARD_FIRST_RUN_TOUR.steps = enabledTourSteps.filter((step) => step.id !== 'inspirations_link');
    renderDashboard();
    expect(await screen.findByText('先选你要写什么')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '知道了' }));
    expect(await screen.findByText('从一个想法开始')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    expect(await screen.findByText('点这里就开始')).toBeInTheDocument();
    expect(screen.queryByText('没想法就先来这里')).not.toBeInTheDocument();
  });

  it('does not auto-start when persona onboarding data is missing', async () => {
    mockHasPersonaOnboarding = false;
    renderDashboard();

    await waitFor(() => {
      expect(screen.queryByText('先选你要写什么')).not.toBeInTheDocument();
    });
  });

  it('does not auto-start when the user already has projects', async () => {
    mockProjects = [{ id: 'project-1', name: '已有项目', project_type: 'novel' }];
    renderDashboard();

    await waitFor(() => {
      expect(screen.queryByText('先选你要写什么')).not.toBeInTheDocument();
    });
  });

  it('can replay the guide from the user panel after skipping', async () => {
    renderDashboard();

    expect(await screen.findByText('先选你要写什么')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '跳过引导' }));

    await waitFor(() => {
      expect(screen.queryByText('先选你要写什么')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Open user settings panel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replay guide' }));

    expect(await screen.findByText('先选你要写什么')).toBeInTheDocument();
  });

  it('uses bottom-sheet style card on mobile', async () => {
    mockIsMobile = true;
    mockIsDesktop = false;
    renderDashboard();

    fireEvent.click(screen.getByRole('button', { name: '打开菜单' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replay guide' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog.className).toContain('w-[calc(100vw-2rem)]');
  });
});
