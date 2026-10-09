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
})
