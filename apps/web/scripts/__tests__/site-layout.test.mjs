import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync, utimesSync, cpSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { finalizeSite, vercelConfig } from '../build-site-layout.mjs'

const INDEXNOW_KEY = '1e4acbc11fe3407a8a641d69a13af696'
const INDEXNOW_FILE = `${INDEXNOW_KEY}.txt`

test('IndexNow ownership key is not shipped as a static public asset', () => {
  assert.match(INDEXNOW_KEY, /^[A-Za-z0-9-]{8,128}$/)
  assert.equal(existsSync(new URL(`../../public/${INDEXNOW_FILE}`, import.meta.url)), false)
})

test('Vercel config is source-controlled and API bypasses the SPA', () => {
  assert.deepEqual(JSON.parse(readFileSync(new URL('../../vercel.json', import.meta.url))), vercelConfig)
  assert.deepEqual(vercelConfig.rewrites[0], {
    source: `/${INDEXNOW_FILE}`,
    destination: '/api/indexnow-key',
  })
  assert.equal(vercelConfig.rewrites[1].source, '/:path(api(?:/.*)?)')
  assert.equal(vercelConfig.rewrites[1].destination, 'https://staging-api-staging-a289.up.railway.app/:path')
  assert.deepEqual(vercelConfig.rewrites[1].has, [{
    type: 'host',
    value: 'geo-preview\\.zenstory\\.ai|app-preview\\.zenstory\\.ai|.*\\.vercel\\.app',
  }])
  assert.equal(vercelConfig.rewrites[2].source, '/:path(api(?:/.*)?)')
  assert.equal(vercelConfig.rewrites[2].destination, 'https://api.zenstory.ai/:path')
  assert.ok(vercelConfig.headers.some(r => r.source === '/:path(api(?:/.*)?)' && r.headers.some(h=>h.key==='x-vercel-enable-rewrite-caching' && h.value==='0')))
  assert.ok(vercelConfig.redirects.findIndex(r=>r.source.endsWith('/index.html') && r.source.startsWith('/:path')) < vercelConfig.redirects.findIndex(r=>r.source.includes('projects|') && r.has), 'Clean aliases before host redirects')
  const scripts = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url))).scripts
  assert.equal(vercelConfig.buildCommand, 'npm run build:vercel')
  assert.equal(vercelConfig.installCommand, 'npm install --legacy-peer-deps')
  assert.equal(readFileSync(new URL('../../.npmrc', import.meta.url), 'utf8'), 'legacy-peer-deps=true\n', 'Standalone Vercel Function installation must use the existing web peer-resolution policy')
  assert.ok(!scripts.build.includes('build-site-layout'), 'Default/Docker build retains a normal root app index')
  assert.match(scripts['build:vercel'], /build-site-layout/)
  const workflow = readFileSync(new URL('../../../../.github/workflows/ci.yml', import.meta.url), 'utf8')
  for (const [, script] of workflow.matchAll(/npm run ([\w:-]+)/g)) assert.ok(scripts[script], `CI script ${script} exists`)
  assert.match(readFileSync(new URL('../../playwright.config.ts', import.meta.url), 'utf8'), /testIgnore: \[.*geo-domain-smoke/)
  assert.ok(!vercelConfig.routes)
  assert.ok(vercelConfig.headers.some(r => r.source === '/:path(.*)' && r.has), 'Preview headers must include root slash')
  assert.ok(vercelConfig.redirects.some(r => r.source === '/:path(.*)' && r.destination === 'https://zenstory.ai/:path'), 'www canonical redirect must include root slash')
  assert.ok(vercelConfig.rewrites.at(-1).has, 'No unconditional apex SPA fallback')
})

