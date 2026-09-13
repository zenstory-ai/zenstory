import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let mockLanguage = 'en'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: {
      language: mockLanguage,
    },
  }),
}))

import { useDocsSearch } from '../useDocsSearch'

describe('useDocsSearch', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockLanguage = 'en'
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('returns an idle empty state for blank queries', () => {
    const { result } = renderHook(() => useDocsSearch({ query: '   ' }))

    expect(result.current.results).toEqual([])
    expect(result.current.isSearching).toBe(false)
  })

  it('finds exact bilingual matches and includes parent metadata', async () => {
    mockLanguage = 'zh'

    const { result } = renderHook(() =>
      useDocsSearch({
        query: '网页写作快速入门',
        debounceMs: 10,
      }),
    )

    await act(async () => {
      vi.advanceTimersByTime(10)
      await Promise.resolve()
    })

    expect(result.current.isSearching).toBe(false)

    expect(result.current.results[0]).toMatchObject({
      title: 'Quick Start',
      titleZh: '网页写作快速入门',
      path: '/docs/getting-started/quick-start',
      parentTitle: 'Getting Started',
      parentTitleZh: '快速入门',
      score: 4,
    })
  })

  it('filters out category routes and only returns leaf docs', async () => {
    const { result } = renderHook(() =>
      useDocsSearch({
        query: 'quick',
        debounceMs: 10,
      }),
    )

    await act(async () => {
      vi.advanceTimersByTime(10)
      await Promise.resolve()
    })

    expect(result.current.isSearching).toBe(false)

    expect(result.current.results.some((item) => item.path === '/docs/getting-started')).toBe(false)
    expect(
      result.current.results.some((item) => item.path === '/docs/getting-started/quick-start'),
    ).toBe(true)
  })

  it('clearSearch cancels the pending debounce and resets local state', () => {
    const { result } = renderHook(() =>
      useDocsSearch({
        query: 'quick',
        debounceMs: 100,
      }),
    )

    expect(result.current.isSearching).toBe(true)

    act(() => {
      result.current.clearSearch()
      vi.advanceTimersByTime(100)
    })

    expect(result.current.results).toEqual([])
    expect(result.current.isSearching).toBe(false)
  })

  it('sorts same-score matches by the current language title', async () => {
    const { result } = renderHook(() =>
      useDocsSearch({
        query: 'file',
        debounceMs: 10,
      }),
    )

    await act(async () => {
      vi.advanceTimersByTime(10)
      await Promise.resolve()
    })

    expect(result.current.isSearching).toBe(false)

    expect(result.current.results.slice(0, 2).map((item) => item.title)).toEqual([
      'File Tree',
      'File Types',
    ])
  })
})
