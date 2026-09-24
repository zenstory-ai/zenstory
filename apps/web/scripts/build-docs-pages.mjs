#!/usr/bin/env node
/**
 * Render the workbench docs (apps/web/docs/**\/*.md) as static organization
 * pages: /docs and one page per leaf route, each on the shared site shell
 * (header, footer, metadata) with a sidebar built from src/data/docsNavigation.ts.
 *
 * The docs keep one URL per page with both languages on it: the Chinese
 * article first, the English translation (docs/en/**) below it, each with an
 * anchor the header language switch points at. Relative markdown links
 * (`../user-guide/editor.md`) are resolved to site routes and must exist.
 *
 * Runs after `vite build` and scripts/build-org-pages.mjs. No app JavaScript.
 * Usage: node scripts/build-docs-pages.mjs [outDir]   (default: ../dist)
 */
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import assert from 'node:assert/strict'
import { micromark } from 'micromark'
import { gfm, gfmHtml } from 'micromark-extension-gfm'
import { SITE, APP, webRoot, esc, orgNode, localized, page } from './site-shell.mjs'

const outDir = resolve(process.argv[2] ?? join(webRoot, 'dist'))
const docsDir = join(webRoot, 'docs')
const LANG = 'zh' // shell language: the docs are Chinese-first

