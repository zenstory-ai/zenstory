import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UpgradePromptModal } from '../UpgradePromptModal'
import { subscriptionApi, subscriptionQueryKeys } from '../../../lib/subscriptionApi'
import { trackUpgradeClick, trackUpgradeExpose } from '../../../lib/upgradeAnalytics'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('../../../lib/upgradeAnalytics', () => ({
  trackUpgradeExpose: vi.fn(),
  trackUpgradeClick: vi.fn(),
}))
vi.mock('../../../lib/subscriptionApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../lib/subscriptionApi')>()
  return { ...actual, subscriptionApi: { ...actual.subscriptionApi, getStatus: vi.fn() } }
})

function renderLimitPrompt(
  tier: 'free' | 'pro',
  { cached = true, paidDescription }: { cached?: boolean; paidDescription?: string } = {},
) {
  vi.mocked(subscriptionApi.getStatus).mockResolvedValue({ tier } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  if (cached) client.setQueryData(subscriptionQueryKeys.status(), { tier })
  render(
    <QueryClientProvider client={client}>
      <UpgradePromptModal
        open
        onClose={vi.fn()}
        title="文件版本额度已达上限"
        description="正文照常保存，只是不再生成新版本。开通 Pro 可以保留更多版本。"
        paidDescription={paidDescription}
        primaryLabel="开通 Pro"
        onPrimary={vi.fn()}
        source="file_version_quota_blocked"
      />
    </QueryClientProvider>,
  )
}

describe('UpgradePromptModal', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal("IntersectionObserver", undefined); })
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); })

  it('tells a Pro author the limit is reached instead of offering Pro, and keeps it out of the funnel', async () => {
    renderLimitPrompt('pro')

    expect(await screen.findByText('dashboard:billing.paidLimitReached')).toBeInTheDocument()
    expect(screen.queryByText('开通 Pro')).not.toBeInTheDocument()
    expect(trackUpgradeExpose).not.toHaveBeenCalled()
  })

  it('still offers the upgrade to a free author and records the exposure', async () => {
    renderLimitPrompt('free')

    expect(await screen.findByText('开通 Pro')).toBeInTheDocument()
    expect(trackUpgradeExpose).toHaveBeenCalledWith('file_version_quota_blocked', 'modal', { placement: 'limit-dialog' })
  })

  it('tells a Pro author what still helps with this limit when the caller knows it', async () => {
    renderLimitPrompt('pro', { paidDescription: '删掉不用的技能就能腾出名额。' })

    expect(await screen.findByText('删掉不用的技能就能腾出名额。')).toBeInTheDocument()
    expect(screen.queryByText('dashboard:billing.paidLimitReached')).not.toBeInTheDocument()
    expect(screen.queryByText('开通 Pro')).not.toBeInTheDocument()
  })

  it('shows a neutral state, never the free "开通 Pro", until a paid plan is known', async () => {
    renderLimitPrompt('pro', { cached: false })

    // First frame: plan unknown, no upgrade copy or button.
    expect(screen.getByTestId('upgrade-prompt-pending')).toBeInTheDocument()
    expect(screen.queryByText('开通 Pro')).not.toBeInTheDocument()
    expect(screen.queryByText(/开通 Pro 可以保留更多版本/)).not.toBeInTheDocument()

    expect(await screen.findByText('dashboard:billing.paidLimitReached')).toBeInTheDocument()
    expect(screen.queryByText('开通 Pro')).not.toBeInTheDocument()
    expect(trackUpgradeExpose).not.toHaveBeenCalled()
  })

  it('shows the upgrade to a free author once the plan arrives', async () => {
    renderLimitPrompt('free', { cached: false })

    expect(screen.getByTestId('upgrade-prompt-pending')).toBeInTheDocument()
    expect(await screen.findByText('开通 Pro')).toBeInTheDocument()
    expect(screen.queryByTestId('upgrade-prompt-pending')).not.toBeInTheDocument()
  })

  it('resolves promptId into source, tracking destinations, and default billing/pricing navigation', async () => {
    const assignMock = vi.fn()
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, assign: assignMock },
      writable: true,
      configurable: true,
    })

    try {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      client.setQueryData(subscriptionQueryKeys.status(), { tier: 'free' })
      const onClose = vi.fn()

      render(
        <QueryClientProvider client={client}>
          <UpgradePromptModal
            open
            onClose={onClose}
            promptId="project_quota_blocked"
            title="项目上限"
            description="升级 Pro"
            primaryLabel="开通 Pro"
            secondaryLabel="查看套餐"
          />
        </QueryClientProvider>,
      )

      expect(trackUpgradeExpose).toHaveBeenCalledWith('project_quota_blocked', 'modal', { placement: 'limit-dialog' })

      fireEvent.click(await screen.findByRole('button', { name: '开通 Pro' }))
      expect(trackUpgradeClick).toHaveBeenCalledWith('project_quota_blocked', 'primary', 'billing', 'modal')
      expect(assignMock).toHaveBeenCalledWith('/dashboard/billing?source=project_quota_blocked')
      expect(onClose).toHaveBeenCalledTimes(1)

      fireEvent.click(screen.getByRole('button', { name: '查看套餐' }))
      expect(trackUpgradeClick).toHaveBeenCalledWith('project_quota_blocked', 'secondary', 'pricing', 'modal')
      expect(assignMock).toHaveBeenCalledWith('/pricing?source=project_quota_blocked')
    } finally {
      Object.defineProperty(window, 'location', {
        value: originalLocation,
        writable: true,
        configurable: true,
      })
    }
  })

  it('lets callers override source and onPrimary while keeping promptId default secondary navigation', async () => {
    const assignMock = vi.fn()
    const onPrimary = vi.fn()
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, assign: assignMock },
      writable: true,
      configurable: true,
    })

    try {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      client.setQueryData(subscriptionQueryKeys.status(), { tier: 'free' })

      render(
        <QueryClientProvider client={client}>
          <UpgradePromptModal
            open
            onClose={vi.fn()}
            promptId="material_upload_quota_blocked"
            source="custom_materials_source"
            primaryDestination="redeem"
            title="素材库"
            description="兑换码开通"
            primaryLabel="兑换码开通"
            onPrimary={onPrimary}
            secondaryLabel="查看套餐对比"
          />
        </QueryClientProvider>,
      )

      fireEvent.click(await screen.findByRole('button', { name: '兑换码开通' }))
      expect(trackUpgradeClick).toHaveBeenCalledWith('custom_materials_source', 'primary', 'redeem', 'modal')
      expect(onPrimary).toHaveBeenCalledTimes(1)
      expect(assignMock).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: '查看套餐对比' }))
      expect(trackUpgradeClick).toHaveBeenCalledWith('custom_materials_source', 'secondary', 'pricing', 'modal')
      expect(assignMock).toHaveBeenCalledWith('/pricing?source=custom_materials_source')
    } finally {
      Object.defineProperty(window, 'location', {
        value: originalLocation,
        writable: true,
        configurable: true,
      })
    }
  })
})
