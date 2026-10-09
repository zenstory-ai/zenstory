import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockApiGet = vi.fn()
const mockApiPost = vi.fn()

vi.mock('../apiClient', () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}))

// Host-local day and Beijing day differ (e.g. a PDT evening): stats use Beijing.
vi.mock('../dateUtils', () => ({
  getLocalDateString: vi.fn(() => '2026-03-05'),
  getBeijingDateString: vi.fn(() => '2026-03-06'),
}))

import { writingStatsApi } from '../writingStatsApi'

describe('writingStatsApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('appends the Beijing date as client_date for dashboard stats by default', async () => {
    mockApiGet.mockResolvedValue({})

    await writingStatsApi.getDashboardStats('project-1')

    expect(mockApiGet).toHaveBeenCalledWith(
      '/api/v1/projects/project-1/stats?client_date=2026-03-06'
    )
  })

  it('fetches activation guide from dedicated endpoint', async () => {
    mockApiGet.mockResolvedValue({})

    await writingStatsApi.getActivationGuide()

    expect(mockApiGet).toHaveBeenCalledWith('/api/v1/activation/guide')
  })

  it('appends the Beijing date as client_date for word trend with custom params', async () => {
    mockApiGet.mockResolvedValue({})

    await writingStatsApi.getWordCountTrend('project-1', { period: 'weekly', days: 84 })

    expect(mockApiGet).toHaveBeenCalledWith(
      '/api/v1/projects/project-1/stats/word-count-trend?period=weekly&days=84&client_date=2026-03-06'
    )
  })

  it('fills stats_date from the Beijing date when recording stats', async () => {
    mockApiPost.mockResolvedValue({})

    await writingStatsApi.recordStats('project-1', {
      word_count: 1234,
      words_added: 12,
      words_deleted: 3,
    })

    expect(mockApiPost).toHaveBeenCalledWith(
      '/api/v1/projects/project-1/stats/record',
      expect.objectContaining({
        word_count: 1234,
        words_added: 12,
        words_deleted: 3,
        stats_date: '2026-03-06',
      })
    )
  })

  it('keeps explicit stats_date when provided', async () => {
    mockApiPost.mockResolvedValue({})

    await writingStatsApi.recordStats('project-1', {
      word_count: 2000,
      stats_date: '2026-03-01',
    })

    expect(mockApiPost).toHaveBeenCalledWith(
      '/api/v1/projects/project-1/stats/record',
      expect.objectContaining({
        word_count: 2000,
        stats_date: '2026-03-01',
      })
    )
  })
})
