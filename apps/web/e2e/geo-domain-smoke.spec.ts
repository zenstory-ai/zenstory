import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const API = 'https://api.zenstory.ai'
const CANONICAL_SITE = 'https://zenstory.ai'
const CANONICAL_APP = 'https://app.zenstory.ai'
const PREVIEW_SITE = 'https://geo-preview.zenstory.ai'
const PREVIEW_APP = 'https://app-preview.zenstory.ai'

const mode = process.env.GEO_DOMAIN_MODE
if (mode && mode !== 'preview' && mode !== 'production') {
  throw new Error('GEO_DOMAIN_MODE must be "preview" or "production"')
}

const explicitSite = process.env.GEO_SITE_ORIGIN
const explicitApp = process.env.GEO_APP_ORIGIN
if (Boolean(explicitSite) !== Boolean(explicitApp)) {
  throw new Error('Set GEO_SITE_ORIGIN and GEO_APP_ORIGIN together')
}
if (!explicitSite && !mode) {
  throw new Error('Set both GEO_SITE_ORIGIN/GEO_APP_ORIGIN or set GEO_DOMAIN_MODE')
}

const SITE = new URL(explicitSite || (mode === 'production' ? CANONICAL_SITE : PREVIEW_SITE)).origin
const APP = new URL(explicitApp || (mode === 'production' ? CANONICAL_APP : PREVIEW_APP)).origin
const IS_PREVIEW = mode ? mode === 'preview' : SITE !== CANONICAL_SITE || APP !== CANONICAL_APP

const PROJECT_SLUGS = [
  'oh-story',
  'drama-skills',
  'novel-to-game',
  'video-recap',
  'dsh',
  'workbench',
] as const

const PUBLIC_DOCUMENTS = [
  {
    name: 'documentation leaf',
    path: '/docs/getting-started/quick-start',
    ogType: 'article',
    requiredSchemaType: 'TechArticle',
  },
  {
    name: 'legal page',
    path: '/privacy-policy',
    ogType: 'website',
  },
  {
    name: 'terms page',
    path: '/terms-of-service',
    ogType: 'website',
  },
] as const

const APP_PUBLIC_DOCUMENTS = [
  { name: 'app home', path: '/', ogType: 'website' },
  { name: 'app pricing', path: '/pricing', ogType: 'website' },
] as const

type SchemaNode = {
  type: string
  references: Array<{
    attribute: '@id' | 'url' | 'isPartOf.@id' | 'isPartOf.url'
    value: string
  }>
}

async function schemaNodes(page: Page): Promise<SchemaNode[]> {
  return page.locator('script[type="application/ld+json"]').evaluateAll((elements) => {
    const nodes: SchemaNode[] = []
    const visit = (value: unknown) => {
      if (Array.isArray(value)) {
        value.forEach(visit)
        return
      }
      if (!value || typeof value !== 'object') return
      const record = value as Record<string, unknown>
      const type = record['@type']
      const types = typeof type === 'string'
        ? [type]
        : Array.isArray(type)
          ? type.filter((item): item is string => typeof item === 'string')
          : []
      const references: SchemaNode['references'] = []
      if (typeof record['@id'] === 'string') references.push({ attribute: '@id', value: record['@id'] })
      if (typeof record.url === 'string') references.push({ attribute: 'url', value: record.url })
      const isPartOf = record.isPartOf
      if (typeof isPartOf === 'string') references.push({ attribute: 'isPartOf.@id', value: isPartOf })
      if (isPartOf && typeof isPartOf === 'object' && !Array.isArray(isPartOf)) {
        const parent = isPartOf as Record<string, unknown>
        if (typeof parent['@id'] === 'string') {
          references.push({ attribute: 'isPartOf.@id', value: parent['@id'] })
        }
        if (typeof parent.url === 'string') references.push({ attribute: 'isPartOf.url', value: parent.url })
      }
      for (const item of types) nodes.push({ type: item, references })
      Object.values(record).forEach(visit)
    }

    for (const element of elements) visit(JSON.parse(element.textContent || 'null'))
    return nodes
  })
}

