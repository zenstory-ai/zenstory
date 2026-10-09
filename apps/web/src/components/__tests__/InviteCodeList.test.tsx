import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { InviteCodeList } from '../referral/InviteCodeList'
import * as referralApi from '@/lib/referralApi'
import type { InviteCode } from '@/types/referral'

const mockUseAuth = vi.hoisted(() =>
  vi.fn(() => ({
    user: { is_superuser: false },
  }))
)

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: mockUseAuth,
}))

// Mock the referralApi
vi.mock('@/lib/referralApi', () => ({
  referralApi: {
    getInviteCodes: vi.fn(),
    createInviteCode: vi.fn(),
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number; defaultValue?: string }) => {
      const translations: Record<string, string> = {
        'inviteCodes.title': '我的邀请码',
        'inviteCodes.maxHint': `最多可创建 ${options?.count ?? 3} 个邀请码`,
        'inviteCodes.unlimitedHint': '可创建无限邀请码',
        'inviteCodes.generateButton': '生成邀请码',
        'inviteCodes.loadError': '加载邀请码失败，请重试',
        'inviteCodes.noCodes': '暂无邀请码',
        'inviteCodes.noCodesHint': '邀请好友注册并获得奖励',
        'inviteCodes.createError': '创建邀请码失败，请重试',
      };
      return translations[key] ?? options?.defaultValue ?? key;
    },
  }),
}))

// Mock InviteCodeCard since it's tested separately
vi.mock('../referral/InviteCodeCard', () => ({
  InviteCodeCard: ({ inviteCode }: { inviteCode: InviteCode }) => (
    <div data-testid={`invite-code-${inviteCode.id}`}>{inviteCode.code}</div>
  ),
}))

