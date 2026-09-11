#!/usr/bin/env node
/**
 * Generate the static ZenStory AI organization pages into the Vite output dir.
 *
 * Runs after `vite build`. Emits complete, crawler-readable HTML (title, meta,
 * Open Graph, JSON-LD, bilingual body) for:
 *   /projects            – the six-project overview
 *   /<project-slug>      – one page per project
 *   /glossary            – the terminology index
 *   /glossary/<term>     – one page per term
 *
 * Vercel matches these files on the filesystem before the SPA rewrite, so the
 * React app is untouched. Content lives in ../content/*.json.
 *
 * Usage: node scripts/build-org-pages.mjs [outDir]   (default: ../dist)
 */
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const outDir = resolve(process.argv[2] ?? join(webRoot, 'dist'))
const SITE = 'https://zenstory.ai'

const org = JSON.parse(readFileSync(join(webRoot, 'content/org.json'), 'utf8'))
const projects = JSON.parse(readFileSync(join(webRoot, 'content/projects.json'), 'utf8'))
const glossary = JSON.parse(readFileSync(join(webRoot, 'content/glossary.json'), 'utf8'))

// ---------- helpers ----------

const esc = (s) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** Escape, then turn `code` spans into <code> and [text](url) into links. */
const rich = (s) => esc(s)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2">$1</a>')

const jsonld = (obj) => `<script type="application/ld+json">${JSON.stringify(obj).replace(/</g, '\\u003c')}</script>`

const orgNode = { '@type': 'Organization', '@id': `${SITE}/#org`, name: org.name, url: SITE, sameAs: [org.github], logo: `${SITE}/brand/zenstory-ai-mark.svg` }

const breadcrumb = (items) => ({
  '@type': 'BreadcrumbList',
  itemListElement: items.map(([name, url], i) => ({ '@type': 'ListItem', position: i + 1, name, item: url })),
})

const write = (route, html) => {
  const dir = join(outDir, route)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'index.html'), html)
}

// ---------- layout ----------

const nav = `
<header class="top">
  <a class="brand" href="/"><img src="/brand/zenstory-ai-mark.svg" alt="" width="22" height="22"> ZenStory AI</a>
  <nav>
    <a href="/projects">Projects</a>
    <a href="/glossary">Glossary</a>
    <a href="/docs">Docs</a>
    <a href="${org.github}">GitHub</a>
  </nav>
</header>`

const roster = (current) => `
<section class="roster" aria-labelledby="roster-h">
  <h2 id="roster-h">Part of ZenStory AI <span lang="zh-CN">· ZenStory AI 项目</span></h2>
  <p>${esc(org.canonical.en)}</p>
  <p lang="zh-CN">${esc(org.canonical.zh)}</p>
  <table>
    <thead><tr><th>Project</th><th>Format</th><th>What it does</th></tr></thead>
    <tbody>${projects.map((p) => `
      <tr${p.slug === current ? ' class="here"' : ''}>
        <td><a href="/${p.slug}">${esc(p.name.en)}</a></td>
        <td>${esc(p.format.en)}</td>
        <td>${esc(p.tagline.en)}</td>
      </tr>`).join('')}
    </tbody>
  </table>
</section>`

const footer = `
<footer class="bottom">
  <p>© ${new Date().getUTCFullYear()} ZenStory AI · MIT-licensed open source · <a href="${org.github}">github.com/zenstory-ai</a> · <a href="/llms.txt">llms.txt</a></p>
</footer>`

const page = ({ route, title, description, ogType = 'article', ld, body }) => `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}">
<link rel="canonical" href="${SITE}${route}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta property="og:type" content="${ogType}">
<meta property="og:site_name" content="ZenStory AI">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${SITE}${route}">
<meta property="og:image" content="${SITE}/brand/zenstory-ai-mark.svg">
<meta property="og:locale" content="en_US">
<meta property="og:locale:alternate" content="zh_CN">
<meta name="twitter:card" content="summary">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Plus+Jakarta+Sans:wght@400;500;600&family=Noto+Serif+SC:wght@400;600&family=JetBrains+Mono:wght@400&display=swap">
<link rel="stylesheet" href="/org/org.css">
${jsonld({ '@context': 'https://schema.org', '@graph': ld })}
</head>
<body>
${nav}
<main>
${body}
</main>
${footer}
</body>
</html>
`

// ---------- project pages ----------

const list = (items, lang) => `<ul>${items.map((i) => `<li${lang === 'zh' ? ' lang="zh-CN"' : ''}>${rich(i)}</li>`).join('')}</ul>`
const steps = (items, lang) => `<ol class="steps">${items.map(([k, v]) => `<li${lang === 'zh' ? ' lang="zh-CN"' : ''}><b>${rich(k)}</b> ${rich(v)}</li>`).join('')}</ol>`

