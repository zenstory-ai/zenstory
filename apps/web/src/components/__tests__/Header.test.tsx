import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as React from 'react'

// Mock dependencies BEFORE importing Header
const mockNavigate = vi.fn()
const mockExportDrafts = vi.fn()
const mockTriggerFileTreeRefresh = vi.fn()
const mockSetSelectedItem = vi.fn()
const { mockToastError, mockToastSuccess, mockToastInfo, mockHandleApiError, mockLoggerError } = vi.hoisted(() => ({
  mockToastError: vi.fn(),
  mockToastSuccess: vi.fn(),
  mockToastInfo: vi.fn(),
  mockHandleApiError: vi.fn((error: unknown) =>
    error instanceof Error ? error.message : 'Unknown error'
  ),
  mockLoggerError: vi.fn(),
}))

// Create mutable mock values
let mockCurrentProjectId: string | null = null
let mockIsMobile = false
let mockSubscriptionStatus = {
  tier: 'free',
  status: 'none',
  display_name: '免费版',
  display_name_en: 'Free',
  current_period_end: null,
  days_remaining: null,
  features: {},
}

// Mock react-router-dom
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}))

// Mock react-i18next
// The Header component calls t() with bare keys (no inline fallback string),
// so resolve the keys the tests assert on to their real translation values
// (see public/locales), matching the map convention used by sibling tests
// like PublicHeader.test.tsx and BottomTabs.test.tsx.
const headerTranslations: Record<string, string> = {
  'editor:header.projectDashboard': '项目统计',
  'editor:header.helpDocs': '帮助文档',
  'common:feedback.entry': '问题反馈',
  'editor:header.exportWithOutline': '导出大纲和正文（TXT）',
  'dashboard:billing.ctaUpgradePro': '升级专业版',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (
      key: string,
      fallbackOrOptions?: string | Record<string, unknown>,
      maybeOptions?: Record<string, unknown>
    ) => {
      const options = (
        typeof fallbackOrOptions === 'object' && fallbackOrOptions !== null
          ? fallbackOrOptions
          : maybeOptions
      ) ?? {}
      const fallback =
        typeof fallbackOrOptions === 'string'
          ? fallbackOrOptions
          : typeof options.defaultValue === 'string'
            ? options.defaultValue
            : headerTranslations[key] ?? key

      return Object.keys(options).reduce((result, optionKey) => {
        return result.replace(new RegExp(`{{\\s*${optionKey}\\s*}}`, 'g'), String(options[optionKey]))
      }, fallback)
    },
  }),
}))

// Mock react-query
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: mockSubscriptionStatus,
    isLoading: false,
  }),
}))

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  History: () => React.createElement('svg', { 'data-testid': 'history-icon' }, 'History'),
  Settings: () => React.createElement('svg', { 'data-testid': 'settings-icon' }, 'Settings'),
  MoreHorizontal: () => React.createElement('svg', { 'data-testid': 'more-actions-icon' }, 'MoreHorizontal'),
  Menu: () => React.createElement('svg', { 'data-testid': 'menu-icon' }, 'Menu'),
  X: () => React.createElement('svg', { 'data-testid': 'x-icon' }, 'X'),
  Download: () => React.createElement('svg', { 'data-testid': 'download-icon' }, 'Download'),
  CreditCard: () => React.createElement('svg', { 'data-testid': 'subscription-icon' }, 'CreditCard'),
  BarChart3: () => React.createElement('svg', { 'data-testid': 'analytics-icon' }, 'Analytics'),
  BookOpen: () => React.createElement('svg', { 'data-testid': 'help-docs-icon' }, 'HelpDocs'),
  Bug: () => React.createElement('svg', { 'data-testid': 'feedback-icon' }, 'Bug'),
  FileText: () => React.createElement('svg', { 'data-testid': 'file-text-icon' }, 'FileText'),
  Sparkles: () => React.createElement('svg', { 'data-testid': 'sparkles-icon' }, 'Sparkles'),
}))

