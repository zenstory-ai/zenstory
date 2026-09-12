import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { pathname } = vi.hoisted(() => ({ pathname: { value: '/' } }))

vi.mock('react-router-dom', () => ({
  useLocation: () => ({ pathname: pathname.value }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ i18n: { language: 'en' } }),
}))

import { SEOProvider, useSEO } from '../SEOProvider'

function Consumer() {
  const { seoConfig } = useSEO()
  return (
    <>
      <div data-testid="title">{seoConfig.title}</div>
      <div data-testid="canonical">{seoConfig.canonical}</div>
      <div data-testid="noindex">{String(Boolean(seoConfig.noindex))}</div>
      <div data-testid="schema">{JSON.stringify(seoConfig.schema)}</div>
      <div data-testid="og">{JSON.stringify(seoConfig.og)}</div>
    </>
  )
}

describe('SEOProvider domain metadata', () => {
  beforeEach(() => {
    document.head.innerHTML = ''
    document.body.innerHTML = ''
    window.history.replaceState({}, '', '/')
    pathname.value = '/'
  })

  it('preserves accurate generated docs metadata during hydration', () => {
    pathname.value = '/docs/guide'
    window.history.replaceState({}, '', pathname.value)
    document.title = 'Guide title from markdown · zenstory 文档 | ZenStory AI'
    document.head.insertAdjacentHTML('beforeend', `
      <meta name="description" content="Guide description from markdown" data-rh="true">
      <link rel="canonical" href="${window.location.origin}/docs/guide" data-rh="true">
      <meta property="og:type" content="article" data-rh="true">
      <meta property="og:title" content="Guide OG title from markdown" data-rh="true">
      <meta property="og:description" content="Guide OG description from markdown" data-rh="true">
      <meta property="og:image" content="https://zenstory.ai/brand/docs-card.png" data-rh="true">
      <script type="application/ld+json" data-rh="true">{"@context":"https://schema.org","@type":"TechArticle","headline":"Guide title from markdown"}</script>
    `)

    render(<SEOProvider><Consumer /></SEOProvider>)

    expect(screen.getByTestId('title')).toHaveTextContent('Guide title from markdown')
    expect(screen.getByTestId('canonical')).toHaveTextContent(`${window.location.origin}/docs/guide`)
    expect(screen.getByTestId('schema')).toHaveTextContent('TechArticle')
    expect(screen.getByTestId('og')).toHaveTextContent('Guide OG title from markdown')
    expect(screen.getByTestId('og')).toHaveTextContent('Guide OG description from markdown')
    expect(screen.getByTestId('og')).toHaveTextContent('https://zenstory.ai/brand/docs-card.png')
  })

  it('keeps unlisted private app routes noindex', async () => {
    pathname.value = '/admin/users'
    window.history.replaceState({}, '', pathname.value)

    render(<SEOProvider><Consumer /></SEOProvider>)

    await waitFor(() => expect(screen.getByTestId('noindex')).toHaveTextContent('true'))
  })

  it('derives route-specific Open Graph metadata after docs SPA navigation', async () => {
    pathname.value = '/docs/first'
    window.history.replaceState({}, '', pathname.value)
    const { rerender } = render(<SEOProvider><Consumer /></SEOProvider>)

    pathname.value = '/docs/second'
    window.history.replaceState({}, '', pathname.value)
    rerender(<SEOProvider><Consumer /></SEOProvider>)
    document.body.insertAdjacentHTML('beforeend', `
      <article>
        <h1>Second guide</h1>
        <p>Second guide description.</p>
      </article>
    `)

    await waitFor(() => {
      expect(screen.getByTestId('canonical')).toHaveTextContent(`${window.location.origin}/docs/second`)
      expect(screen.getByTestId('og')).toHaveTextContent('article')
      expect(screen.getByTestId('og')).toHaveTextContent('Second guide · zenstory 文档 | ZenStory AI')
      expect(screen.getByTestId('og')).toHaveTextContent('Second guide description.')
    })
  })

  it.each([
    ['absent static JSON-LD and Open Graph', ''],
    ['malformed static JSON-LD and blank Open Graph', `
      <script type="application/ld+json">{not valid JSON</script>
      <meta property="og:type">
      <meta property="og:title" content="">
      <meta property="og:description">
      <meta property="og:image" content="">
    `],
  ])('falls back safely with a matching canonical and %s', (_name, staticMetadata) => {
    pathname.value = '/docs/guide'
    window.history.replaceState({}, '', pathname.value)
    document.head.innerHTML = `
      <link rel="canonical" href="${window.location.origin}/docs/guide">
      ${staticMetadata}
    `

    expect(() => render(<SEOProvider><Consumer /></SEOProvider>)).not.toThrow()

    expect(screen.getByTestId('schema')).toBeEmptyDOMElement()
    expect(screen.getByTestId('og')).toHaveTextContent('article')
    expect(screen.getByTestId('og')).toHaveTextContent('Documentation - zenstory')
    expect(screen.getByTestId('og')).toHaveTextContent('zenstory documentation')
    expect(screen.getByTestId('og')).toHaveTextContent('https://zenstory.ai/brand/zenstory-ai-mark.svg')
  })

  it('preserves the prior docs description and image when an article has no paragraph', async () => {
    pathname.value = '/docs/guide'
    window.history.replaceState({}, '', pathname.value)
    document.title = 'Initial guide title'
    document.head.insertAdjacentHTML('beforeend', `
      <meta name="description" content="Prior guide description">
      <link rel="canonical" href="${window.location.origin}/docs/guide">
      <meta property="og:type" content="article">
      <meta property="og:title" content="Initial guide OG title">
      <meta property="og:description" content="Prior guide description">
      <meta property="og:image" content="https://zenstory.ai/brand/prior-guide.png">
    `)
    render(<SEOProvider><Consumer /></SEOProvider>)

    document.body.insertAdjacentHTML('beforeend', '<article><h1>Heading without paragraph</h1></article>')

    await waitFor(() => {
      expect(screen.getByTestId('title')).toHaveTextContent('Heading without paragraph')
      expect(screen.getByTestId('og')).toHaveTextContent('Prior guide description')
      expect(screen.getByTestId('og')).toHaveTextContent('https://zenstory.ai/brand/prior-guide.png')
    })
  })
})
