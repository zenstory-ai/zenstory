import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('dashboardInspirationSource', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.unstubAllGlobals()
  })

  it('normalizes locales and loads sanitized bundles with caching', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        version: 1,
        locale: 'en',
        generated_at: '2026-04-07T00:00:00Z',
        homepage_priority: {
          novel: [{ id: 'n-hero', title: 'Hero', hook: 'Hook', tags: ['epic'], source: 'manual' }],
          short: [{ id: 's-hero', title: 'Short', hook: 'Hook', tags: ['brief'], source: 'manual' }],
          screenplay: [{ id: 'sp-hero', title: 'Scene', hook: 'Hook', tags: ['cinema'], source: 'manual' }],
        },
        items: {
          novel: [{ id: 'n-hero', title: 'Hero', hook: 'Hook', tags: ['epic'], source: 'manual' }],
          short: [{ id: 's-hero', title: 'Short', hook: 'Hook', tags: ['brief'], source: 'manual' }],
          screenplay: [{ id: 'sp-hero', title: 'Scene', hook: 'Hook', tags: ['cinema'], source: 'manual' }],
        },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const mod = await import('../dashboardInspirationSource')

    expect(mod.normalizeDashboardInspirationLocale('en-US')).toBe('en')
    expect(mod.normalizeDashboardInspirationLocale('zh-CN')).toBe('zh')

    const first = await mod.loadDashboardInspirationBundle('en')
    const second = await mod.loadDashboardInspirationBundle('en')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(first).toEqual(second)
    expect(first).toMatchObject({
      locale: 'en',
      items: {
        novel: [{ id: 'n-hero', title: 'Hero', source: 'manual' }],
      },
      homepagePriority: {
        novel: [{ id: 'n-hero' }],
      },
    })
  })

  it('falls back to slices of items when homepage_priority is missing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          version: 1,
          locale: 'zh',
          generated_at: '2026-04-07T00:00:00Z',
          items: {
            novel: [
              { id: 'n1', title: 'One', hook: 'H', tags: ['a'], source: 'seed' },
              { id: 'n2', title: 'Two', hook: 'H', tags: ['b'], source: 'seed' },
            ],
            short: [{ id: 's1', title: 'Short', hook: 'H', tags: ['c'], source: 'seed' }],
            screenplay: [{ id: 'sp1', title: 'Scene', hook: 'H', tags: ['d'], source: 'seed' }],
          },
        }),
      }),
    )

    const mod = await import('../dashboardInspirationSource')
    const bundle = await mod.loadDashboardInspirationBundle('zh')

    expect(bundle?.homepagePriority.novel.map((item) => item.id)).toEqual(['n1', 'n2'])
  })

  it('returns null for invalid responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          version: 999,
          locale: 'en',
          items: {},
        }),
      }),
    )

    const mod = await import('../dashboardInspirationSource')

    await expect(mod.loadDashboardInspirationBundle('en')).resolves.toBeNull()
  })

  it('returns null for non-ok responses and evicts failed cache entries so retries recover', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 502 })
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          version: 1,
          locale: 'en',
          generated_at: '2026-04-07T00:00:00Z',
          items: {
            novel: [{ id: 'n-recovered', title: 'Recovered', hook: 'H', tags: [], source: 'seed' }],
            short: [{ id: 's-recovered', title: 'Short', hook: 'H', tags: [], source: 'seed' }],
            screenplay: [{ id: 'sp-recovered', title: 'Scene', hook: 'H', tags: [], source: 'seed' }],
          },
        }),
      })
    vi.stubGlobal('fetch', fetchMock)

    const mod = await import('../dashboardInspirationSource')

    await expect(mod.loadDashboardInspirationBundle('en')).resolves.toBeNull()
    await expect(mod.loadDashboardInspirationBundle('en')).rejects.toThrow('Failed to fetch')
    const recovered = await mod.loadDashboardInspirationBundle('en')
    expect(recovered?.items.novel[0]?.id).toBe('n-recovered')
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})