// Mock Logo component
vi.mock('../Logo', () => ({
  Logo: ({ className }: { className?: string }) =>
    React.createElement('div', { 'data-testid': 'logo', className }, 'Logo'),
  LogoMark: ({ className }: { className?: string }) =>
    React.createElement('div', { 'data-testid': 'logo-mark', className }, 'LogoMark'),
}))

// Mock ProjectSwitcher
vi.mock('../ProjectSwitcher', () => ({
  ProjectSwitcher: () =>
    React.createElement('div', { 'data-testid': 'project-switcher' }, 'ProjectSwitcher'),
}))

// Mock VersionHistoryPanel
vi.mock('../VersionHistoryPanel', () => ({
  VersionHistoryPanel: ({
    projectId,
    onClose,
    onRollback,
  }: {
    projectId: string
    onClose: () => void
    onRollback: () => void
    onCompare: (id1: string, id2: string) => void
  }) =>
    React.createElement(
      'div',
      { 'data-testid': 'version-history-panel' },
      React.createElement('span', null, `Project: ${projectId}`),
      React.createElement('button', { onClick: onRollback }, 'Trigger Rollback'),
      React.createElement('button', { onClick: onClose }, 'Close')
    ),
}))

// Mock SettingsDialog
vi.mock('../SettingsDialog', () => ({
  SettingsDialog: ({
    isOpen,
    onClose,
  }: {
    isOpen: boolean
    onClose: () => void
  }) =>
    isOpen
      ? React.createElement(
          'div',
          { 'data-testid': 'settings-dialog' },
          React.createElement('button', { onClick: onClose }, 'Close Settings')
        )
      : null,
}))

// Mock UserMenu
vi.mock('../UserMenu', () => ({
  UserMenu: () =>
    React.createElement('div', { 'data-testid': 'user-menu' }, 'UserMenu'),
  UserMenuMobile: ({
    onLogout,
  }: {
    onLogout: () => void
  }) =>
    React.createElement(
      'div',
      { 'data-testid': 'user-menu-mobile' },
      React.createElement('button', { onClick: onLogout }, 'Logout')
    ),
}))

vi.mock('../feedback/FeedbackDialog', () => ({
  FeedbackDialog: ({ open }: { open: boolean }) =>
    open ? React.createElement('div', { 'data-testid': 'feedback-dialog' }, 'FeedbackDialog') : null,
}))

// Mock ProjectContext
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: mockCurrentProjectId,
    triggerFileTreeRefresh: mockTriggerFileTreeRefresh,
    setSelectedItem: mockSetSelectedItem,
  }),
}))

// Mock useMediaQuery hook
vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: vi.fn(() => mockIsMobile),
}))

// Mock useExport hook
vi.mock('../../hooks/useExport', () => ({
  useExport: vi.fn(() => ({ exportDrafts: mockExportDrafts })),
}))

// Mock toast
vi.mock('../../lib/toast', () => ({
  toast: {
    error: mockToastError,
    success: mockToastSuccess,
    info: mockToastInfo,
  },
}))

// Mock error handler
vi.mock('../../lib/errorHandler', () => ({
  handleApiError: (error: unknown) => mockHandleApiError(error),
  toUserErrorMessage: (message: string) => message,
}))

vi.mock('../../lib/logger', () => ({
  logger: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: (...args: unknown[]) => mockLoggerError(...args),
    log: vi.fn(),
  },
  default: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: (...args: unknown[]) => mockLoggerError(...args),
    log: vi.fn(),
  },
}))

// Import Header after mocks are set up
import { Header } from '../Header'
import { ApiError } from '../../lib/apiClient'