async function expectPublicDocumentHead(
  page: Page,
  canonicalUrl: string,
  requiredSchemaType?: string,
): Promise<void> {
  const canonical = page.locator('link[rel="canonical"]')
  const openGraphUrl = page.locator('meta[property="og:url"]')

  await expect(canonical).toHaveCount(1)
  await expect(canonical).toHaveAttribute('href', canonicalUrl)
  await expect(openGraphUrl).toHaveCount(1)
  await expect(openGraphUrl).toHaveAttribute('content', canonicalUrl)

  const nodes = await schemaNodes(page)
  const types = nodes.map((node) => node.type)
  const appOrigins = new Set([CANONICAL_APP, APP])
  for (const node of nodes.filter((item) => item.type === 'WebSite')) {
    expect(
      node.references.some((reference) => {
        const isId = reference.attribute === '@id' || reference.attribute === 'isPartOf.@id'
        if (isId && (reference.value.startsWith('#') || reference.value.startsWith('_:'))) return false
        return appOrigins.has(new URL(reference.value, CANONICAL_SITE).origin)
      }),
      'public site documents must not inherit an app-origin WebSite node',
    ).toBe(false)
  }
  expect(types, 'public site documents must not inherit app structured data').not.toContain('SoftwareApplication')
  if (requiredSchemaType) expect(types).toContain(requiredSchemaType)
}

async function expectFullOpenGraph(page: Page, canonicalUrl: string, ogType: string): Promise<void> {
  const fields = ['type', 'site_name', 'title', 'description', 'image'] as const
  for (const field of fields) {
    const metadata = page.locator(`meta[property="og:${field}"]`)
    await expect(metadata, `og:${field} must occur exactly once`).toHaveCount(1)
    await expect(metadata).toHaveAttribute('content', /\S+/)
  }
  await expect(page.locator('meta[property="og:type"]')).toHaveAttribute('content', ogType)
  await expect(page.locator('meta[property="og:url"]')).toHaveCount(1)
  await expect(page.locator('meta[property="og:url"]')).toHaveAttribute('content', canonicalUrl)
}

async function getWithoutRedirects(request: APIRequestContext, url: string) {
  return request.get(url, { maxRedirects: 0, timeout: 20_000 })
}

async function expectOneHopCanonicalRedirect(
  request: APIRequestContext,
  source: string,
  destination: string,
): Promise<void> {
  const first = await getWithoutRedirects(request, source)
  expect(first.status()).toBe(308)
  const location = first.headers().location
  expect(location).toBe(destination)
  expect(location).not.toBe(source)

  const final = await getWithoutRedirects(request, destination)
  expect(final.status(), `${destination} must terminate the alias redirect`).toBeLessThan(400)
}