const mockInviteCodes: InviteCode[] = [
  {
    id: 'code-1',
    code: 'ABCD-EFGH',
    max_uses: 10,
    current_uses: 3,
    is_active: true,
    expires_at: null,
    created_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 'code-2',
    code: 'IJKL-MNOP',
    max_uses: 5,
    current_uses: 0,
    is_active: true,
    expires_at: null,
    created_at: '2024-01-02T00:00:00Z',
  },
]

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('InviteCodeList', () => {
  const mockGetInviteCodes = vi.mocked(referralApi.referralApi.getInviteCodes)
  const mockCreateInviteCode = vi.mocked(referralApi.referralApi.createInviteCode)

  beforeEach(() => {
    vi.clearAllMocks()
    mockUseAuth.mockReturnValue({
      user: { is_superuser: false },
    })
    mockGetInviteCodes.mockResolvedValue([])
    mockCreateInviteCode.mockResolvedValue({
      id: 'new-code',
      code: 'NEW-CODE',
      max_uses: 10,
      current_uses: 0,
      is_active: true,
      expires_at: null,
      created_at: '2024-01-03T00:00:00Z',
    })
  })

  describe('Rendering', () => {
    it('renders header with title', async () => {
      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('我的邀请码')).toBeInTheDocument()
      })
    })

    it('renders subtitle with max codes info', async () => {
      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('最多可创建 3 个邀请码')).toBeInTheDocument()
      })
    })

    it('renders create button', async () => {
      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })
    })

    it('shows loading state initially', () => {
      mockGetInviteCodes.mockImplementation(() => new Promise(() => {})) // Never resolves

      render(<InviteCodeList />, { wrapper: createWrapper() })

      // Should show spinner
      const spinner = document.querySelector('.animate-spin')
      expect(spinner).toBeInTheDocument()
    })

    it('shows error state when fetch fails', async () => {
      mockGetInviteCodes.mockRejectedValue(new Error('Network error'))

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('加载邀请码失败，请重试')).toBeInTheDocument()
      })
    })

    it('shows empty state when no codes', async () => {
      mockGetInviteCodes.mockResolvedValue([])

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('暂无邀请码')).toBeInTheDocument()
      })
    })

    it('keeps the header action as the only button in the empty state', async () => {
      mockGetInviteCodes.mockResolvedValue([])

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('暂无邀请码')).toBeInTheDocument()
      })
      // One solid primary per view: the empty card no longer repeats the create action.
      const buttons = screen.getAllByRole('button')
      expect(buttons).toHaveLength(1)
      expect(buttons[0]).toHaveTextContent('生成邀请码')
    })
  })

  describe('List Display', () => {
    it('renders list of invite codes', async () => {
      mockGetInviteCodes.mockResolvedValue(mockInviteCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByTestId('invite-code-code-1')).toBeInTheDocument()
        expect(screen.getByTestId('invite-code-code-2')).toBeInTheDocument()
      })
    })

    it('displays code content correctly', async () => {
      mockGetInviteCodes.mockResolvedValue(mockInviteCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('ABCD-EFGH')).toBeInTheDocument()
        expect(screen.getByText('IJKL-MNOP')).toBeInTheDocument()
      })
    })
  })

  describe('Create Button', () => {
    it('is enabled when under the limit', async () => {
      mockGetInviteCodes.mockResolvedValue([mockInviteCodes[0]]) // 1 code

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        const button = screen.getByText('生成邀请码').closest('button')
        expect(button).not.toBeDisabled()
      })
    })

    it('is enabled when at 2 codes', async () => {
      mockGetInviteCodes.mockResolvedValue(mockInviteCodes) // 2 codes

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        const button = screen.getByText('生成邀请码').closest('button')
        expect(button).not.toBeDisabled()
      })
    })

    it('is disabled when at max limit (3)', async () => {
      const threeCodes: InviteCode[] = [
        ...mockInviteCodes,
        {
          id: 'code-3',
          code: 'QRST-UVWX',
          max_uses: 10,
          current_uses: 0,
          is_active: true,
          expires_at: null,
          created_at: '2024-01-03T00:00:00Z',
        },
      ]
      mockGetInviteCodes.mockResolvedValue(threeCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        const button = screen.getByText('生成邀请码').closest('button')
        expect(button).toBeDisabled()
      })
    })

    it('stays enabled for superuser even when at max limit (3)', async () => {
      mockUseAuth.mockReturnValue({
        user: { is_superuser: true },
      })

      const threeCodes: InviteCode[] = [
        ...mockInviteCodes,
        {
          id: 'code-3',
          code: 'QRST-UVWX',
          max_uses: 10,
          current_uses: 0,
          is_active: true,
          expires_at: null,
          created_at: '2024-01-03T00:00:00Z',
        },
      ]
      mockGetInviteCodes.mockResolvedValue(threeCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        const button = screen.getByText('生成邀请码').closest('button')
        expect(button).not.toBeDisabled()
      })
    })

    it('stays enabled when total is 3 but only 2 are active', async () => {
      const mixedCodes: InviteCode[] = [
        ...mockInviteCodes,
        {
          id: 'code-3',
          code: 'QRST-UVWX',
          max_uses: 10,
          current_uses: 0,
          is_active: false,
          expires_at: null,
          created_at: '2024-01-03T00:00:00Z',
        },
      ]
      mockGetInviteCodes.mockResolvedValue(mixedCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        const button = screen.getByText('生成邀请码').closest('button')
        expect(button).not.toBeDisabled()
      })
    })

    it('shows Plus icon when not creating', async () => {
      mockGetInviteCodes.mockResolvedValue([])

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        const button = screen.getByText('生成邀请码').closest('button')
        // Plus icon should be present (no animate-spin class)
        expect(button?.querySelector('.animate-spin')).not.toBeInTheDocument()
      })
    })

    it('shows spinner when creating', async () => {
      mockGetInviteCodes.mockResolvedValue([])
      mockCreateInviteCode.mockImplementation(() => new Promise(() => {})) // Never resolves

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })

      const button = screen.getByText('生成邀请码').closest('button')
      fireEvent.click(button!)

      await waitFor(() => {
        expect(button?.querySelector('.animate-spin')).toBeInTheDocument()
      })
    })

    it('is disabled while creating', async () => {
      mockGetInviteCodes.mockResolvedValue([])
      mockCreateInviteCode.mockImplementation(() => new Promise(() => {})) // Never resolves

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })

      const button = screen.getByText('生成邀请码').closest('button')
      fireEvent.click(button!)

      // Wait for mutation to start and button to become disabled
      await waitFor(() => {
        expect(button).toBeDisabled()
      })
    })
  })

  describe('Create Functionality', () => {
    it('calls createInviteCode when button clicked', async () => {
      mockGetInviteCodes.mockResolvedValue([])

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })

      const button = screen.getByText('生成邀请码').closest('button')
      fireEvent.click(button!)

      // Wait for mutation to be called
      await waitFor(() => {
        expect(mockCreateInviteCode).toHaveBeenCalledTimes(1)
      })
    })

    it('adds new code to list after creation', async () => {
      mockGetInviteCodes.mockResolvedValue([])

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })

      const button = screen.getByText('生成邀请码').closest('button')
      fireEvent.click(button!)

      await waitFor(() => {
        expect(screen.getByTestId('invite-code-new-code')).toBeInTheDocument()
      })
    })

    it('shows error message when creation fails', async () => {
      mockGetInviteCodes.mockResolvedValue([])
      mockCreateInviteCode.mockRejectedValue(new Error('Creation failed'))

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })

      const button = screen.getByText('生成邀请码').closest('button')
      fireEvent.click(button!)

      await waitFor(() => {
        expect(screen.getByText('创建邀请码失败，请重试')).toBeInTheDocument()
      })
    })

    it('does not create when at limit', async () => {
      const threeCodes: InviteCode[] = [
        ...mockInviteCodes,
        {
          id: 'code-3',
          code: 'QRST-UVWX',
          max_uses: 10,
          current_uses: 0,
          is_active: true,
          expires_at: null,
          created_at: '2024-01-03T00:00:00Z',
        },
      ]
      mockGetInviteCodes.mockResolvedValue(threeCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        const button = screen.getByText('生成邀请码').closest('button')
        fireEvent.click(button!)
      })

      expect(mockCreateInviteCode).not.toHaveBeenCalled()
    })

    it('creates when active count is below limit even if total count is 3', async () => {
      const mixedCodes: InviteCode[] = [
        ...mockInviteCodes,
        {
          id: 'code-3',
          code: 'QRST-UVWX',
          max_uses: 10,
          current_uses: 0,
          is_active: false,
          expires_at: null,
          created_at: '2024-01-03T00:00:00Z',
        },
      ]
      mockGetInviteCodes.mockResolvedValue(mixedCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })

      const button = screen.getByText('生成邀请码').closest('button')
      fireEvent.click(button!)

      await waitFor(() => {
        expect(mockCreateInviteCode).toHaveBeenCalledTimes(1)
      })
    })

    it('creates the first code from the header button in the empty state', async () => {
      mockGetInviteCodes.mockResolvedValue([])

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('暂无邀请码')).toBeInTheDocument()
      })

      fireEvent.click(screen.getByRole('button', { name: /生成邀请码/ }))

      // Wait for mutation to be called
      await waitFor(() => {
        expect(mockCreateInviteCode).toHaveBeenCalledTimes(1)
      })
    })
  })

  describe('Loading State', () => {
    it('shows loading spinner during initial fetch', () => {
      mockGetInviteCodes.mockImplementation(() => new Promise(() => {}))

      render(<InviteCodeList />, { wrapper: createWrapper() })

      const spinner = document.querySelector('.animate-spin')
      expect(spinner).toBeInTheDocument()
    })

    it('hides loading spinner after fetch completes', async () => {
      mockGetInviteCodes.mockResolvedValue(mockInviteCodes)

      render(<InviteCodeList />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('我的邀请码')).toBeInTheDocument()
      })

      // Initial loading spinner should be gone
      const spinners = document.querySelectorAll('.animate-spin')
      expect(spinners.length).toBe(0)
    })
  })

  describe('Query Client Integration', () => {
    it('updates cache after successful creation', async () => {
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
          },
        },
      })

      mockGetInviteCodes.mockResolvedValue([])

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      )

      render(<InviteCodeList />, { wrapper })

      await waitFor(() => {
        expect(screen.getByText('生成邀请码')).toBeInTheDocument()
      })

      const button = screen.getByText('生成邀请码').closest('button')
      fireEvent.click(button!)

      await waitFor(() => {
        const cachedData = queryClient.getQueryData(['inviteCodes'])
        expect(cachedData).toHaveLength(1)
      })
    })
  })
})