test('post-build output cannot shadow host rewrites; sitemap uses canonical routes only', () => {
  const dir = mkdtempSync(join(tmpdir(), 'site-layout-'))
  try {
    writeFileSync(join(dir, 'index.html'), '<html><head><title>App</title><meta name="description" content="App" /></head><body><div id="root"></div></body></html>')
    writeFileSync(join(dir, 'robots.txt'), 'old robots')
    writeFileSync(join(dir, 'sitemap.xml'), 'old sitemap')
    for (const path of ['org-home','projects','docs/example','workbench','zh','zh/projects']) {
      mkdirSync(join(dir,path), {recursive:true});writeFileSync(join(dir,path,'index.html'), '<html>Existing content</html>')
    }
    finalizeSite(dir)
    for (const f of ['index.html','robots.txt','sitemap.xml']) assert.equal(existsSync(join(dir,f)), false)
    assert.match(readFileSync(join(dir,'_app/home.html'),'utf8'), /href="https:\/\/app\.zenstory\.ai\/"/)
    assert.doesNotMatch(readFileSync(join(dir,'_app/index.html'),'utf8'), /rel="canonical"/)
    const privacy = readFileSync(join(dir, 'privacy-policy/index.html'), 'utf8')
    const terms = readFileSync(join(dir, 'terms-of-service/index.html'), 'utf8')
    for (const html of [privacy, terms]) {
      assert.match(html, /<main\b/)
      assert.match(html, /<h1\b[^>]*>[^<]+<\/h1>/)
      assert.match(html, /<p\b[^>]*>[^<]+<\/p>/)
      assert.match(html, /<div id="root"><main\b/)
    }
    assert.match(privacy, /Privacy Policy/)
    assert.match(privacy, /committed to protecting your personal data/)
    assert.match(terms, /Terms of Service/)
    assert.match(terms, /By accessing or using ZenStory/)
    const siteMap=readFileSync(join(dir,'_site/sitemap.xml'),'utf8')
    assert.match(siteMap, /xmlns:xhtml="http:\/\/www\.w3\.org\/1999\/xhtml"/)
    // Single-URL pages list no alternates; language pairs list both languages and x-default on each entry.
    assert.match(siteMap, /<url><loc>https:\/\/zenstory\.ai\/docs\/example<\/loc><\/url>/)
    assert.match(siteMap, /<url><loc>https:\/\/zenstory\.ai\/workbench<\/loc><\/url>/)
    const pair=(route,zh)=>`<xhtml:link rel="alternate" hreflang="en" href="https://zenstory.ai${route}"/><xhtml:link rel="alternate" hreflang="zh-CN" href="https://zenstory.ai${zh}"/><xhtml:link rel="alternate" hreflang="zh" href="https://zenstory.ai${zh}"/><xhtml:link rel="alternate" hreflang="x-default" href="https://zenstory.ai${route}"/>`
    assert.ok(siteMap.includes(`<url><loc>https://zenstory.ai/</loc>${pair('/','/zh')}</url>`))
    assert.ok(siteMap.includes(`<url><loc>https://zenstory.ai/zh</loc>${pair('/','/zh')}</url>`))
    assert.ok(siteMap.includes(`<url><loc>https://zenstory.ai/projects</loc>${pair('/projects','/zh/projects')}</url>`))
    assert.ok(siteMap.includes(`<url><loc>https://zenstory.ai/zh/projects</loc>${pair('/projects','/zh/projects')}</url>`))
    assert.doesNotMatch(siteMap, /app\.zenstory|org-home|_app|_site|\/pricing|\/login/)
    const appMap=readFileSync(join(dir,'_app/sitemap.xml'),'utf8')
    assert.match(appMap, /https:\/\/app\.zenstory\.ai\/pricing/)
    assert.doesNotMatch(appMap, /\/docs|\/dashboard|\/login/)
    assert.match(readFileSync(join(dir,'_site/robots.txt'),'utf8'), /Sitemap: https:\/\/zenstory\.ai\/sitemap.xml/)
    assert.equal(readFileSync(join(dir,'docs/example/index.html'),'utf8'), '<html>Existing content</html>')
    assert.equal(existsSync(join(dir, INDEXNOW_FILE)), false)
    for (const path of ['_site/sitemap.xml', '_app/sitemap.xml', '_site/robots.txt', '_app/robots.txt']) {
      assert.doesNotMatch(readFileSync(join(dir, path), 'utf8'), new RegExp(INDEXNOW_KEY))
    }
  } finally { rmSync(dir, {recursive:true,force:true}) }
})

test('all protected/public route roots are classified and aliases covered', () => {
  const contract=JSON.parse(readFileSync(new URL('../../content/site-routing.json', import.meta.url)))
  const projects=JSON.parse(readFileSync(new URL('../../content/projects.json', import.meta.url)))
  for (const p of projects) assert.ok(contract.sitePrefixes.includes(p.slug))
  assert.ok(contract.sitePrefixes.includes('compare'))
  assert.ok(contract.sitePrefixes.includes('zh'), 'the Chinese site lives under /zh on the organization host')
  const aliasRedirect = vercelConfig.redirects.find(r => r.source.startsWith('/:path') && r.source.endsWith('/index.html'))
  for (const prefix of contract.sitePrefixes.filter(p => p !== 'llms.txt')) assert.ok(aliasRedirect.source.includes(`|${prefix}|`) || aliasRedirect.source.includes(`(?:${prefix}|`) || aliasRedirect.source.includes(`|${prefix})`), `index.html alias redirect must cover ${prefix}`)
  assert.ok(vercelConfig.redirects.some(r => r.source.includes('(?:zh|') && r.destination === 'https://zenstory.ai/:path' && r.has), 'app host must send /zh/* to the organization host')
  const src=readFileSync(new URL('../../src/App.tsx', import.meta.url),'utf8')
  for (const [,path] of src.matchAll(/path="(\/[^"*]+)"/g)) {
    assert.ok([...contract.sitePrefixes,...contract.appPrefixes].includes(path.split('/')[1]), path)
  }
  for (const path of ['/_app/index.html','/_app/home.html','/org-home/index.html','/_site/sitemap.xml','/_app/robots.txt']) {
    assert.ok(vercelConfig.redirects.some(r=>r.source===path), `missing canonical alias ${path}`)
  }
  assert.ok(vercelConfig.redirects.some(r => r.source.includes('compare|') || r.source.includes('|compare|') || r.source.includes('|compare)')), 'comparison index aliases must be canonicalized')
})


