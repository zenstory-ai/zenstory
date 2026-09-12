import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { HelmetProvider } from 'react-helmet-async'
import { MemoryRouter } from 'react-router-dom'
import { SEOHelmet } from '../Helmet'
import { SEOProvider } from '../../providers/SEOProvider'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ i18n: { language: 'en-US' } }),
}))

describe('SEOHelmet static metadata hydration', () => {
  beforeEach(() => {
    document.head.innerHTML = ''
    window.history.replaceState({}, '', '/docs/guide')
  })

  it('reconciles generated docs metadata without dropping route-specific Open Graph tags', async () => {
    const canonical = `${window.location.origin}/docs/guide`
    document.head.innerHTML = `
      <title data-rh="true">Generated guide title</title>
      <meta name="description" content="Generated guide description" data-rh="true">
      <link rel="canonical" href="${canonical}" data-rh="true">
      <meta property="og:url" content="${canonical}" data-rh="true">
      <meta property="og:site_name" content="ZenStory AI" data-rh="true">
      <meta property="og:type" content="article" data-rh="true">
      <meta property="og:title" content="Generated guide OG title" data-rh="true">
      <meta property="og:description" content="Generated guide OG description" data-rh="true">
      <meta property="og:image" content="https://zenstory.ai/brand/generated-guide.png" data-rh="true">
      <script type="application/ld+json" data-rh="true">{"@context":"https://schema.org","@type":"TechArticle","headline":"Generated guide title"}</script>
    `

    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={['/docs/guide']}>
          <SEOProvider>
            <SEOHelmet />
          </SEOProvider>
        </MemoryRouter>
      </HelmetProvider>,
    )

    await waitFor(() => {
      expect(document.querySelectorAll('link[rel="canonical"]')).toHaveLength(1)
      expect(document.querySelectorAll('meta[property="og:url"]')).toHaveLength(1)
      expect(document.querySelectorAll('meta[property="og:site_name"]')).toHaveLength(1)
      expect(document.querySelectorAll('meta[property="og:type"]')).toHaveLength(1)
      expect(document.querySelectorAll('meta[property="og:title"]')).toHaveLength(1)
      expect(document.querySelectorAll('meta[property="og:description"]')).toHaveLength(1)
      expect(document.querySelectorAll('meta[property="og:image"]')).toHaveLength(1)
      expect(document.querySelector('meta[property="og:site_name"]')).toHaveAttribute('content', 'ZenStory AI')
      expect(document.querySelector('meta[property="og:type"]')).toHaveAttribute('content', 'article')
      expect(document.querySelector('meta[property="og:title"]')).toHaveAttribute('content', 'Generated guide OG title')
      expect(document.querySelector('meta[property="og:description"]')).toHaveAttribute('content', 'Generated guide OG description')
      expect(document.querySelector('meta[property="og:image"]')).toHaveAttribute('content', 'https://zenstory.ai/brand/generated-guide.png')
    })
  })

  it('inserts the complete Open Graph set through the real Helmet implementation', async () => {
    window.history.replaceState({}, '', '/privacy-policy')

    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={['/privacy-policy']}>
          <SEOProvider>
            <SEOHelmet />
          </SEOProvider>
        </MemoryRouter>
      </HelmetProvider>,
    )

    await waitFor(() => {
      expect(document.querySelector('meta[property="og:url"]')).toHaveAttribute(
        'content',
        `${window.location.origin}/privacy-policy`,
      )
      expect(document.querySelector('meta[property="og:site_name"]')).toHaveAttribute('content', 'ZenStory AI')
      expect(document.querySelector('meta[property="og:type"]')).toHaveAttribute('content', 'website')
      expect(document.querySelector('meta[property="og:title"]')).toHaveAttribute('content', 'Privacy Policy - zenstory')
      expect(document.querySelector('meta[property="og:description"]')).toHaveAttribute('content', 'Privacy Policy of zenstory')
      expect(document.querySelector('meta[property="og:image"]')).toHaveAttribute(
        'content',
        'https://zenstory.ai/brand/zenstory-ai-mark.svg',
      )
    })
  })
})
