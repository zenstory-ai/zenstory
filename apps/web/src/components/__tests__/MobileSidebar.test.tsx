import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MobileSidebar from '../MobileSidebar'

const mockNavigate = vi.fn()
const mockLogout = vi.fn()

let mockPathname = '/dashboard/materials'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => ({ pathname: mockPathname }),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          'nav.home': 'Home',
          'nav.projects': 'Projects',
          'nav.materials': 'Materials',
          'nav.skills': 'Skills',
          'nav.lab': 'Lab',
          'nav.logout': 'Logout',
          'common.loading': 'Loading',
          'common:closeSidebar': '关闭侧边栏',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { username: 'sam', email: 'sam@example.com' },
    logout: mockLogout,
  }),
}))

vi.mock('../Logo', () => ({
  Logo: () => <div data-testid="sidebar-logo">Logo</div>,
}))

describe('MobileSidebar', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    mockPathname = '/dashboard/materials'
    mockLogout.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders nothing when closed', () => {
    const { container } = render(<MobileSidebar isOpen={false} onClose={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('navigates to a selected section and closes after the animation delay', async () => {
    const onClose = vi.fn()
    render(<MobileSidebar isOpen={true} onClose={onClose} />)

    expect(screen.getByTestId('sidebar-logo')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Projects' }))

    expect(mockNavigate).toHaveBeenCalledWith('/dashboard/projects')
    expect(onClose).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('handles overlay and explicit close actions', async () => {
    const onClose = vi.fn()
    const { container, rerender } = render(<MobileSidebar isOpen={true} onClose={onClose} />)

    fireEvent.click(container.querySelector('.mobile-drawer-overlay') as HTMLElement)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    expect(onClose).toHaveBeenCalledTimes(1)

    onClose.mockClear()
    rerender(<MobileSidebar isOpen={true} onClose={onClose} />)
    fireEvent.click(screen.getByLabelText('关闭侧边栏'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('shows a loading label while logout is pending', async () => {
    let resolveLogout: (() => void) | undefined
    mockLogout.mockReturnValue(
      new Promise<void>((resolve) => {
        resolveLogout = resolve
      }),
    )

    render(<MobileSidebar isOpen={true} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Logout' }))

    expect(mockLogout).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Loading' })).toBeDisabled()

    await act(async () => {
      resolveLogout?.()
    })
  })
})