test('domain finalizer rejects contaminated shell metadata before generating files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'site-layout-reject-'))
  try {
    mkdirSync(join(dir, 'org-home'))
    writeFileSync(join(dir, 'org-home/index.html'), '<html>Org</html>')
    for (const tag of [
      '<link rel="canonical" href="https://stale.example/">',
      '<meta property="og:url" content="https://stale.example/">',
      '<script type="application/ld+json">{}</script>',
    ]) {
      writeFileSync(join(dir, 'index.html'), `<html><head>${tag}</head><body><div id="root"></div></body></html>`)
      assert.throws(() => finalizeSite(dir), /metadata-free/i)
      assert.equal(existsSync(join(dir, '_app')), false)
    }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})

test('sitemap lastmod follows each page dateModified, not publication, other nodes or build time', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'site-lastmod-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  const render = (route, nodes) => {
    const path = route === '/' ? 'org-home' : route.slice(1)
    mkdirSync(join(dir, path), { recursive: true })
    const html = `<html><script type="application/ld+json">${JSON.stringify({ '@graph': nodes })}</script><p>Original content</p></html>`
    const file = join(dir, path, 'index.html')
    writeFileSync(file, html)
    utimesSync(file, new Date('2030-01-01'), new Date('2030-01-01'))
    return html
  }
  render('/', [{ '@type': 'Organization', url: 'https://zenstory.ai/', dateModified: '2026-01-01' }])
  const article = { '@type': 'Article', url: 'https://zenstory.ai/oh-story/example', datePublished: '2024-02-01', dateModified: '2026-02-28' }
  const original = render('/oh-story/example', [article, { ...article, url: 'https://example.com/elsewhere', dateModified: '2020-01-01' }])
  render('/zh/oh-story/example', [{ ...article, url: 'https://zenstory.ai/zh/oh-story/example', dateModified: '2026-03-01' }])
  render('/compare/example', [{ '@type': 'TechArticle', url: 'https://zenstory.ai/compare/example', dateModified: '2025-06-02' }])
  render('/docs/example', [{ '@type': 'TechArticle', url: 'https://zenstory.ai/docs/example', datePublished: '2024-01-01' }])
  const build = () => {
    writeFileSync(join(dir, 'index.html'), '<html><head><title>App</title></head><body><div id="root"></div></body></html>')
    finalizeSite(dir)
    return readFileSync(join(dir, '_site/sitemap.xml'), 'utf8')
  }
  const first = build()
  assert.ok(first.includes('<loc>https://zenstory.ai/oh-story/example</loc><lastmod>2026-02-28</lastmod>'))
  assert.ok(first.includes('<loc>https://zenstory.ai/zh/oh-story/example</loc><lastmod>2026-03-01</lastmod>'))
  assert.ok(first.includes('<loc>https://zenstory.ai/compare/example</loc><lastmod>2025-06-02</lastmod>'))
  assert.match(first, /<loc>https:\/\/zenstory\.ai\/docs\/example<\/loc><\/url>/)
  assert.equal((first.match(/<lastmod>/g) ?? []).length, 3)
  assert.doesNotMatch(first, /2030-01-01|2024-02-01|2020-01-01|2026-01-01/)
  assert.equal(readFileSync(join(dir, 'oh-story/example/index.html'), 'utf8'), original)
  assert.equal(build(), first, 'rebuilding unchanged pages preserves every sitemap date')
  render('/oh-story/example', [{ ...article, dateModified: '2026-03-02' }])
  const updated = build()
  assert.ok(updated.includes('<loc>https://zenstory.ai/oh-story/example</loc><lastmod>2026-03-02</lastmod>'))
  assert.ok(updated.includes('<loc>https://zenstory.ai/zh/oh-story/example</loc><lastmod>2026-03-01</lastmod>'))
  assert.doesNotMatch(readFileSync(join(dir, '_app/sitemap.xml'), 'utf8'), /lastmod/)
})

