import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

let mockIsMobile = false
let mockIsTablet = false
let mockIsDesktop = true
let mockTheme: 'dark' | 'light' = 'light'
let mockLanguage = 'zh-CN'
let mockResolvedLanguage: string | undefined = 'zh-CN'
let mockProjects: Array<{ id: string; name: string; project_type: 'novel'; updated_at?: string | null }> = []
let mockProjectsLoading = false
const inspirationFeature = vi.hoisted(() => ({ enabled: true }))

const mockChangeLanguage = vi.fn()
const mockSetTheme = vi.fn()
const mockLogout = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'userPanel.quickSettings': 'Quick settings',
        'userPanel.openPanel': 'Open user settings panel',
        'userPanel.adminPanel': 'Admin panel',
        'dashboard:userPanel.quickSettings': 'Quick settings',
        'dashboard:userPanel.openPanel': 'Open user settings panel',
        'dashboard:userPanel.adminPanel': 'Admin panel',
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
        'nav.lab': 'Lab',
      }
      return translations[key] || key
    },
    i18n: {
      language: mockLanguage,
      resolvedLanguage: mockResolvedLanguage,
      changeLanguage: mockChangeLanguage,
    },
  }),
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => mockIsMobile,
  useIsTablet: () => mockIsTablet,
  useIsDesktop: () => mockIsDesktop,
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: {
      username: 'test-user',
      email: 'test@example.com',
      avatar_url: null,
      is_superuser: false,
    },
    logout: mockLogout,
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: mockProjects,
    loading: mockProjectsLoading,
  }),
}))

vi.mock('../../contexts/ThemeContext', () => ({
  useTheme: () => ({
    theme: mockTheme,
    setTheme: mockSetTheme,
  }),
}))

vi.mock('../../components/SettingsDialog', () => ({
  SettingsDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div data-testid="settings-dialog">Settings Dialog</div> : null,
}))

vi.mock('../../components/UserMenu', () => ({
  UserAvatar: ({ username }: { username: string }) => <span>{username}</span>,
}))

vi.mock('../../config/inspirations', () => ({
  inspirationsConfig: inspirationFeature,
}))

import Dashboard from '../Dashboard'

const renderDashboard = (initialEntries: string[] = ['/dashboard']) => {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/dashboard" element={<Dashboard />}>
          <Route index element={<div>Dashboard Home</div>} />
          <Route path="inspirations" element={<div>Inspirations Page</div>} />
          <Route path="inspirations/:inspirationId" element={<div>Inspiration Detail Page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

describe('Dashboard user panel and quick switches', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockIsMobile = false
    mockIsTablet = false
    mockIsDesktop = true
    mockTheme = 'light'
    mockLanguage = 'zh-CN'
    mockResolvedLanguage = 'zh-CN'
    mockProjects = []
    mockProjectsLoading = false
    inspirationFeature.enabled = true
  })

  it('opens user panel and closes on outside click', async () => {
    renderDashboard()

    fireEvent.click(screen.getByRole('button', { name: 'Open user settings panel' }))
    expect(screen.getByText('Quick settings')).toBeInTheDocument()

    fireEvent.mouseDown(document.body)

    await waitFor(() => {
      expect(screen.queryByText('Quick settings')).not.toBeInTheDocument()
    })
  })

  it('supports desktop quick theme and language switching with locale variants', () => {
    renderDashboard()

    fireEvent.click(screen.getByRole('button', { name: 'Open user settings panel' }))

    const zhButton = screen.getByRole('button', { name: '切换到中文' })
    expect(zhButton.className).toContain('accent-primary')

    fireEvent.click(screen.getByRole('button', { name: 'Dark mode' }))
    expect(mockSetTheme).toHaveBeenCalledWith('dark')

    fireEvent.click(screen.getByRole('button', { name: '切换到英文' }))
    expect(mockChangeLanguage).toHaveBeenCalledWith('en')
  })

  it('renders user panel with elevated z-index so quick settings stay clickable', () => {
    renderDashboard()

    fireEvent.click(screen.getByRole('button', { name: 'Open user settings panel' }))

    const quickSettingsTitle = screen.getByText('Quick settings')
    const panel = quickSettingsTitle.closest('div[class*="z-\\[1200\\]"]')
    expect(panel).toBeTruthy()
  })

  it('links the support email from the desktop account panel', () => {
    renderDashboard()
    fireEvent.click(screen.getByRole('button', { name: 'Open user settings panel' }))
    expect(screen.getByTestId('dashboard-contact-support-link')).toHaveAttribute('href', 'mailto:support@zenstory.ai')
    expect(screen.getByTestId('dashboard-contact-support-link')).toHaveTextContent('support@zenstory.ai')
  })

  it('links the support email from the mobile menu', () => {
    mockIsMobile = true
    mockIsDesktop = false
    renderDashboard()
    fireEvent.click(screen.getByRole('button', { name: '打开菜单' }))
    expect(screen.getByTestId('dashboard-contact-support-link-mobile')).toHaveAttribute('href', 'mailto:support@zenstory.ai')
  })

  it('toggles language correctly in mobile quick menu when locale is zh-CN', () => {
    mockIsMobile = true
    mockIsDesktop = false
    mockResolvedLanguage = undefined
    renderDashboard()

    fireEvent.click(screen.getByRole('button', { name: '打开菜单' }))
    fireEvent.click(screen.getByRole('button', { name: '切换界面语言' }))

    expect(mockChangeLanguage).toHaveBeenCalledWith('en')
  })

  it('exposes and dismisses the mobile navigation disclosure with focus return', () => {
    mockIsMobile = true
    mockIsDesktop = false
    renderDashboard()

    const trigger = screen.getByRole('button', { name: '打开菜单' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveAttribute('aria-controls', 'dashboard-mobile-navigation')

    fireEvent.click(trigger)
    expect(screen.getByRole('button', { name: '关闭菜单' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('navigation', { name: '移动端导航菜单' })).toHaveAttribute('id', 'dashboard-mobile-navigation')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('navigation', { name: '移动端导航菜单' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开菜单' })).toHaveFocus()
  })

  it('highlights inspirations nav item for /dashboard/inspirations/* routes', () => {
    renderDashboard(['/dashboard/inspirations/test-inspiration-id'])

    const inspirationsButton = screen.getByRole('button', { name: 'Inspirations' })
    expect(inspirationsButton.className).toContain('accent-primary')
    expect(screen.getByText('Inspiration Detail Page')).toBeInTheDocument()
  })

  it('hides inspirations from desktop and mobile navigation when disabled', () => {
    inspirationFeature.enabled = false
    const { unmount } = renderDashboard()

    expect(screen.queryByRole('button', { name: 'Inspirations' })).not.toBeInTheDocument()
    unmount()

    mockIsMobile = true
    mockIsDesktop = false
    renderDashboard()
    fireEvent.click(screen.getByRole('button', { name: '打开菜单' }))

    expect(screen.queryByRole('button', { name: 'Inspirations' })).not.toBeInTheDocument()
  })

  it('does not render deprecated Lab nav entry', () => {
    renderDashboard()

    expect(screen.queryByRole('button', { name: 'Lab' })).not.toBeInTheDocument()
  })
})
