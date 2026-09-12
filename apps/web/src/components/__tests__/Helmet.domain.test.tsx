import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SEOHelmet } from '../Helmet'

const mockSeoConfig = {
  title: 'Accurate document title',
  description: 'Accurate document description',
  canonical: 'https://zenstory.ai/docs/guide',
  noindex: false,
  og: {
    type: 'article',
    title: 'Accurate document OG title',
    description: 'Accurate document OG description',
    image: 'https://zenstory.ai/brand/docs-card.png',
  },
}

vi.mock('react-helmet-async', () => ({
  Helmet: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}))

vi.mock('../../providers/SEOProvider', () => ({
  useSEO: () => ({ seoConfig: mockSeoConfig }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ i18n: { language: 'en-US' } }),
}))

describe('SEOHelmet domain metadata', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/docs/guide')
  })

  it('emits exactly one canonical and matching og:url for a docs leaf', () => {
    render(<SEOHelmet />)

    expect(document.querySelectorAll('link[rel="canonical"]')).toHaveLength(1)
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute('href', mockSeoConfig.canonical)
    expect(document.querySelectorAll('meta[property="og:url"]')).toHaveLength(1)
    expect(document.querySelector('meta[property="og:url"]')).toHaveAttribute('content', mockSeoConfig.canonical)
    expect(document.querySelector('meta[property="og:site_name"]')).toHaveAttribute('content', 'ZenStory AI')
    expect(document.querySelector('meta[property="og:type"]')).toHaveAttribute('content', 'article')
    expect(document.querySelector('meta[property="og:title"]')).toHaveAttribute('content', 'Accurate document OG title')
    expect(document.querySelector('meta[property="og:description"]')).toHaveAttribute('content', 'Accurate document OG description')
    expect(document.querySelector('meta[property="og:image"]')).toHaveAttribute('content', 'https://zenstory.ai/brand/docs-card.png')
    expect(document.title).toBe('Accurate document title')
  })
})