const render = (md) => micromark(md, { extensions: [gfm()], htmlExtensions: [gfmHtml()] })
const firstHeading = (md) => (md.match(/^#\s+(.+)$/m)?.[1] ?? '').trim()
const firstParagraph = (md) => {
  const body = md.replace(/^#\s+.+$/m, '').trim()
  const para = body.split(/\n\s*\n/).find((p) => p && !p.startsWith('#') && !p.startsWith('|') && !p.startsWith('-') && !p.startsWith('!'))
  return (para ?? '').replace(/\s+/g, ' ').replace(/[*_`\[\]]/g, '').slice(0, 300)
}

/** Leaf docs routes, mirroring DocsPage's import.meta.glob over docs/**\/*.md (en/ is a translation, not a route). */
function leafSlugs() {
  const out = []
  const walk = (dir, rel) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'en') continue
      const r = rel ? `${rel}/${e.name}` : e.name
      if (e.isDirectory()) walk(join(dir, e.name), r)
      else if (e.name.endsWith('.md') && e.name !== 'README.md') out.push(r.replace(/\.md$/, ''))
    }
  }
  walk(docsDir, '')
  return out.sort()
}
const slugs = leafSlugs()
const routes = new Set(['/docs', ...slugs.map((slug) => `/docs/${slug}`)])

/**
 * The sidebar comes from the app's navigation data so both renderings agree.
 * docsNavigation.ts is a plain literal; each entry is `{ title, titleZh, path }`
 * with sections (`/docs/<group>`) followed by their leaves (`/docs/<group>/<page>`).
 */
function navigation() {
  const source = readFileSync(join(webRoot, 'src/data/docsNavigation.ts'), 'utf8')
  const entries = [...source.matchAll(/title:\s*"([^"]+)",\s*titleZh:\s*"([^"]+)",\s*path:\s*"([^"]+)"/g)]
    .map(([, title, titleZh, path]) => ({ title, titleZh, path }))
  const sections = []
  for (const entry of entries) {
    const depth = entry.path.split('/').length
    if (depth === 3) sections.push({ ...entry, children: [] })
    else if (depth === 4) sections.at(-1).children.push(entry)
    else throw new Error(`Unexpected docs navigation path ${entry.path}`)
  }
  const listed = sections.flatMap((s) => s.children.map((c) => c.path))
  assert.deepEqual([...listed].sort(), [...routes].filter((r) => r !== '/docs').sort(), 'docsNavigation.ts and docs/**/*.md must list the same pages')
  return sections
}
const sections = navigation()

/**
 * Point relative markdown links at site routes. `dir` is the directory of the
 * current page (`/docs/` or `/docs/<group>/`); the English tree mirrors the
 * Chinese one, so the same resolution serves both articles. Unknown targets fail.
 */
function resolveLinks(html, route) {
  const dir = route === '/docs' ? '/docs/' : route.slice(0, route.lastIndexOf('/') + 1)
  return html.replace(/href="([^"]+)"/g, (m, href) => {
    if (/^(?:https?:|mailto:|tel:|#|\/)/i.test(href)) return m
    const [pathPart, hash] = href.split('#')
    let target = new URL(pathPart, `https://zenstory.local${dir}`).pathname.replace(/\.md$/i, '').replace(/\/README$/, '').replace(/(.)\/$/, '$1')
    // A link to a section (`../user-guide/`) opens its first page, as the app does.
    const section = sections.find((s) => s.path === target)
    if (section) target = section.children[0].path
    assert.ok(routes.has(target), `${route}: link to ${href} resolves to ${target}, which is not a docs page`)
    return `href="${target}${hash ? `#${hash}` : ''}"`
  })
}

const sidebar = (route) => `
<nav class="docs-side" aria-label="文档目录 · Documentation">
  <p class="docs-side-h"><a href="/docs"${route === '/docs' ? ' aria-current="page"' : ''}>工作台文档</a></p>
  ${sections.map((section) => `
  <p class="docs-group">${esc(section.titleZh)} <span class="docs-group-en">${esc(section.title)}</span></p>
  <ul>${section.children.map((leaf) => `
    <li><a href="${leaf.path}"${leaf.path === route ? ' aria-current="page"' : ''}>${esc(leaf.titleZh)}</a></li>`).join('')}
  </ul>`).join('')}
</nav>`

function writePage(route, zhMd, enMd) {
  const zhTitle = firstHeading(zhMd)
  const enTitle = enMd ? firstHeading(enMd) : ''
  // One language in the title: the page is Chinese first; the English title stays in JSON-LD (alternativeHeadline).
  const title = `${zhTitle} | ZenStory Workbench`
  const description = firstParagraph(zhMd)
  const canonical = `${SITE}${route}`
  const zhHtml = resolveLinks(render(zhMd), route)
  // The English article sits below the Chinese one on the same page: its headings move down one
  // level so the page keeps a single <h1>.
  const enHtml = enMd ? resolveLinks(render(enMd), route).replace(/<(\/?)h([1-5])\b/g, (m, slash, level) => `<${slash}h${Number(level) + 1}`) : ''
  const ld = [
    orgNode,
    {
      '@type': 'WebSite', '@id': `${SITE}/#website`, name: 'ZenStory AI', url: SITE,
      publisher: { '@id': `${SITE}/#org` }, inLanguage: ['en', 'zh-CN'],
    },
    {
      '@type': 'TechArticle', '@id': `${canonical}#article`, headline: zhTitle, alternativeHeadline: enTitle || undefined,
      url: canonical, inLanguage: enMd ? ['zh-CN', 'en'] : ['zh-CN'],
      about: { '@id': `${SITE}/workbench#software` },
      isPartOf: { '@id': `${SITE}/#website` }, publisher: { '@id': `${SITE}/#org` },
    },
  ]
  const body = `
<article class="docs">
  <div class="wrap docs-grid">
    ${sidebar(route)}
    <div class="docs-body">
      <p class="crumbs"><a href="${localized(LANG, '/')}">ZenStory AI</a> <span aria-hidden="true">/</span> <a href="${localized(LANG, '/workbench')}">ZenStory Workbench</a> <span aria-hidden="true">/</span> <a href="/docs">工作台文档</a></p>
      <p class="docs-note">本页是 <a href="${localized(LANG, '/workbench')}">ZenStory 工作台</a> 的产品文档；工作台在 <a href="${APP}">app.zenstory.ai</a> 运行。${enHtml ? '<a href="#en" lang="en">English below</a>' : ''}</p>
      <section class="prose" id="zh" lang="zh-CN">${zhHtml}</section>
      ${enHtml ? `<hr class="lang">
      <section class="prose" id="en" lang="en">${enHtml}</section>` : ''}
    </div>
  </div>
</article>`
  const html = page({ lang: LANG, route, alternates: null, switchLinks: { en: enHtml ? '#en' : '#zh', zh: '#zh' }, title, description, ogType: 'article', ld, body })
  const dir = join(outDir, route)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'index.html'), html)
}

const read = (p) => (existsSync(p) ? readFileSync(p, 'utf8') : null)
writePage('/docs', read(join(docsDir, 'README.md')) ?? '# 文档', read(join(docsDir, 'en/README.md')))
for (const slug of slugs) {
  writePage(`/docs/${slug}`, read(join(docsDir, `${slug}.md`)), read(join(docsDir, 'en', `${slug}.md`)))
}
console.log(`docs pages: rendered /docs + ${slugs.length} leaf routes into ${outDir}`)
