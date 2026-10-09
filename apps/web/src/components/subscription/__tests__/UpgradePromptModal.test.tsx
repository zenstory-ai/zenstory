import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UpgradePromptModal } from '../UpgradePromptModal'
import { subscriptionApi, subscriptionQueryKeys } from '../../../lib/subscriptionApi'
import { trackUpgradeExpose } from '../../../lib/upgradeAnalytics'

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

function renderLimitPrompt(tier: 'free' | 'pro') {
  vi.mocked(subscriptionApi.getStatus).mockResolvedValue({ tier } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(subscriptionQueryKeys.status(), { tier })
  render(
    <QueryClientProvider client={client}>
      <UpgradePromptModal
        open
        onClose={vi.fn()}
        title="文件版本额度已达上限"
        description="正文照常保存，只是不再生成新版本。开通 Pro 可以保留更多版本。"
        primaryLabel="开通 Pro"
        onPrimary={vi.fn()}
        source="file_version_quota_blocked"
      />
    </QueryClientProvider>,
  )
}

describe('UpgradePromptModal', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('tells a Pro author the limit is reached instead of offering Pro, and keeps it out of the funnel', async () => {
    renderLimitPrompt('pro')

    expect(await screen.findByText('dashboard:billing.paidLimitReached')).toBeInTheDocument()
    expect(screen.queryByText('开通 Pro')).not.toBeInTheDocument()
    expect(trackUpgradeExpose).not.toHaveBeenCalled()
  })

  it('still offers the upgrade to a free author and records the exposure', async () => {
    renderLimitPrompt('free')

    expect(await screen.findByText('开通 Pro')).toBeInTheDocument()
    expect(trackUpgradeExpose).toHaveBeenCalledWith('file_version_quota_blocked', 'modal')
  })
})
