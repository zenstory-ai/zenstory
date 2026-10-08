import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { RedeemCodeModal } from '../subscription/RedeemCodeModal'
import { subscriptionApi } from '../../lib/subscriptionApi'
import { handleApiError } from '../../lib/errorHandler'
import { trackEvent } from '../../lib/analytics'

vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    redeemCode: vi.fn(),
  },
}))

vi.mock('../../lib/errorHandler', () => ({
  handleApiError: vi.fn((err: unknown) =>
    err instanceof Error ? err.message : '兑换失败，请检查兑换码'
  ),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, defaultValue: string) => defaultValue,
  }),
}))

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('RedeemCodeModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows normalized error message from unified error handler', async () => {
    const mockRedeemCode = vi.mocked(subscriptionApi.redeemCode)
    const mockHandleApiError = vi.mocked(handleApiError)

    const sourceError = new Error('ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED')
    mockRedeemCode.mockRejectedValue(sourceError)
    mockHandleApiError.mockReturnValue(
      '今日 AI 对话次数已用尽，请明天再试或升级套餐'
    )

    render(<RedeemCodeModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    })

    fireEvent.change(screen.getByPlaceholderText('ERG-XXXX-XXXX-XXXXXXXX'), {
      target: { value: 'ERG-ABCD-1234-ABCDEFGH' },
    })
    const form = document.querySelector('form')
    expect(form).toBeTruthy()
    fireEvent.submit(form!)

    await waitFor(() => {
      expect(mockHandleApiError).toHaveBeenCalled()
    })
    expect(
      screen.getByText('今日 AI 对话次数已用尽，请明天再试或升级套餐')
    ).toBeInTheDocument()
  })

  it('passes optional attribution source when redeeming', async () => {
    const mockRedeemCode = vi.mocked(subscriptionApi.redeemCode)
    mockRedeemCode.mockResolvedValue({
      success: true,
      message: '兑换成功！',
    })

    render(
      <RedeemCodeModal
        isOpen={true}
        onClose={vi.fn()}
        source="chat_quota_blocked"
      />,
      {
        wrapper: createWrapper(),
      }
    )

    fireEvent.change(screen.getByPlaceholderText('ERG-XXXX-XXXX-XXXXXXXX'), {
      target: { value: 'ERG-ABCD-1234-ABCDEFGH' },
    })
    const form = document.querySelector('form')
    expect(form).toBeTruthy()
    fireEvent.submit(form!)

    await waitFor(() => {
      expect(mockRedeemCode).toHaveBeenCalledWith(
        'ERG-ABCD-1234-ABCDEFGH',
        'chat_quota_blocked'
      )
    })
  })

  it('tracks redemption results without sending the code', async () => {
    const mockRedeemCode = vi.mocked(subscriptionApi.redeemCode)
    mockRedeemCode.mockResolvedValueOnce({ success: true, message: 'ok', tier: 'pro', duration_days: 7 })

    render(<RedeemCodeModal isOpen={true} onClose={vi.fn()} source="billing_header_upgrade" />, {
      wrapper: createWrapper(),
    })
    const input = screen.getByPlaceholderText('ERG-XXXX-XXXX-XXXXXXXX')
    fireEvent.change(input, { target: { value: 'ERG-PRO7D-1234-ABCDEFGH' } })
    fireEvent.submit(document.querySelector('form')!)
    await waitFor(() => {
      expect(trackEvent).toHaveBeenCalledWith('redeem_code_succeeded', {
        tier: 'pro', duration_days: 7, source: 'billing_header_upgrade',
      })
    })

    mockRedeemCode.mockRejectedValueOnce(new Error('Redemption failed'))
    fireEvent.change(input, { target: { value: 'ERG-PRO7D-1234-ABCDEFGH' } })
    fireEvent.submit(document.querySelector('form')!)
    await waitFor(() => {
      expect(trackEvent).toHaveBeenCalledWith('redeem_code_failed', {
        reason: 'unknown', source: 'billing_header_upgrade',
      })
    })

    fireEvent.change(input, { target: { value: 'not-a-code' } })
    fireEvent.submit(document.querySelector('form')!)
    expect(trackEvent).toHaveBeenCalledWith('redeem_code_failed', {
      reason: 'invalid_format', source: 'billing_header_upgrade',
    })
    const serialized = JSON.stringify(vi.mocked(trackEvent).mock.calls)
    expect(serialized).not.toContain('ERG-PRO7D')
  })

  it('celebrates a paid activation and offers a single way forward', async () => {
    const mockRedeemCode = vi.mocked(subscriptionApi.redeemCode)
    mockRedeemCode.mockResolvedValueOnce({ success: true, message: 'ok', tier: 'pro', duration_days: 30 })
    const onClose = vi.fn()

    render(<RedeemCodeModal isOpen={true} onClose={onClose} />, { wrapper: createWrapper() })
    fireEvent.change(screen.getByPlaceholderText('ERG-XXXX-XXXX-XXXXXXXX'), {
      target: { value: 'ERG-PRO30-1234-ABCDEFGH' },
    })
    fireEvent.submit(document.querySelector('form')!)

    expect(await screen.findByTestId('celebration-burst')).toBeInTheDocument()
    expect(screen.getByRole('status')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '开始使用' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
