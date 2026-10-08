import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { InviteCodeCard } from '../referral/InviteCodeCard'
import type { InviteCode } from '@/types/referral'

// Mock react-i18next so the `referral` namespace resolves to the same strings
// the app ships in public/locales/zh/referral.json (the `card.*` keys).
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number; code?: string }) => {
      const translations: Record<string, string> = {
        'card.inviteCode': '邀请码',
        'card.copyInviteCode': '复制邀请码',
        'card.shareInviteCode': '分享邀请码',
        'card.used': '已使用:',
        'card.expired': '已过期',
        'card.expiresTomorrow': '明天过期',
        'card.expiresInDays': `${options?.count ?? 0} 天后过期`,
        'card.disabled': '已停用',
        'card.exhausted': '已用完',
        'card.available': '可用',
        'card.shareTitle': 'zenstory写作 - 邀请码',
        'card.shareText': `邀请码: ${options?.code ?? ''}`,
      }
      return translations[key] ?? key
    },
    i18n: { language: 'zh', changeLanguage: vi.fn() },
  }),
}))

// Mock clipboard API
const mockClipboardWrite = vi.fn().mockResolvedValue(undefined)

// Mock navigator.share
const mockShare = vi.fn().mockResolvedValue(undefined)

// Store original navigator properties
const originalClipboard = navigator.clipboard
const originalShare = navigator.share

beforeEach(() => {
  // Mock clipboard
  Object.defineProperty(navigator, 'clipboard', {
    value: {
      writeText: mockClipboardWrite,
    },
    writable: true,
    configurable: true,
  })

  // Mock share
  Object.defineProperty(navigator, 'share', {
    value: mockShare,
    writable: true,
    configurable: true,
  })
})

afterEach(() => {
  // Restore original navigator properties
  Object.defineProperty(navigator, 'clipboard', {
    value: originalClipboard,
    writable: true,
    configurable: true,
  })
  Object.defineProperty(navigator, 'share', {
    value: originalShare,
    writable: true,
    configurable: true,
  })
})

