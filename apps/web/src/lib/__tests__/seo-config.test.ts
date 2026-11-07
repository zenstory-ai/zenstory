import { describe, expect, it } from 'vitest'
import { pageSEOConfig } from '../seo-config'
import zhDashboard from '../../../public/locales/zh/dashboard.json'
import enDashboard from '../../../public/locales/en/dashboard.json'

describe('pageSEOConfig', () => {
  it('names the billing tab like the page heading and sidebar in both languages', () => {
    const billing = pageSEOConfig['/dashboard/billing']
    expect(billing.zh.title).toBe(`${zhDashboard.billing.title} - ZenStory`)
    expect(billing.en.title).toBe(`${enDashboard.billing.title} - ZenStory`)
  })

  it('defines public bilingual SEO metadata for /status', () => {
    const status = pageSEOConfig['/status']
    expect(status).toBeDefined()
    expect(status.zh.title).toBe('Agent 状态 - ZenStory')
    expect(status.en.title).toBe('Agent Status - ZenStory')
    expect(status.zh.noindex).toBe(false)
    expect(status.en.noindex).toBe(false)
  })
})