test.describe('organization site', () => {
  test('serves a meaningful bilingual home with six project destinations and the app CTA without JavaScript', async ({ browser }) => {
    const context = await browser.newContext({ javaScriptEnabled: false })
    const page = await context.newPage()

    await page.goto(`${SITE}/`, { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { level: 1 })).toContainText('ZenStory AI')
    await expect(page.locator('article.home .lede').first()).toContainText(/story|open-source/i)
    await expect(page.locator('article.home .lede[lang="zh-CN"]')).toContainText(/故事|创作/)
    const projectDestinations = await page.locator('.project-grid h3 a').evaluateAll((links) =>
      links.map((link) => link.getAttribute('href')),
    )
    expect(projectDestinations).toEqual(PROJECT_SLUGS.map((slug) => `/${slug}`))
    await expect(page.locator(`a[href="${CANONICAL_APP}"]`).first()).toBeVisible()

    await context.close()
  })

  test('fits the organization home in a 390px viewport and records a mobile screenshot', async ({ browser }, testInfo) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
    const page = await context.newPage()
    await page.goto(`${SITE}/`, { waitUntil: 'networkidle' })

    const widths = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth,
      viewport: document.documentElement.clientWidth,
    }))
    expect(widths.document).toBeLessThanOrEqual(widths.viewport)
    await page.screenshot({ path: testInfo.outputPath('organization-home-mobile.png'), fullPage: true })

    await context.close()
  })

  for (const path of ['/novel-to-game/quick-start', '/video-recap/capcut-draft']) {
    test(`task guide ${path} fits a 390px viewport after fonts settle`, async ({ browser }, testInfo) => {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
      try {
        const page = await context.newPage()
        await page.goto(`${SITE}${path}`, { waitUntil: 'load' })
        await expect(page.locator('article.guide h1')).toBeVisible()
        await expect(page.locator('article.guide pre').first()).toHaveCSS('white-space', 'pre-wrap')
        await expect(page.locator('article.guide pre').first()).toHaveCSS('overflow-wrap', 'anywhere')
        await page.evaluate(() => document.fonts.ready.then(() => undefined))

        const widths = await page.evaluate(() => ({
          document: document.documentElement.scrollWidth,
          viewport: document.documentElement.clientWidth,
        }))
        await page.screenshot({ path: testInfo.outputPath('task-guide-mobile.png'), fullPage: true })
        expect(widths.document).toBeLessThanOrEqual(widths.viewport)
      } finally {
        await context.close()
      }
    })
  }

  for (const document of PUBLIC_DOCUMENTS) {
    test(`${document.name} has one production canonical and clean public schema in initial HTML`, async ({ browser }) => {
      const context = await browser.newContext({ javaScriptEnabled: false })
      const page = await context.newPage()
      await page.goto(`${SITE}${document.path}`, { waitUntil: 'domcontentloaded' })

      await expectPublicDocumentHead(
        page,
        `${CANONICAL_SITE}${document.path}`,
        'requiredSchemaType' in document ? document.requiredSchemaType : undefined,
      )
      await context.close()
    })

    test(`${document.name} keeps one production canonical and clean public schema after hydration`, async ({ page }) => {
      await page.goto(`${SITE}${document.path}`, { waitUntil: 'networkidle' })
      await expect(page.locator('main h1').first()).toBeVisible()

      await expectPublicDocumentHead(
        page,
        `${CANONICAL_SITE}${document.path}`,
        'requiredSchemaType' in document ? document.requiredSchemaType : undefined,
      )
      await expectFullOpenGraph(page, `${CANONICAL_SITE}${document.path}`, document.ogType)
    })
  }

  test('docs client navigation returns home on the site origin', async ({ page }) => {
    await page.goto(`${SITE}/docs/getting-started/quick-start`, { waitUntil: 'networkidle' })
    await page.locator('header button').first().click()

    await expect(page).toHaveURL(`${SITE}/`)
    await expect(page.getByRole('heading', { level: 1 })).toContainText('ZenStory AI')
  })

  test('docs client navigation sends an app route to the app origin', async ({ page }) => {
    await page.goto(`${SITE}/docs/getting-started/quick-start`, { waitUntil: 'networkidle' })
    await page.locator('header a[href^="/pricing"]').first().click()

    await expect(page).toHaveURL(new RegExp(`^${APP.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/pricing(?:[?#]|$)`))
  })
})

test.describe('anonymous app boundary', () => {
  for (const document of APP_PUBLIC_DOCUMENTS) {
    test(`${document.name} keeps complete app Open Graph metadata after hydration`, async ({ page }) => {
      await page.goto(`${APP}${document.path}`, { waitUntil: 'networkidle' })

      const canonical = document.path === '/' ? `${CANONICAL_APP}/` : `${CANONICAL_APP}${document.path}`
      await expect(page.locator('link[rel="canonical"]')).toHaveCount(1)
      await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', canonical)
      await expectFullOpenGraph(page, canonical, document.ogType)
    })
  }

  test('serves login on the app origin without authenticated storage', async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: 'networkidle' })

    expect(new URL(page.url()).origin).toBe(APP)
    expect(new URL(page.url()).pathname).toBe('/login')
    await expect(page.locator('form')).toBeVisible()
  })

  test('redirects an anonymous protected dashboard visit to app login', async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: 'networkidle' })

    expect(new URL(page.url()).origin).toBe(APP)
    expect(new URL(page.url()).pathname).toBe('/login')
  })

  test('preserves a dummy OAuth error across the site boundary, handles it, and scrubs the fragment', async ({ page }) => {
    await page.goto(`${SITE}/auth/callback#error=access_denied`, { waitUntil: 'networkidle' })

    expect(new URL(page.url()).origin).toBe(APP)
    expect(new URL(page.url()).pathname).toBe('/auth/callback')
    await expect(page.locator('.btn-primary')).toBeVisible()
    await expect.poll(() => new URL(page.url()).hash).toBe('')
  })
})