const projectPage = (p) => {
  const route = `/${p.slug}`
  const title = `${p.name.en} — ${p.tagline.en.replace(/\.$/, '')} | ZenStory AI`
  const facts = [
    p.stars ? `${p.stars.toLocaleString('en-US')} GitHub stars` : null,
    p.skills ? `${p.skills} skills` : null,
    'MIT license',
    `Format: ${p.format.en}`,
  ].filter(Boolean)
  const ld = [
    orgNode,
    {
      '@type': p.slug === 'workbench' ? 'SoftwareApplication' : 'SoftwareSourceCode',
      '@id': `${SITE}${route}#software`,
      name: p.name.en,
      alternateName: p.repo,
      description: p.definition.en,
      url: `${SITE}${route}`,
      codeRepository: p.github,
      license: 'https://opensource.org/licenses/MIT',
      publisher: { '@id': `${SITE}/#org` },
      ...(p.slug === 'workbench' ? { applicationCategory: 'BusinessApplication', operatingSystem: 'Web' } : { programmingLanguage: 'Markdown' }),
      keywords: (p.vocabulary ?? []).join(', '),
    },
    breadcrumb([['ZenStory AI', SITE], ['Projects', `${SITE}/projects`], [p.name.en, `${SITE}${route}`]]),
  ]
  const body = `
<article class="project">
  <p class="eyebrow">${esc(p.format.en)} <span lang="zh-CN">· ${esc(p.format.zh)}</span></p>
  <h1>${esc(p.name.en)}</h1>
  <p class="lede">${esc(p.tagline.en)}</p>
  <p class="lede" lang="zh-CN">${esc(p.tagline.zh)}</p>
  <p class="facts">${facts.map(esc).join(' · ')}</p>
  <p class="actions">
    <a class="btn" href="${p.github}">Source on GitHub</a>
    ${p.readme_en && p.readme_en !== p.github ? `<a class="btn ghost" href="${p.readme_en}">English README</a>` : ''}
    ${p.install ? `<code class="install">${esc(p.install)}</code>` : ''}
    ${p.slug === 'workbench' ? `<a class="btn ghost" href="${p.entry}">Open the workbench</a>` : ''}
  </p>

  <h2>What it is</h2>
  <p>${rich(p.definition.en)}</p>
  <h2 lang="zh-CN">它是什么</h2>
  <p lang="zh-CN">${rich(p.definition.zh)}</p>

  <h2>Who it is for <span lang="zh-CN">· 适合谁</span></h2>
  <div class="cols">${list(p.audience.en)}${list(p.audience.zh, 'zh')}</div>

  <h2>How it works <span lang="zh-CN">· 流程</span></h2>
  ${steps(p.method.en)}
  ${steps(p.method.zh, 'zh')}

  <h2>What makes it different <span lang="zh-CN">· 有什么不同</span></h2>
  <div class="cols">${list(p.distinctive.en)}${list(p.distinctive.zh, 'zh')}</div>

  ${p.vocabulary?.length ? `<h2>Terms it uses</h2><p class="terms">${p.vocabulary.map((t) => {
    const g = glossary.find((x) => x.term === t)
    return g ? `<a href="/glossary/${g.slug}">${esc(t)}</a>` : `<span>${esc(t)}</span>`
  }).join(' ')}</p>` : ''}
</article>
${roster(p.slug)}`
  write(route, page({ route, title, description: p.definition.en, ogType: 'website', ld, body }))
}

// ---------- projects index ----------

const projectsIndex = () => {
  const route = '/projects'
  const title = 'ZenStory AI projects — open-source agent skills for story creation, adaptation and production'
  const ld = [
    orgNode,
    {
      '@type': 'ItemList', name: 'ZenStory AI projects',
      itemListElement: projects.map((p, i) => ({ '@type': 'ListItem', position: i + 1, name: p.name.en, url: `${SITE}/${p.slug}` })),
    },
    breadcrumb([['ZenStory AI', SITE], ['Projects', `${SITE}${route}`]]),
  ]
  const body = `
<article>
  <p class="eyebrow">${esc(org.tagline.en)} <span lang="zh-CN">· ${esc(org.tagline.zh)}</span></p>
  <h1>Six open-source projects, one story stack</h1>
  <p class="lede">${esc(org.canonical.en)}</p>
  <p class="lede" lang="zh-CN">${esc(org.canonical.zh)}</p>

  <h2>Start with what you want to make <span lang="zh-CN">· 从目标开始</span></h2>
  <div class="cards">${projects.map((p) => `
    <a class="card" href="/${p.slug}">
      <p class="eyebrow">${esc(p.format.en)}</p>
      <h3>${esc(p.name.en)}</h3>
      <p>${esc(p.tagline.en)}</p>
      <p lang="zh-CN">${esc(p.tagline.zh)}</p>
      <p class="facts">${p.stars ? `${p.stars.toLocaleString('en-US')} ★` : ''}${p.skills ? ` · ${p.skills} skills` : ''}</p>
    </a>`).join('')}
  </div>

  <h2>How the pieces fit together <span lang="zh-CN">· 项目如何协作</span></h2>
  ${steps(org.model.en)}
  ${steps(org.model.zh, 'zh')}

  <h2>Runs inside the agents you already use</h2>
  <p>${org.proof.harnesses.map(esc).join(' · ')}. All ${org.proof.license}-licensed. ${org.proof.stars_total.toLocaleString('en-US')} GitHub stars across the organization as of ${org.proof.as_of}.</p>
</article>`
  write(route, page({ route, title, description: org.canonical.en, ogType: 'website', ld, body }))
}

