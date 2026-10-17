import { describe, expect, it } from 'vitest'
import { getBoundaryDestination, getPublicCanonicalUrl, getWorkbenchHomeUrl } from '../site-domain'

describe('getWorkbenchHomeUrl', () => {
  it('returns the matching workbench home from legal pages on both managed hosts', () => {
    expect(getWorkbenchHomeUrl('https://zenstory.ai/privacy-policy')).toBe('https://app.zenstory.ai/')
    expect(getWorkbenchHomeUrl('https://app.zenstory.ai/terms-of-service')).toBe('https://app.zenstory.ai/')
    expect(getWorkbenchHomeUrl('https://geo-preview.zenstory.ai/privacy-policy')).toBe('https://app-preview.zenstory.ai/')
    expect(getWorkbenchHomeUrl('https://app-preview.zenstory.ai/terms-of-service')).toBe('https://app-preview.zenstory.ai/')
    expect(getWorkbenchHomeUrl('http://127.0.0.1:5183/privacy-policy')).toBe('/')
  })
})

describe('getBoundaryDestination', () => {
  it('moves exact app route prefixes from the production site to the app', () => {
    expect(getBoundaryDestination('https://zenstory.ai/project/abc?file=1#selection')).toBe(
      'https://app.zenstory.ai/project/abc?file=1#selection',
    )
    expect(getBoundaryDestination('https://zenstory.ai/auth/callback?error=denied#result')).toBe(
      'https://app.zenstory.ai/auth/callback?error=denied#result',
    )
    expect(getBoundaryDestination('https://zenstory.ai/projects')).toBeNull()
    expect(getBoundaryDestination('https://zenstory.ai/projectile')).toBeNull()
  })

  it('moves public site routes from the app to the matching site host', () => {
    expect(getBoundaryDestination('https://app.zenstory.ai/docs/guide?q=one#install')).toBeNull()
    expect(getBoundaryDestination('https://zenstory.ai/docs/guide?q=one#install')).toBeNull()
    expect(getBoundaryDestination('https://app.zenstory.ai/privacy-policy')).toBeNull()
    expect(getBoundaryDestination('https://app.zenstory.ai/terms-of-service')).toBeNull()
    expect(getBoundaryDestination('https://zenstory.ai/privacy-policy')).toBeNull()
    expect(getBoundaryDestination('https://zenstory.ai/terms-of-service')).toBeNull()
    expect(getBoundaryDestination('https://app.zenstory.ai/')).toBeNull()
  })

  it('keeps production and preview pairs separate', () => {
    expect(getBoundaryDestination('https://geo-preview.zenstory.ai/pricing?plan=pro#checkout')).toBe(
      'https://app-preview.zenstory.ai/pricing?plan=pro#checkout',
    )
    expect(getBoundaryDestination('https://app-preview.zenstory.ai/docs')).toBeNull()
    expect(getBoundaryDestination('https://geo-preview.zenstory.ai/docs')).toBeNull()
  })

  it('does not redirect localhost or foreign origins', () => {
    expect(getBoundaryDestination('http://localhost:5173/docs')).toBeNull()
    expect(getBoundaryDestination('https://evil.example/project/abc')).toBeNull()
  })
})

describe('getPublicCanonicalUrl', () => {
  it('uses the correct public host without query strings or fragments', () => {
    expect(getPublicCanonicalUrl('https://app.zenstory.ai/?utm=campaign#top')).toBe('https://app.zenstory.ai/')
    expect(getPublicCanonicalUrl('https://zenstory.ai/?utm=campaign#top')).toBe('https://zenstory.ai/')
    expect(getPublicCanonicalUrl('https://zenstory.ai/pricing?plan=pro')).toBe('https://app.zenstory.ai/pricing')
    expect(getPublicCanonicalUrl('https://app.zenstory.ai/docs/guide#install')).toBe('https://zenstory.ai/docs/guide')
    expect(getPublicCanonicalUrl('https://geo-preview.zenstory.ai/docs')).toBe('https://zenstory.ai/docs')
    expect(getPublicCanonicalUrl('https://app-preview.zenstory.ai/')).toBe('https://app.zenstory.ai/')
  })
})