test.describe('platform routing', () => {
  test('proxies Google OAuth initiation from both origins with the backend callback contract', async ({ request }) => {
    const direct = await getWithoutRedirects(request, `${API}/api/auth/google`)
    expect(direct.status()).toBe(307)

    for (const origin of [API, SITE, APP]) {
      const response = origin === API
        ? direct
        : await getWithoutRedirects(request, `${origin}/api/auth/google`)
      expect(response.status()).toBe(direct.status())
      const destination = new URL(response.headers().location || 'about:blank')
      expect(destination.origin).toBe('https://accounts.google.com')
      expect(destination.searchParams.get('redirect_uri')).toBe(`${API}/api/auth/google/callback`)
    }
  })

  test('proxies API health from both origins with the direct backend JSON status and body', async ({ request }) => {
    const direct = await getWithoutRedirects(request, `${API}/api/health`)
    expect(direct.headers()['content-type']).toContain('application/json')
    const directBody: unknown = await direct.json()

    for (const origin of [SITE, APP]) {
      const response = await getWithoutRedirects(request, `${origin}/api/health`)
      expect(response.status()).toBe(direct.status())
      expect(response.headers()['content-type']).toContain('application/json')
      expect(await response.json()).toEqual(directBody)
    }
  })

  for (const path of ['/api', '/api/']) {
    test(`${path} cannot fall through to the app shell and matches the backend JSON response`, async ({ request }) => {
      const direct = await getWithoutRedirects(request, `${API}${path}`)
      expect(direct.headers()['content-type']).toContain('application/json')
      const directBody: unknown = await direct.json()

      for (const origin of [SITE, APP]) {
        const response = await getWithoutRedirects(request, `${origin}${path}`)
        expect(response.status()).toBe(direct.status())
        expect(response.headers()['content-type']).toContain('application/json')
        expect(await response.json()).toEqual(directBody)
        expect(response.headers()['cache-control']).toContain('private')
        expect(response.headers()['cache-control']).toContain('no-store')
      }
    })
  }

  test(`${IS_PREVIEW ? 'marks preview' : 'keeps production'} roots with the expected indexing header`, async ({ request }) => {
    for (const origin of [SITE, APP]) {
      const response = await getWithoutRedirects(request, `${origin}/`)
      expect(response.status()).toBe(200)
      const robotsTag = response.headers()['x-robots-tag']?.toLowerCase() || ''
      if (IS_PREVIEW) expect(robotsTag).toContain('noindex')
      else expect(robotsTag).not.toContain('noindex')
    }
  })

  test('redirects an app-host docs index alias directly to its clean production canonical', async ({ request }) => {
    const source = `${APP}/docs/getting-started/quick-start/index.html`
    const destination = `${CANONICAL_SITE}/docs/getting-started/quick-start`
    const redirect = await getWithoutRedirects(request, source)

    expect(redirect.status()).toBe(308)
    expect(redirect.headers().location).toBe(destination)
    const final = await getWithoutRedirects(request, destination)
    expect(final.status()).toBe(200)
  })

  test('returns a genuine 404 for an unknown organization URL', async ({ request }) => {
    const response = await getWithoutRedirects(request, `${SITE}/__geo-domain-smoke-missing__`)
    expect(response.status()).toBe(404)
  })

  test('redirects reserved filesystem aliases once to canonical production URLs', async ({ request }) => {
    const aliases = [
      ['/org-home', `${CANONICAL_SITE}/`],
      ['/org-home/', `${CANONICAL_SITE}/`],
      ['/org-home/index.html', `${CANONICAL_SITE}/`],
      ['/_app', `${CANONICAL_APP}/`],
      ['/_app/', `${CANONICAL_APP}/`],
      ['/_app/index.html', `${CANONICAL_APP}/`],
      ['/_app/home.html', `${CANONICAL_APP}/`],
      ['/_app/pricing.html', `${CANONICAL_APP}/pricing`],
      ['/_app/robots.txt', `${CANONICAL_APP}/robots.txt`],
      ['/_app/sitemap.xml', `${CANONICAL_APP}/sitemap.xml`],
      ['/_site/robots.txt', `${CANONICAL_SITE}/robots.txt`],
      ['/_site/sitemap.xml', `${CANONICAL_SITE}/sitemap.xml`],
    ] as const

    for (const origin of [SITE, APP]) {
      for (const [path, destination] of aliases) {
        await expectOneHopCanonicalRedirect(request, `${origin}${path}`, destination)
      }
    }
  })
})