describe('Header', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCurrentProjectId = null
    mockIsMobile = false
    mockSubscriptionStatus = {
      tier: 'free',
      status: 'none',
      display_name: '免费版',
      display_name_en: 'Free',
      current_period_end: null,
      days_remaining: null,
      features: {},
    }
  })

  afterEach(() => {
    mockCurrentProjectId = null
    mockIsMobile = false
  })

  describe('Basic Rendering', () => {
    it('renders header element', () => {
      render(<Header />)

      const header = screen.getByRole('banner')
      expect(header).toBeInTheDocument()
      expect(header.tagName).toBe('HEADER')
    })

    it('renders project switcher', () => {
      render(<Header />)

      expect(screen.getByTestId('project-switcher')).toBeInTheDocument()
    })

    it('renders user menu', () => {
      render(<Header />)

      expect(screen.getByTestId('user-menu')).toBeInTheDocument()
    })

    it('renders settings button', () => {
      render(<Header />)

      expect(screen.getByTestId('settings-button')).toBeInTheDocument()
    })

    it('renders subscription entry button', () => {
      render(<Header />)

      expect(screen.getByTestId('header-subscription-entry')).toBeInTheDocument()
    })
  })

  describe('Logo Rendering', () => {
    it('renders both logo and logo mark (CSS controls visibility)', () => {
      render(<Header />)

      // Both are in the DOM, CSS classes control visibility
      expect(screen.getByTestId('logo')).toBeInTheDocument()
      expect(screen.getByTestId('logo-mark')).toBeInTheDocument()
    })

    it('logo mark only appears on ultra narrow screens', () => {
      render(<Header />)

      const logoMark = screen.getByTestId('logo-mark')
      expect(logoMark.parentElement).toHaveClass('hidden')
      expect(logoMark.parentElement).toHaveClass('max-[380px]:block')
    })

    it('full logo is shown by default and hidden only on ultra narrow screens', () => {
      render(<Header />)

      const logo = screen.getByTestId('logo')
      expect(logo.parentElement).toHaveClass('max-[380px]:hidden')
    })

    it('navigates to dashboard when logo area is clicked', () => {
      render(<Header />)

      const logoButton = screen.getByTestId('header-logo-button')
      fireEvent.click(logoButton)

      expect(mockNavigate).toHaveBeenCalledWith('/dashboard')
    })

    it('uses desktop zero horizontal padding to align logo with dashboard brand position', () => {
      render(<Header />)

      const logoButton = screen.getByTestId('header-logo-button')
      expect(logoButton).toHaveClass('md:px-0')
    })
  })

  describe('Project Actions', () => {
    it('does not show project dashboard action in more menu when no project is selected', () => {
      mockCurrentProjectId = null
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      expect(screen.queryByText('项目统计')).not.toBeInTheDocument()
    })

    it('shows project dashboard action in more menu when project is selected', () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      expect(screen.getByText('项目统计')).toBeInTheDocument()
    })

    it('navigates to project dashboard when project dashboard action is clicked', () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      fireEvent.click(screen.getByText('项目统计'))

      expect(mockNavigate).toHaveBeenCalledWith('/project/project-123/dashboard')
    })

    it('navigates to docs when help docs action is clicked', () => {
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      fireEvent.click(screen.getByText('帮助文档'))

      expect(mockNavigate).toHaveBeenCalledWith('/docs')
    })

    it('opens feedback dialog from more menu', async () => {
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      fireEvent.click(screen.getByText('问题反馈'))

      await waitFor(() => {
        expect(screen.getByTestId('feedback-dialog')).toBeInTheDocument()
      })
    })

    it('does not show export button when no project is selected', () => {
      mockCurrentProjectId = null
      render(<Header />)

      expect(screen.queryByTestId('download-icon')).not.toBeInTheDocument()
    })

    it('shows export button when project is selected', () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      expect(screen.getByTestId('download-icon')).toBeInTheDocument()
    })

    it('does not show history button when no project is selected', () => {
      mockCurrentProjectId = null
      render(<Header />)

      expect(screen.queryByTestId('history-icon')).not.toBeInTheDocument()
    })

    it('shows history button when project is selected', () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      expect(screen.getByTestId('history-icon')).toBeInTheDocument()
    })

    it('calls exportDrafts when export button is clicked', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const exportButton = screen.getByTestId('download-icon').closest('button')
      fireEvent.click(exportButton!)

      await waitFor(() => {
        expect(mockExportDrafts).toHaveBeenCalled()
      })
      await waitFor(() => {
        expect(mockToastSuccess).toHaveBeenCalledWith('正文已导出为 TXT 文件')
      })
    })

    it('offers an outline-included export next to the plain one', async () => {
      mockCurrentProjectId = 'project-123'
      mockExportDrafts.mockResolvedValueOnce({ filename: '晚风_大纲和正文.txt', includesOutline: true })
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      fireEvent.click(screen.getByText('导出大纲和正文（TXT）'))

      await waitFor(() => {
        expect(mockExportDrafts).toHaveBeenCalledWith({ includeOutline: true })
      })
      await waitFor(() => {
        expect(mockToastSuccess).toHaveBeenCalledWith('大纲和正文已导出为 TXT 文件')
      })
    })

    it('does not promise the outline when the server sent the text only', async () => {
      // Vercel ships before Railway: the old API ignores include_outline.
      mockCurrentProjectId = 'project-123'
      mockExportDrafts.mockResolvedValueOnce({ filename: '晚风_正文.txt', includesOutline: false })
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      fireEvent.click(screen.getByText('导出大纲和正文（TXT）'))

      await waitFor(() => {
        expect(mockToastInfo).toHaveBeenCalledWith('这次只导出了正文，大纲没能一起导出，请过几分钟再试')
      })
      expect(mockToastSuccess).not.toHaveBeenCalledWith('大纲和正文已导出为 TXT 文件')
    })

    it('uses a neutral toast when the exported file name is unreadable', async () => {
      mockCurrentProjectId = 'project-123'
      mockExportDrafts.mockResolvedValueOnce({ filename: null, includesOutline: null })
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-more-actions'))
      fireEvent.click(screen.getByText('导出大纲和正文（TXT）'))

      await waitFor(() => {
        expect(mockToastSuccess).toHaveBeenCalledWith('已导出为 TXT 文件')
      })
    })

    it('shows translated error toast when export fails', async () => {
      mockCurrentProjectId = 'project-123'
      const exportError = new Error('ERR_QUOTA_EXPORT_FORMAT_RESTRICTED')
      mockExportDrafts.mockRejectedValueOnce(exportError)
      mockHandleApiError.mockReturnValueOnce('Plan does not support this export format')
      render(<Header />)

      const exportButton = screen.getByTestId('download-icon').closest('button')
      fireEvent.click(exportButton!)

      await waitFor(() => {
        expect(mockHandleApiError).toHaveBeenCalledWith(exportError)
        expect(mockLoggerError).toHaveBeenCalledWith('Export failed', exportError)
        expect(mockToastError).toHaveBeenCalledWith('Plan does not support this export format')
      })
      expect(mockToastSuccess).not.toHaveBeenCalled()
    })

    it('shows upgrade modal when export format is plan-restricted', async () => {
      mockCurrentProjectId = 'project-123'
      const exportError = new ApiError(402, 'ERR_QUOTA_EXPORT_FORMAT_RESTRICTED')
      mockExportDrafts.mockRejectedValueOnce(exportError)
      mockHandleApiError.mockReturnValueOnce('Plan does not support this export format')
      render(<Header />)

      const exportButton = screen.getByTestId('download-icon').closest('button')
      fireEvent.click(exportButton!)

      await waitFor(() => {
        expect(mockLoggerError).toHaveBeenCalledWith('Export failed', exportError)
        expect(screen.getByRole('dialog')).toBeInTheDocument()
        expect(screen.getByText('升级专业版')).toBeInTheDocument()
      })
    })

    it('shows version history panel when history button is clicked', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const historyButton = screen.getByTestId('history-icon').closest('button')
      fireEvent.click(historyButton!)

      await waitFor(() => {
        expect(screen.getByTestId('version-history-panel')).toBeInTheDocument()
      })
    })

    it('closes version history panel when close is clicked', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const historyButton = screen.getByTestId('history-icon').closest('button')
      fireEvent.click(historyButton!)

      await waitFor(() => {
        expect(screen.getByTestId('version-history-panel')).toBeInTheDocument()
      })

      const closeButton = screen.getByText('Close')
      fireEvent.click(closeButton)

      await waitFor(() => {
        expect(screen.queryByTestId('version-history-panel')).not.toBeInTheDocument()
      })
    })

    it('refreshes tree and clears selection after rollback callback', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const historyButton = screen.getByTestId('history-icon').closest('button')
      fireEvent.click(historyButton!)

      await waitFor(() => {
        expect(screen.getByTestId('version-history-panel')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByText('Trigger Rollback'))

      await waitFor(() => {
        expect(mockTriggerFileTreeRefresh).toHaveBeenCalledTimes(1)
        expect(mockSetSelectedItem).toHaveBeenCalledWith(null)
        expect(screen.queryByTestId('version-history-panel')).not.toBeInTheDocument()
      })
    })
  })

  describe('Settings Dialog', () => {
    it('shows settings dialog when settings button is clicked', async () => {
      render(<Header />)

      const settingsButton = screen.getByTestId('settings-button')
      fireEvent.click(settingsButton)

      await waitFor(() => {
        expect(screen.getByTestId('settings-dialog')).toBeInTheDocument()
      })
    })

    it('closes settings dialog when close is clicked', async () => {
      render(<Header />)

      const settingsButton = screen.getByTestId('settings-button')
      fireEvent.click(settingsButton)

      await waitFor(() => {
        expect(screen.getByTestId('settings-dialog')).toBeInTheDocument()
      })

      const closeButton = screen.getByText('Close Settings')
      fireEvent.click(closeButton)

      await waitFor(() => {
        expect(screen.queryByTestId('settings-dialog')).not.toBeInTheDocument()
      })
    })
  })

  describe('Mobile Layout', () => {
    beforeEach(() => {
      mockIsMobile = true
    })

    it('renders mobile menu button', () => {
      render(<Header />)

      expect(screen.getByTestId('menu-icon')).toBeInTheDocument()
    })

    it('mobile menu button has visibility classes (flex md:hidden)', () => {
      render(<Header />)

      const menuButtonContainer = screen.getByTestId('menu-icon').closest('div')
      expect(menuButtonContainer).toHaveClass('flex')
      expect(menuButtonContainer).toHaveClass('md:hidden')
    })

    it('opens mobile menu when menu button is clicked', async () => {
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('x-icon')).toBeInTheDocument()
        expect(screen.getByTestId('user-menu-mobile')).toBeInTheDocument()
      })
    })

    it('closes mobile menu when X button is clicked', async () => {
      render(<Header />)

      // Open menu
      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('x-icon')).toBeInTheDocument()
      })

      // Close menu
      const closeButton = screen.getByTestId('x-icon').closest('button')
      fireEvent.click(closeButton!)

      await waitFor(() => {
        expect(screen.getByTestId('menu-icon')).toBeInTheDocument()
      })
    })

    it('shows mobile user menu when menu is open', async () => {
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('user-menu-mobile')).toBeInTheDocument()
      })
    })

    it('shows mobile settings button when menu is open', async () => {
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        const settingsButtons = screen.getAllByTestId('settings-button')
        expect(settingsButtons.length).toBeGreaterThan(0)
      })
    })

    it('shows mobile subscription entry when menu is open', async () => {
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('header-subscription-entry-mobile')).toBeInTheDocument()
      })
    })

    it('shows export button in mobile menu when project is selected', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        // There should be download icons (one from desktop actions + one from mobile menu)
        const downloadIcons = screen.getAllByTestId('download-icon')
        expect(downloadIcons.length).toBeGreaterThan(0)
      })
    })

    it('navigates to project dashboard from mobile menu', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('project-dashboard-button-mobile')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByTestId('project-dashboard-button-mobile'))
      expect(mockNavigate).toHaveBeenCalledWith('/project/project-123/dashboard')
    })

    it('navigates to docs from mobile menu', async () => {
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('help-docs-button-mobile')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByTestId('help-docs-button-mobile'))
      expect(mockNavigate).toHaveBeenCalledWith('/docs')
    })

    it('does not show export button when no project in mobile mode', async () => {
      mockCurrentProjectId = null
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('user-menu-mobile')).toBeInTheDocument()
      })

      expect(screen.queryByTestId('download-icon')).not.toBeInTheDocument()
    })

    it('shows history button in mobile menu when project is selected', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        // There should be history icons (one from desktop actions + one from mobile menu)
        const historyIcons = screen.getAllByTestId('history-icon')
        expect(historyIcons.length).toBeGreaterThan(0)
      })
    })

    it('closes mobile menu after clicking settings', async () => {
      render(<Header />)

      // Open menu
      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('user-menu-mobile')).toBeInTheDocument()
      })

      // Click mobile settings button (second one, as first is desktop)
      const settingsButtons = screen.getAllByTestId('settings-button')
      const mobileSettingsButton = settingsButtons.find(btn =>
        btn.closest('[class*="flex-col"]')
      ) || settingsButtons[settingsButtons.length - 1]
      fireEvent.click(mobileSettingsButton)

      await waitFor(() => {
        expect(screen.getByTestId('menu-icon')).toBeInTheDocument()
      })
    })

    it('closes mobile menu after clicking history', async () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      // Open menu
      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getAllByTestId('history-icon').length).toBeGreaterThan(0)
      })

      // Click history in mobile menu (find it in the dropdown container)
      const dropdownContainer = screen.getByTestId('user-menu-mobile').closest('[class*="flex-col"]')
      const historyButtons = dropdownContainer?.querySelectorAll('button')
      const historyButton = Array.from(historyButtons || []).find(btn =>
        btn.querySelector('[data-testid="history-icon"]')
      )
      fireEvent.click(historyButton!)

      await waitFor(() => {
        expect(screen.getByTestId('menu-icon')).toBeInTheDocument()
      })
    })

    it('keeps mobile menu open and shows error toast when mobile export fails', async () => {
      mockCurrentProjectId = 'project-123'
      mockExportDrafts.mockRejectedValueOnce(new Error('ERR_EXPORT_NO_DRAFTS'))
      mockHandleApiError.mockReturnValueOnce('No content found in this project')
      render(<Header />)

      const menuButton = screen.getByTestId('menu-icon').closest('button')
      fireEvent.click(menuButton!)

      await waitFor(() => {
        expect(screen.getByTestId('x-icon')).toBeInTheDocument()
      })

      const dropdownContainer = screen.getByTestId('user-menu-mobile').closest('[class*="flex-col"]')
      const buttons = dropdownContainer?.querySelectorAll('button')
      const exportButton = Array.from(buttons || []).find(btn =>
        btn.querySelector('[data-testid="download-icon"]')
      )
      fireEvent.click(exportButton!)

      await waitFor(() => {
        expect(mockToastError).toHaveBeenCalledWith('No content found in this project')
      })
      expect(screen.getByTestId('x-icon')).toBeInTheDocument()
    })

    it('does not show mobile dropdown when isMobile is false', () => {
      mockIsMobile = false
      render(<Header />)

      // Even if we try to click menu button, dropdown shouldn't appear
      expect(screen.queryByTestId('user-menu-mobile')).not.toBeInTheDocument()
    })
  })

  describe('Desktop Layout', () => {
    beforeEach(() => {
      mockIsMobile = false
    })

    it('has desktop actions container with proper classes', () => {
      render(<Header />)

      const userMenu = screen.getByTestId('user-menu')
      const desktopContainer = userMenu.closest('div[class*="items-center"]')
      expect(desktopContainer).toHaveClass('hidden')
      expect(desktopContainer).toHaveClass('md:flex')
    })

    it('shows all action buttons on desktop', () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      expect(screen.getByTestId('header-subscription-entry')).toBeInTheDocument()
      expect(screen.getByTestId('header-more-actions')).toBeInTheDocument()
      expect(screen.getByTestId('download-icon')).toBeInTheDocument()
      expect(screen.getByTestId('history-icon')).toBeInTheDocument()
      expect(screen.getByTestId('settings-icon')).toBeInTheDocument()
      expect(screen.getByTestId('user-menu')).toBeInTheDocument()
    })

    it('renders icon-only subscription entry on desktop', () => {
      render(<Header />)

      const entry = screen.getByTestId('header-subscription-entry')
      expect(entry).toHaveAttribute('title')
      expect(screen.getByTestId('subscription-icon')).toBeInTheDocument()
      expect(entry).not.toHaveTextContent('订阅权益')
    })

    it('navigates to billing when subscription entry is clicked', () => {
      render(<Header />)

      fireEvent.click(screen.getByTestId('header-subscription-entry'))

      expect(mockNavigate).toHaveBeenCalledWith('/dashboard/billing')
    })

    it('has mobile menu button with visibility classes', () => {
      render(<Header />)

      const menuIcon = screen.getByTestId('menu-icon')
      const mobileContainer = menuIcon.closest('div[class*="items-center"]')
      expect(mobileContainer).toHaveClass('flex')
      expect(mobileContainer).toHaveClass('md:hidden')
    })
  })

  describe('Styling', () => {
    it('has correct header classes', () => {
      render(<Header />)

      const header = screen.getByRole('banner')
      expect(header).toHaveClass('h-12')
      expect(header).toHaveClass('flex')
      expect(header).toHaveClass('items-center')
    })

    it('has proper padding classes', () => {
      render(<Header />)

      const header = screen.getByRole('banner')
      expect(header).toHaveClass('px-2')
      expect(header).toHaveClass('md:px-4')
    })

    it('has justify-between for layout', () => {
      render(<Header />)

      const header = screen.getByRole('banner')
      expect(header).toHaveClass('justify-between')
    })

    it('has shrink-0 class', () => {
      render(<Header />)

      const header = screen.getByRole('banner')
      expect(header).toHaveClass('shrink-0')
    })

    it('lets the project switcher take the remaining width without a viewport calc, so it cannot cover the menu button', () => {
      mockIsMobile = true
      render(<Header />)

      const switcherSlot = screen.getByTestId('project-switcher').parentElement as HTMLElement
      expect(switcherSlot).toHaveClass('min-w-0', 'flex-1', 'md:flex-none', 'md:max-w-[420px]')
      expect(switcherSlot.className).not.toMatch(/calc\(/)
      // The dropdown must not be clipped by an ancestor.
      let ancestor: HTMLElement | null = switcherSlot
      while (ancestor && ancestor.tagName !== 'HEADER') {
        expect(ancestor.className).not.toMatch(/overflow-hidden/)
        ancestor = ancestor.parentElement
      }
      // The menu toggle sits outside the shrinking group and never shrinks.
      const menuToggleGroup = screen.getByTestId('menu-icon').closest('div') as HTMLElement
      expect(menuToggleGroup).toHaveClass('shrink-0')
      expect(menuToggleGroup.contains(switcherSlot)).toBe(false)
    })
  })

  describe('Accessibility', () => {
    it('has proper header role', () => {
      render(<Header />)

      expect(screen.getByRole('banner')).toBeInTheDocument()
    })

    it('has accessible button titles on desktop', () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const buttons = screen.getAllByRole('button')
      expect(buttons.length).toBeGreaterThan(0)
    })

    it('has title attributes on action buttons', () => {
      mockCurrentProjectId = 'project-123'
      render(<Header />)

      const downloadButton = screen.getByTestId('download-icon').closest('button')
      expect(downloadButton).toHaveAttribute('title')

      const historyButton = screen.getByTestId('history-icon').closest('button')
      expect(historyButton).toHaveAttribute('title')
    })
  })
})
