#!/usr/bin/env node
/**
 * Make the workbench docs readable by crawlers that do not execute JavaScript.
 *
 * For /docs and every docs leaf route this writes dist/docs/<slug>/index.html:
 * the built SPA shell (same hashed assets) with a page-specific <title>,
 * description and canonical, and with <div id="root"> pre-filled with the
 * rendered markdown (中文 first, then English). React mounts on the same URL
 * and replaces the root content, so people still get the app; GPTBot,
 * ClaudeBot, PerplexityBot and Bingbot get the article.
 *
 * Runs after `vite build` and after scripts/build-org-pages.mjs.
 * Usage: node scripts/build-docs-pages.mjs [outDir]   (default: ../dist)
 */
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'
import { renderDocsMarkdown } from './render-docs-markdown.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const outDir = resolve(process.argv[2] ?? join(webRoot, 'dist'))
const docsDir = join(webRoot, 'docs')
const SITE = 'https://zenstory.ai'

const sourceShell = readFileSync(join(outDir, 'index.html'), 'utf8')
if (!sourceShell.includes('<div id="root"></div>')) {
  throw new Error('dist/index.html has no empty <div id="root"></div> to fill; aborting docs prerender')
}

// This is our controlled Vite template, not arbitrary HTML to sanitize.
// Fail closed if route metadata appears upstream; each generated route owns it.
assert.equal(sourceShell.split('</head>').length, 2, 'Expected one deterministic head insertion point')
assert.doesNotMatch(sourceShell.slice(0, sourceShell.indexOf('</head>')), /canonical|og:url|application\/ld\+json/i, 'Expected a metadata-free source shell (no canonical, og:url or JSON-LD)')
const shell = sourceShell

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
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

const style = `<style>
#root .prerender{max-width:72ch;margin:0 auto;padding:32px 20px;font:16px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1c1a17}
#root .prerender h1{font-size:30px;line-height:1.2;margin:0 0 16px}#root .prerender h2{font-size:22px;margin:28px 0 8px}#root .prerender h3{font-size:18px;margin:20px 0 6px}
#root .prerender p,#root .prerender li{margin:0 0 10px}#root .prerender ul,#root .prerender ol{padding-left:22px}
#root .prerender table{border-collapse:collapse;margin:12px 0}#root .prerender td,#root .prerender th{border:1px solid #ddd;padding:6px 10px;text-align:left}
#root .prerender code{font-family:ui-monospace,Menlo,monospace;font-size:.9em;background:#f0ede6;padding:.1em .3em;border-radius:3px}#root .prerender pre{overflow-x:auto;background:#f0ede6;padding:12px;border-radius:6px}
#root .prerender img{max-width:100%}#root .prerender hr.lang{margin:40px 0;border:0;border-top:1px solid #ddd}
</style>`

function writePage(route, zhMd, enMd) {
  const zhTitle = firstHeading(zhMd)
  const enTitle = enMd ? firstHeading(enMd) : ''
  const title = `${zhTitle}${enTitle && enTitle !== zhTitle ? ` · ${enTitle}` : ''} · zenstory 文档 | ZenStory AI`
  const description = firstParagraph(enMd || zhMd)
  const canonical = `${SITE}${route}`
  const zhHtml = renderDocsMarkdown(zhMd, route)
  const enHtml = enMd ? renderDocsMarkdown(enMd, route) : ''
  const ld = {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'Organization', '@id': `${SITE}/#org`, name: 'ZenStory AI', url: SITE,
        logo: `${SITE}/brand/zenstory-ai-mark.svg`, sameAs: ['https://github.com/zenstory-ai'],
      },
      {
        '@type': 'WebSite', '@id': `${SITE}/#website`, name: 'ZenStory AI', url: SITE,
        publisher: { '@id': `${SITE}/#org` }, inLanguage: ['en', 'zh-CN'],
      },
      {
        '@type': 'TechArticle', headline: zhTitle, alternativeHeadline: enTitle || undefined,
        url: canonical, inLanguage: enMd ? ['zh-CN', 'en'] : ['zh-CN'],
        isPartOf: { '@id': `${SITE}/#website` }, publisher: { '@id': `${SITE}/#org` },
      },
    ],
  }
  const filled = shell
    .replace(/<title\b[^>]*>[\s\S]*?<\/title>/i, `<title data-rh="true">${esc(title)}</title>`)
    .replace(/<meta\b(?=[^>]*\bname=["']description["'])[^>]*>/i, `<meta name="description" content="${esc(description)}" data-rh="true" />`)
    .replace(/<meta\b(?=[^>]*\bproperty=["']og:type["'])[^>]*>/i, '<meta property="og:type" content="article" data-rh="true" />')
    .replace(/<meta\b(?=[^>]*\bproperty=["']og:title["'])[^>]*>/i, `<meta property="og:title" content="${esc(title)}" data-rh="true" />`)
    .replace(/<meta\b(?=[^>]*\bproperty=["']og:description["'])[^>]*>/i, `<meta property="og:description" content="${esc(description)}" data-rh="true" />`)
    .replace('</head>', `<link rel="canonical" href="${canonical}" data-rh="true" /><meta property="og:url" content="${canonical}" data-rh="true" /><script type="application/ld+json" data-rh="true">${JSON.stringify(ld).replace(/</g, '\\u003c')}</script>${style}</head>`)
    .replace('<div id="root"></div>', `<div id="root"><div class="prerender"><article lang="zh-CN">${zhHtml}</article>${enHtml ? `<hr class="lang" /><article lang="en">${enHtml}</article>` : ''}</div></div>`)
  const dir = join(outDir, route)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'index.html'), filled)
}

const read = (p) => (existsSync(p) ? readFileSync(p, 'utf8') : null)
writePage('/docs', read(join(docsDir, 'README.md')) ?? '# 文档', read(join(docsDir, 'en/README.md')))
const slugs = leafSlugs()
for (const slug of slugs) {
  writePage(`/docs/${slug}`, read(join(docsDir, `${slug}.md`)), read(join(docsDir, 'en', `${slug}.md`)))
}
console.log(`docs pages: prerendered /docs + ${slugs.length} leaf routes into ${outDir}`)