// ---------- glossary ----------

const termPage = (g) => {
  const route = `/glossary/${g.slug}`
  const owner = projects.find((p) => p.slug === g.owner)
  const title = `${g.term} — ${g.bridge} | ZenStory AI glossary`
  const ld = [
    orgNode,
    {
      '@type': 'DefinedTerm', '@id': `${SITE}${route}#term`, name: g.term, alternateName: g.bridge,
      description: g.definition.en, url: `${SITE}${route}`,
      inDefinedTermSet: { '@type': 'DefinedTermSet', name: 'ZenStory AI glossary', url: `${SITE}/glossary` },
    },
    breadcrumb([['ZenStory AI', SITE], ['Glossary', `${SITE}/glossary`], [g.term, `${SITE}${route}`]]),
  ]
  const body = `
<article class="term">
  <p class="eyebrow">Glossary <span lang="zh-CN">· 术语</span></p>
  <h1 lang="zh-CN">${esc(g.term)}</h1>
  <p class="lede">${esc(g.bridge)}</p>

  <h2>Definition</h2>
  <p>${rich(g.definition.en)}</p>
  <h2 lang="zh-CN">定义</h2>
  <p lang="zh-CN">${rich(g.definition.zh)}</p>

  <h2>In practice <span lang="zh-CN">· 在工具里</span></h2>
  <p>${rich(g.in_practice.en)}</p>
  <p lang="zh-CN">${rich(g.in_practice.zh)}</p>
  ${owner ? `<p class="facts">Implemented in <a href="/${owner.slug}">${esc(owner.name.en)}</a> · <a href="${owner.github}">source</a></p>` : ''}
  ${g.related?.length ? `<p class="terms">Related: ${g.related.map((r) => { const x = glossary.find((y) => y.slug === r); return x ? `<a href="/glossary/${x.slug}">${esc(x.term)}</a>` : '' }).join(' ')}</p>` : ''}
</article>
${roster(g.owner)}`
  write(route, page({ route, title, description: g.definition.en, ld, body }))
}

const glossaryIndex = () => {
  const route = '/glossary'
  const title = 'ZenStory AI glossary — 扫榜, 拆文, 去AI味, 漫剧 and other terms of the story pipeline'
  const description = 'Definitions, with English bridges, of the Chinese web-fiction and short-drama craft terms that ZenStory AI tools implement as concrete pipeline steps.'
  const ld = [
    orgNode,
    { '@type': 'DefinedTermSet', '@id': `${SITE}${route}#set`, name: 'ZenStory AI glossary', url: `${SITE}${route}`,
      hasDefinedTerm: glossary.map((g) => ({ '@type': 'DefinedTerm', name: g.term, alternateName: g.bridge, url: `${SITE}/glossary/${g.slug}` })) },
    breadcrumb([['ZenStory AI', SITE], ['Glossary', `${SITE}${route}`]]),
  ]
  const body = `
<article>
  <p class="eyebrow">Glossary <span lang="zh-CN">· 术语表</span></p>
  <h1>Terms of the story pipeline</h1>
  <p class="lede">${esc(description)}</p>
  <dl class="glossary">${glossary.map((g) => `
    <dt><a href="/glossary/${g.slug}" lang="zh-CN">${esc(g.term)}</a> <span>${esc(g.bridge)}</span></dt>
    <dd>${esc(g.definition.en.split('. ')[0])}.</dd>`).join('')}
  </dl>
</article>
${roster()}`
  write(route, page({ route, title, description, ogType: 'website', ld, body }))
}

// ---------- run ----------

mkdirSync(join(outDir, 'org'), { recursive: true })
copyFileSync(join(here, 'org-pages.css'), join(outDir, 'org/org.css'))
projectsIndex()
projects.forEach(projectPage)
glossaryIndex()
glossary.forEach(termPage)

const routes = ['/projects', ...projects.map((p) => `/${p.slug}`), '/glossary', ...glossary.map((g) => `/glossary/${g.slug}`)]
console.log(`org pages: wrote ${routes.length} routes to ${outDir}\n  ${routes.join('  ')}`)
