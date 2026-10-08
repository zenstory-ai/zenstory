import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { UserAvatar, UserMenu, UserMenuMobile } from '../UserMenu'

const mockNavigate = vi.fn()
const mockLogout = vi.fn()
const mockMutate = vi.fn()
const mockInvalidateQueries = vi.fn()
const mockUseAuth = vi.fn()
let checkedIn = false
let checkInPending = false

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) =>
      (
        {
          'userMenu.open': 'Open user menu',
          'userMenu.close': 'Close user menu',
          'userMenu.adminPanel': 'Admin panel',
          'userMenu.logout': 'Log out',
          'common:userMenu.logout': 'Log out',
          'settings:points.alreadyCheckedIn': 'Already checked in',
          'settings:points.checkIn': 'Check in',
          'common:processing': 'Loading',
        } as Record<string, string>
      )[key] ?? fallback ?? key,
  }),
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: { checked_in: checkedIn } }),
  useMutation: () => ({ mutate: mockMutate, isPending: checkInPending }),
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}))

vi.mock('../../lib/pointsApi', () => ({
  pointsApi: {
    getCheckInStatus: vi.fn(),
    checkIn: vi.fn(),
  },
}))

describe('UserMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    checkedIn = false
    checkInPending = false
    mockUseAuth.mockReturnValue({
      user: {
        username: 'Alice',
        email: 'alice@example.com',
        avatar_url: null,
        is_superuser: true,
      },
      logout: mockLogout,
    })
  })

  it('renders avatar fallback initials and hides image after an error', () => {
    render(<UserAvatar username="alice" avatarUrl="https://example.com/avatar.png" />)

    const image = screen.getByAltText('alice')
    fireEvent.error(image)

    expect(screen.getByText('A')).toBeInTheDocument()
  })

  it('renders the avatar image when it loads successfully', () => {
    render(<UserAvatar username="alice" avatarUrl="https://example.com/avatar.png" />)
    expect(screen.getByAltText('alice')).toHaveAttribute('src', 'https://example.com/avatar.png')
  })

  it('opens the desktop dropdown, supports admin navigation, check-in, and logout', async () => {
    render(<UserMenu />)

    fireEvent.click(screen.getByTestId('user-menu-button'))
    expect(screen.getByText('alice@example.com')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Admin panel'))
    expect(mockNavigate).toHaveBeenCalledWith('/admin')

    fireEvent.click(screen.getByTestId('user-menu-button'))
    fireEvent.click(screen.getByTestId('check-in-button'))
    expect(mockMutate).toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('logout-button'))
    expect(mockLogout).toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('user-menu-button'))
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByText('alice@example.com')).not.toBeInTheDocument()
    })
  })

  it('closes on outside click and reflects checked-in / pending states', async () => {
    const { rerender } = render(<UserMenu />)

    fireEvent.click(screen.getByTestId('user-menu-button'))
    fireEvent.mouseDown(document.body)
    await waitFor(() => {
      expect(screen.queryByText('alice@example.com')).not.toBeInTheDocument()
    })

    checkedIn = true
    rerender(<UserMenu />)
    fireEvent.click(screen.getByTestId('user-menu-button'))
    expect(screen.getByTestId('check-in-button')).toBeDisabled()
    expect(screen.getByText('Already checked in')).toBeInTheDocument()

    checkedIn = false
    checkInPending = true
    rerender(<UserMenu />)
    expect(screen.getByText('Loading')).toBeInTheDocument()
  })

  it('links to the support email from desktop and mobile menus', () => {
    render(<UserMenu />)
    fireEvent.click(screen.getByTestId('user-menu-button'))
    expect(screen.getByTestId('contact-support-link')).toHaveAttribute('href', 'mailto:support@zenstory.ai')
    expect(screen.getByTestId('contact-support-link')).toHaveTextContent('support@zenstory.ai')

    render(<UserMenuMobile onLogout={vi.fn()} />)
    expect(screen.getByTestId('contact-support-link-mobile')).toHaveAttribute('href', 'mailto:support@zenstory.ai')
  })

  it('returns null when no user is logged in', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      logout: mockLogout,
    })

    const { container } = render(<UserMenu />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the mobile menu and triggers admin/check-in/logout callbacks', () => {
    const onLogout = vi.fn()
    render(<UserMenuMobile onLogout={onLogout} />)

    fireEvent.click(screen.getByText('Admin panel'))
    expect(onLogout).toHaveBeenCalled()
    expect(mockNavigate).toHaveBeenCalledWith('/admin')

    fireEvent.click(screen.getByTestId('check-in-button-mobile'))
    expect(mockMutate).toHaveBeenCalled()

    fireEvent.click(screen.getByText('Log out'))
    expect(mockLogout).toHaveBeenCalled()
  })

  it('hides admin actions for non-superusers and still logs out on mobile', () => {
    mockUseAuth.mockReturnValue({
      user: {
        username: 'Alice',
        email: 'alice@example.com',
        avatar_url: null,
        is_superuser: false,
      },
      logout: mockLogout,
    })

    const { rerender } = render(<UserMenu />)
    fireEvent.click(screen.getByTestId('user-menu-button'))
    expect(screen.queryByText('Admin panel')).not.toBeInTheDocument()

    rerender(<UserMenuMobile />)
    expect(screen.queryByText('Admin panel')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Log out'))
    expect(mockLogout).toHaveBeenCalled()
  })

  it('renders null for the mobile menu when no user exists', () => {
    mockUseAuth.mockReturnValue({
      user: null,
      logout: mockLogout,
    })

    const { container } = render(<UserMenuMobile />)
    expect(container).toBeEmptyDOMElement()
  })
})