test('sitemap rejects impossible or conflicting editorial modification dates', (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'site-lastmod-invalid-'))
  t.after(() => rmSync(dir, { recursive: true, force: true }))
  mkdirSync(join(dir, 'org-home'))
  mkdirSync(join(dir, 'oh-story/example'), { recursive: true })
  writeFileSync(join(dir, 'org-home/index.html'), '<html>Home</html>')
  const node = { '@type': 'Article', url: 'https://zenstory.ai/oh-story/example' }
  for (const nodes of [
    [{ ...node, dateModified: '2026-02-30' }],
    [{ ...node, dateModified: '2026-13-01' }],
    [{ ...node, dateModified: 'today' }],
    [{ ...node, dateModified: '2026-01-01' }, { ...node, dateModified: '2026-01-02' }],
  ]) {
    writeFileSync(join(dir, 'index.html'), '<html><head></head><body><div id="root"></div></body></html>')
    writeFileSync(join(dir, 'oh-story/example/index.html'), `<script type="application/ld+json">${JSON.stringify({ '@graph': nodes })}</script>`)
    assert.throws(() => finalizeSite(dir), /Invalid page dateModified|Conflicting page dateModified/)
  }
})


test('static privacy retains the same rights note and contact email as React', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'site-privacy-parity-'))
  try {
    writeFileSync(join(dir, 'index.html'), '<html><head><title>App</title></head><body><div id="root"></div></body></html>')
    mkdirSync(join(dir, 'org-home'))
    writeFileSync(join(dir, 'org-home/index.html'), '<html>Org</html>')
    finalizeSite(dir)
    const privacy = readFileSync(join(dir, 'privacy-policy/index.html'), 'utf8')
    const source = JSON.parse(readFileSync(new URL('../../public/locales/en/privacy.json', import.meta.url), 'utf8'))
    const escape = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;')
    await t.test('rights-response note is present before hydration', () => {
      const note = `<p>${escape(source.sections.userRights.note)}</p>`
      assert.equal(privacy.split(note).length - 1, 1)
      assert.ok(privacy.indexOf('</ul>') < privacy.indexOf(note))
      assert.ok(privacy.indexOf(note) < privacy.indexOf(source.sections.userRights.dataExport.title))
    })
    await t.test('support contact email is present before hydration', () => {
      const email = `<p>${escape(source.sections.contact.email)}</p>`
      assert.equal(privacy.split(email).length - 1, 1)
      assert.ok(privacy.indexOf(source.sections.contact.content) < privacy.indexOf(email))
    })
    await t.test('unused controller metadata is not broadened into legal copy', () => {
      assert.ok(!privacy.includes(source.sections.contact.controller))
    })
  } finally { rmSync(dir, { recursive: true, force: true }) }
})


test('static legal note/email escape markup without rendering unrelated scalar fields', async () => {
  const fixture = mkdtempSync(join(tmpdir(), 'site-privacy-escaped-'))
  try {
    for (const dir of ['scripts', 'content', 'public/locales/en', 'output/org-home']) mkdirSync(join(fixture, dir), { recursive: true })
    for (const file of ['scripts/build-site-layout.mjs', 'content/site-routing.json', 'content/guide-redirects.json', 'vercel.json']) {
      cpSync(new URL(`../../${file}`, import.meta.url), join(fixture, file))
    }
    const source = JSON.parse(readFileSync(new URL('../../public/locales/en/privacy.json', import.meta.url), 'utf8'))
    const markup = '<img src=x onerror="unsafe()"> & support'
    source.sections.userRights.note = markup
    source.sections.contact.email = markup
    source.sections.contact.controller = 'UNUSED-CONTROLLER'
    source.sections.contact.extraScalar = 'UNUSED-SCALAR'
    writeFileSync(join(fixture, 'public/locales/en/privacy.json'), JSON.stringify(source))
    writeFileSync(join(fixture, 'output/index.html'), '<html><head><title>App</title></head><body><div id="root"></div></body></html>')
    writeFileSync(join(fixture, 'output/org-home/index.html'), '<html>Org</html>')
    const { finalizeSite: finalizeFixture } = await import(pathToFileURL(join(fixture, 'scripts/build-site-layout.mjs')).href)
    finalizeFixture(join(fixture, 'output'))
    const privacy = readFileSync(join(fixture, 'output/privacy-policy/index.html'), 'utf8')
    const escaped = '<p>&lt;img src=x onerror=&quot;unsafe()&quot;> &amp; support</p>'
    assert.equal(privacy.split(escaped).length - 1, 2)
    assert.doesNotMatch(privacy, /<img|UNUSED-CONTROLLER|UNUSED-SCALAR/)
    const terms = readFileSync(join(fixture, 'output/terms-of-service/index.html'), 'utf8')
    assert.match(terms, /Terms of Service/)
    assert.doesNotMatch(terms, /unsafe\(\)|UNUSED-CONTROLLER|UNUSED-SCALAR/)
  } finally { rmSync(fixture, { recursive: true, force: true }) }
})