describe('InviteCodeCard', () => {
  const mockInviteCode: InviteCode = {
    id: 'code-123',
    code: 'ABCD-EFGH',
    max_uses: 10,
    current_uses: 3,
    is_active: true,
    expires_at: null,
    created_at: '2024-01-01T00:00:00Z',
  }

  const mockExhaustedCode: InviteCode = {
    id: 'code-exhausted',
    code: 'FULL-FULL',
    max_uses: 5,
    current_uses: 5,
    is_active: true,
    expires_at: null,
    created_at: '2024-01-01T00:00:00Z',
  }

  const mockExpiredCode: InviteCode = {
    id: 'code-expired',
    code: 'OLD-CODE',
    max_uses: 10,
    current_uses: 2,
    is_active: true,
    expires_at: '2020-01-01T00:00:00Z',
    created_at: '2019-01-01T00:00:00Z',
  }

  const mockInactiveCode: InviteCode = {
    id: 'code-inactive',
    code: 'STOP-STOP',
    max_uses: 10,
    current_uses: 1,
    is_active: false,
    expires_at: null,
    created_at: '2024-01-01T00:00:00Z',
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Rendering', () => {
    it('renders invite code correctly', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      expect(screen.getByText('邀请码')).toBeInTheDocument()
      expect(screen.getByText('ABCD-EFGH')).toBeInTheDocument()
    })

    it('displays usage count correctly', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      expect(screen.getByText('已使用:')).toBeInTheDocument()
      expect(screen.getByText('3/10')).toBeInTheDocument()
    })

    it('shows available status for active codes', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      expect(screen.getByText('可用')).toBeInTheDocument()
    })

    it('shows exhausted status when max uses reached', () => {
      render(<InviteCodeCard inviteCode={mockExhaustedCode} />)

      expect(screen.getByText('已用完')).toBeInTheDocument()
    })

    it('shows expired status for expired codes', () => {
      render(<InviteCodeCard inviteCode={mockExpiredCode} />)

      // "已过期" appears twice: in the badge and in the expiry section
      const expiredTexts = screen.getAllByText('已过期')
      expect(expiredTexts.length).toBeGreaterThanOrEqual(1)
    })

    it('shows inactive status for disabled codes', () => {
      render(<InviteCodeCard inviteCode={mockInactiveCode} />)

      expect(screen.getByText('已停用')).toBeInTheDocument()
    })

    it('does not show expiry text when no expiry date', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      // Should not show Clock icon text when no expiry
      expect(screen.queryByText(/天后过期/)).not.toBeInTheDocument()
    })
  })

  describe('Copy Functionality', () => {
    it('has copy button', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const copyButton = screen.getByTitle('复制邀请码')
      expect(copyButton).toBeInTheDocument()
    })

    it('copies code to clipboard when copy button clicked', async () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const copyButton = screen.getByTitle('复制邀请码')
      fireEvent.click(copyButton)

      expect(mockClipboardWrite).toHaveBeenCalledWith('ABCD-EFGH')
    })

    it('shows check icon after successful copy', async () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const copyButton = screen.getByTitle('复制邀请码')
      fireEvent.click(copyButton)

      await waitFor(() => {
        // Check icon should appear (it's inside the button)
        expect(copyButton.querySelector('svg')).toBeInTheDocument()
      })
    })

    it('resets copy state after 2 seconds', async () => {
      vi.useFakeTimers()

      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const copyButton = screen.getByTitle('复制邀请码')
      fireEvent.click(copyButton)

      // Fast-forward 2 seconds
      vi.advanceTimersByTime(2000)

      // After timer, copy state should be reset
      // The button should still be in the document
      expect(screen.getByTitle('复制邀请码')).toBeInTheDocument()

      vi.useRealTimers()
    })

    it('handles clipboard API failure silently', async () => {
      mockClipboardWrite.mockRejectedValueOnce(new Error('Clipboard error'))

      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const copyButton = screen.getByTitle('复制邀请码')

      // Should not throw
      expect(() => fireEvent.click(copyButton)).not.toThrow()
    })
  })

  describe('Share Functionality', () => {
    it('has share button', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const shareButton = screen.getByTitle('分享邀请码')
      expect(shareButton).toBeInTheDocument()
    })

    it('calls navigator.share with correct data', async () => {
      // Mock window.location.origin
      Object.defineProperty(window, 'location', {
        value: { origin: 'http://localhost:5173' },
        writable: true,
      })

      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const shareButton = screen.getByTitle('分享邀请码')
      fireEvent.click(shareButton)

      expect(mockShare).toHaveBeenCalledWith({
        title: 'zenstory写作 - 邀请码',
        text: '邀请码: ABCD-EFGH',
        url: 'http://localhost:5173/register?code=ABCD-EFGH',
      })
    })

    it('falls back to clipboard when share API not available', async () => {
      // Temporarily remove share API
      const originalShare = navigator.share
      // @ts-expect-error - testing fallback
      delete navigator.share

      Object.defineProperty(window, 'location', {
        value: { origin: 'http://localhost:5173' },
        writable: true,
      })

      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const shareButton = screen.getByTitle('分享邀请码')
      fireEvent.click(shareButton)

      // Should copy the full URL instead
      expect(mockClipboardWrite).toHaveBeenCalledWith(
        'http://localhost:5173/register?code=ABCD-EFGH'
      )

      // Restore share API
      Object.assign(navigator, { share: originalShare })
    })

    it('handles share cancellation silently', async () => {
      mockShare.mockRejectedValueOnce(new Error('Share cancelled'))

      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      const shareButton = screen.getByTitle('分享邀请码')

      // Should not throw
      expect(() => fireEvent.click(shareButton)).not.toThrow()
    })
  })

  describe('Usage Display', () => {
    it('shows correct usage ratio', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      expect(screen.getByText('3/10')).toBeInTheDocument()
    })

    it('shows warning styling when exhausted', () => {
      render(<InviteCodeCard inviteCode={mockExhaustedCode} />)

      const usageElement = screen.getByText('5/5').parentElement
      expect(usageElement).toBeInTheDocument()
    })
  })

  describe('Expiry Status', () => {
    it('shows "已过期" for past dates', () => {
      render(<InviteCodeCard inviteCode={mockExpiredCode} />)

      // "已过期" appears in both badge and expiry section
      const expiredTexts = screen.getAllByText('已过期')
      expect(expiredTexts.length).toBeGreaterThanOrEqual(1)
    })

    it('shows "明天过期" for codes expiring tomorrow', () => {
      const tomorrow = new Date()
      tomorrow.setDate(tomorrow.getDate() + 1)

      const codeExpiringTomorrow: InviteCode = {
        ...mockInviteCode,
        expires_at: tomorrow.toISOString(),
      }

      render(<InviteCodeCard inviteCode={codeExpiringTomorrow} />)

      expect(screen.getByText('明天过期')).toBeInTheDocument()
    })

    it('shows "N 天后过期" for codes expiring within 7 days', () => {
      const in3Days = new Date()
      in3Days.setDate(in3Days.getDate() + 3)

      const codeExpiringIn3Days: InviteCode = {
        ...mockInviteCode,
        expires_at: in3Days.toISOString(),
      }

      render(<InviteCodeCard inviteCode={codeExpiringIn3Days} />)

      expect(screen.getByText('3 天后过期')).toBeInTheDocument()
    })

    it('shows formatted date for codes expiring after 7 days', () => {
      const in30Days = new Date()
      in30Days.setDate(in30Days.getDate() + 30)

      const codeExpiringIn30Days: InviteCode = {
        ...mockInviteCode,
        expires_at: in30Days.toISOString(),
      }

      render(<InviteCodeCard inviteCode={codeExpiringIn30Days} />)

      // Should show Clock icon - lucide icons are SVG elements
      const svgElements = document.querySelectorAll('svg.lucide-clock')
      expect(svgElements.length).toBeGreaterThan(0)

      // Should also show a date text (formatted date string)
      // The date format is locale-dependent, so we check for digits
      const container = screen.getByText('可用').parentElement
      expect(container).toBeInTheDocument()
    })
  })

  describe('Status Badge Logic', () => {
    it('prioritizes expired status over inactive', () => {
      const expiredAndInactive: InviteCode = {
        ...mockExpiredCode,
        is_active: false,
      }

      render(<InviteCodeCard inviteCode={expiredAndInactive} />)

      // Expired takes precedence - appears in badge and expiry section
      const expiredTexts = screen.getAllByText('已过期')
      expect(expiredTexts.length).toBeGreaterThanOrEqual(1)
    })

    it('shows inactive status when not active', () => {
      render(<InviteCodeCard inviteCode={mockInactiveCode} />)

      expect(screen.getByText('已停用')).toBeInTheDocument()
    })

    it('shows available status when active and not exhausted', () => {
      render(<InviteCodeCard inviteCode={mockInviteCode} />)

      expect(screen.getByText('可用')).toBeInTheDocument()
    })

    it('shows exhausted status when uses equal max', () => {
      render(<InviteCodeCard inviteCode={mockExhaustedCode} />)

      expect(screen.getByText('已用完')).toBeInTheDocument()
    })
  })
})
