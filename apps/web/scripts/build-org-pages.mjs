#!/usr/bin/env node
/**
 * Generate the static ZenStory AI organization pages into the Vite output dir.
 *
 * Runs after `vite build`. Emits complete, crawler-readable HTML (title, meta,
 * Open Graph, JSON-LD, bilingual body) for:
 *   /org-home           – the apex organization homepage (internal output)
 *   /projects           – the six-project overview
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
import assert from 'node:assert/strict'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const outDir = resolve(process.argv[2] ?? join(webRoot, 'dist'))
const SITE = 'https://zenstory.ai'
const APP = 'https://app.zenstory.ai'

const org = JSON.parse(readFileSync(join(webRoot, 'content/org.json'), 'utf8'))
const projects = JSON.parse(readFileSync(join(webRoot, 'content/projects.json'), 'utf8'))
const glossary = JSON.parse(readFileSync(join(webRoot, 'content/glossary.json'), 'utf8'))
const guides = JSON.parse(readFileSync(join(webRoot, 'content/guides.json'), 'utf8'))
const comparisons = JSON.parse(readFileSync(join(webRoot, 'content/comparisons.json'), 'utf8'))
const guideRoutes = new Set()
for (const guide of guides) {
  assert.ok(projects.filter((p) => p.slug === guide.owner).length === 1 && /^[a-z0-9-]+$/.test(guide.slug), 'Invalid guide identity')
  const route = `/${guide.owner}/${guide.slug}`
  assert.ok(!guideRoutes.has(route), 'Duplicate guide route')
  guideRoutes.add(route)
}
const comparisonAxes = [
  ['fit', 'Best fit', '适合场景'],
  ['environment', 'Working environment', '工作环境'],
  ['configuration', 'Configuration responsibility', '配置责任'],
  ['files', 'Files and results', '文件与结果'],
  ['version', 'Version relationship', '版本关系'],
  ['review', 'What to review', '需要检查什么'],
]
const comparisonProjects = ['oh-story', 'dsh', 'workbench']
const comparisonRoutes = new Set()
for (const comparison of comparisons) {
  assert.ok(typeof comparison.slug === 'string' && /^[a-z0-9-]+$/.test(comparison.slug), 'Invalid comparison identity')
  const route = `/compare/${comparison.slug}`
  assert.ok(!comparisonRoutes.has(route), 'Duplicate comparison route')
  comparisonRoutes.add(route)
  assert.deepEqual(comparison.options?.map((option) => option.project), comparisonProjects, 'Invalid comparison options')
  for (const field of ['title', 'answer', 'disclosure']) {
    assert.ok(comparison[field]?.en?.trim() && comparison[field]?.zh?.trim(), 'Invalid comparison content')
  }
  assert.match(comparison.checked_on, /^\d{4}-\d{2}-\d{2}$/, 'Invalid comparison content')
  for (const option of comparison.options) {
    assert.ok(projects.some((project) => project.slug === option.project), 'Invalid comparison options')
    for (const [field] of comparisonAxes) {
      assert.ok(option[field]?.en?.trim() && option[field]?.zh?.trim(), 'Invalid comparison content')
    }
    assert.ok(option.sources?.en?.length && option.sources?.zh?.length, 'Invalid comparison content')
  }
  for (const field of ['checklist', 'boundaries']) {
    assert.ok(comparison[field]?.en?.length && comparison[field]?.zh?.length, 'Invalid comparison content')
  }
}

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
    <a class="nav-app" href="${APP}">Open app</a>
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

const list = (items, lang) => `<ul>${items.map((i) => `<li${lang === 'zh' ? ' lang="zh-CN"' : ''}>${rich(i)}</li>`).join('')}</ul>`
const steps = (items, lang) => `<ol class="steps">${items.map(([k, v]) => `<li${lang === 'zh' ? ' lang="zh-CN"' : ''}><b>${rich(k)}</b> ${rich(v)}</li>`).join('')}</ol>`
const comparisonLinks = (project) => comparisons.filter((comparison) => comparison.options.some((option) => option.project === project))

// ---------- organization homepage ----------

const taskChoices = [
  ['Write web fiction', '写网文', 'oh-story'],
  ['Produce a short drama or motion comic', '制作短剧或漫剧', 'drama-skills'],
  ['Adapt a novel into a playable game', '把小说改编成可玩的游戏', 'novel-to-game'],
  ['Turn footage into a narrated recap', '把视频做成解说成片', 'video-recap'],
  ['Use the story stack in DeepSeek Harness', '在 DeepSeek Harness 中使用故事工具链', 'dsh'],
  ['Write in a hosted browser workspace', '在浏览器工作台中写作', 'workbench'],
]

const homePage = () => {
  const route = '/'
  const title = 'ZenStory AI — Open-source tools for creating and adapting stories'
  const description = org.canonical.en
  const ld = [
    orgNode,
    {
      '@type': 'WebSite', '@id': `${SITE}/#website`, name: org.name, url: SITE,
      description, publisher: { '@id': `${SITE}/#org` }, inLanguage: ['en', 'zh-CN'],
    },
  ]
  const body = `
<article class="home">
  <p class="eyebrow">${esc(org.tagline.en)} <span lang="zh-CN">· ${esc(org.tagline.zh)}</span></p>
  <h1>ZenStory AI turns stories into many forms</h1>
  <p class="lede">${esc(org.canonical.en)}</p>
  <p class="lede" lang="zh-CN">${esc(org.canonical.zh)}</p>
  <p class="actions home-actions">
    <a class="btn" href="/projects">Explore the six projects</a>
    ${comparisons.map((comparison) => `<a class="btn ghost" href="/compare/${comparison.slug}">${esc(comparison.title.en)}</a>`).join('')}
    <a class="btn ghost" href="${APP}">Open the web workbench</a>
  </p>
  <p class="migration-note">The hosted writing workbench now lives at <a href="${APP}">app.zenstory.ai</a>. You may need to sign in again; account data is not copied through this page.<span lang="zh-CN">托管写作工作台现位于 app.zenstory.ai。你可能需要重新登录；此页面不会传递账户数据。</span></p>

  <section aria-labelledby="choose-h">
    <h2 id="choose-h">Choose by task <span lang="zh-CN">· 按任务选择</span></h2>
    <div class="task-grid">${taskChoices.map(([en, zh, slug]) => `
      <a class="task" href="/${slug}"><strong>${esc(en)}</strong><span lang="zh-CN">${esc(zh)}</span></a>`).join('')}
    </div>
  </section>

  <section aria-labelledby="projects-h">
    <h2 id="projects-h">Six open-source projects <span lang="zh-CN">· 六个开源项目</span></h2>
    <div class="project-grid">${projects.map((p) => `
      <article class="project-card">
        <p class="eyebrow">${esc(p.format.en)} <span lang="zh-CN">· ${esc(p.format.zh)}</span></p>
        <h3><a href="/${p.slug}">${esc(p.name.en)}</a></h3>
        <p>${esc(p.tagline.en)}</p>
        <p lang="zh-CN">${esc(p.tagline.zh)}</p>
        <p class="facts">${p.stars ? `${p.stars.toLocaleString('en-US')} GitHub stars as of ${esc(org.proof.as_of)} · ` : ''}${p.skills ? `${p.skills} skills · ` : ''}${esc(org.proof.license)} license</p>
        ${p.install ? `<code class="install">${esc(p.install)}</code>` : ''}
        <p class="card-actions"><a href="/${p.slug}">Project details</a><a href="${p.github}">Source on GitHub</a>${p.slug === 'workbench' ? `<a href="${APP}">Open app</a>` : ''}</p>
      </article>`).join('')}
    </div>
  </section>

  <section aria-labelledby="model-h">
    <h2 id="model-h">How the pieces fit together <span lang="zh-CN">· 项目如何协作</span></h2>
    ${steps(org.model.en)}
    ${steps(org.model.zh, 'zh')}
    <p class="facts">${org.proof.stars_total.toLocaleString('en-US')} GitHub stars across the organization as of ${esc(org.proof.as_of)}. All repositories listed here are ${esc(org.proof.license)}-licensed.</p>
  </section>
</article>`
  write('/org-home', page({ route, title, description, ogType: 'website', ld, body }))
}

// ---------- project pages ----------

const projectPage = (p) => {
  const route = `/${p.slug}`
  const title = `${p.name.en} — ${p.tagline.en.replace(/\.$/, '')} | ZenStory AI`
  const facts = [
    p.stars ? `${p.stars.toLocaleString('en-US')} GitHub stars as of ${org.proof.as_of}` : null,
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

  ${comparisonLinks(p.slug).length ? `<h2>Compare writing workflows <span lang="zh-CN">· 比较写作环境</span></h2>
  <ul>${comparisonLinks(p.slug).map((comparison) => `<li><a href="/compare/${comparison.slug}">${esc(comparison.title.en)} <span lang="zh-CN">· ${esc(comparison.title.zh)}</span></a></li>`).join('')}</ul>` : ''}

  <h2>Source notes <span lang="zh-CN">· 来源与边界</span></h2>
  <p class="facts">Source checked ${esc(p.sources.checked_on)} <span lang="zh-CN">· 源码核对日期</span>. Links identify the reviewed version, not a guarantee about later releases.</p>
  <div class="cols">${list(p.sources.en)}${list(p.sources.zh, 'zh')}</div>

  ${guides.some((g) => g.owner === p.slug) ? `<h2>Practical guides <span lang="zh-CN">· 实用指南</span></h2>
  <ul>${guides.filter((g) => g.owner === p.slug).map((g) => `<li><a href="/${g.owner}/${g.slug}">${esc(g.title.en)} <span lang="zh-CN">· ${esc(g.title.zh)}</span></a></li>`).join('')}</ul>` : ''}

  ${p.vocabulary?.length ? `<h2>Terms it uses</h2><p class="terms">${p.vocabulary.map((t) => {
    const g = glossary.find((x) => x.term === t)
    return g ? `<a href="/glossary/${g.slug}">${esc(t)}</a>` : `<span>${esc(t)}</span>`
  }).join(' ')}</p>` : ''}
</article>
${roster(p.slug)}`
  write(route, page({ route, title, description: p.definition.en, ogType: 'website', ld, body }))
}

// ---------- task guides ----------

const guidePage = (g) => {
  const owner = projects.find((p) => p.slug === g.owner)
  const route = `/${g.owner}/${g.slug}`
  const ld = [
    orgNode,
    { '@type': 'TechArticle', '@id': `${SITE}${route}#article`, url: `${SITE}${route}`,
      headline: g.title.en, description: g.answer.en, inLanguage: ['en', 'zh-CN'],
      dateModified: g.checked_on, publisher: { '@id': `${SITE}/#org` } },
    breadcrumb([['ZenStory AI', SITE], [owner.name.en, `${SITE}/${owner.slug}`], [g.title.en, `${SITE}${route}`]]),
  ]
  const body = `
<article class="guide">
  <p class="eyebrow">Practical guide <span lang="zh-CN">· 实用指南</span></p>
  <h1>${esc(g.title.en)}</h1>
  <p class="lede" lang="zh-CN">${esc(g.title.zh)}</p>
  <p class="facts">Source checked ${esc(g.checked_on)} <span lang="zh-CN">· 源码核对日期</span></p>
  <p>${rich(g.answer.en)}</p><p lang="zh-CN">${rich(g.answer.zh)}</p>
  <p class="actions"><a class="btn ghost" href="/${owner.slug}">${esc(owner.name.en)}</a><a class="btn ghost" href="${owner.github}">Source on GitHub</a></p>
  <h2>Before you start <span lang="zh-CN">· 开始之前</span></h2>
  ${list(g.prerequisites.en)}${list(g.prerequisites.zh, 'zh')}
  <h2>Steps <span lang="zh-CN">· 操作步骤</span></h2>
  ${steps(g.steps.en)}${steps(g.steps.zh, 'zh')}
  <h2>Example <span lang="zh-CN">· 示例</span></h2>
  <pre><code>${esc(g.example.en)}</code></pre><pre lang="zh-CN"><code>${esc(g.example.zh)}</code></pre>
  <h2>Expected files <span lang="zh-CN">· 预期文件</span></h2>
  ${list(g.outputs.en)}${list(g.outputs.zh, 'zh')}
  <h2>Verify the result <span lang="zh-CN">· 验证结果与边界</span></h2>
  ${list(g.verification.en)}${list(g.verification.zh, 'zh')}
  <h2>Versioned sources <span lang="zh-CN">· 版本化来源</span></h2>
  ${list(g.sources.en)}${list(g.sources.zh, 'zh')}
</article>`
  write(route, page({ route, title: `${g.title.en} | ZenStory AI`, description: g.answer.en, ld, body }))
}

// ---------- first-party comparisons ----------

const comparisonPage = (comparison) => {
  const route = `/compare/${comparison.slug}`
  const url = `${SITE}${route}`
  const optionProject = (option) => projects.find((project) => project.slug === option.project)
  const axisList = (field, language) => `<ul>${comparison.options.map((option) => {
    const project = optionProject(option)
    return `<li${language === 'zh' ? ' lang="zh-CN"' : ''}><b><a href="/${project.slug}">${esc(project.name[language])}</a></b> ${rich(option[field][language])}</li>`
  }).join('')}</ul>`
  const ld = [
    orgNode,
    { '@type': 'TechArticle', '@id': `${url}#article`, url,
      headline: comparison.title.en, description: comparison.answer.en,
      inLanguage: ['en', 'zh-CN'], dateModified: comparison.checked_on,
      publisher: { '@id': `${SITE}/#org` } },
    breadcrumb([['ZenStory AI', SITE], [comparison.title.en, url]]),
  ]
  const body = `
<article class="comparison">
  <p class="eyebrow">First-party comparison <span lang="zh-CN">· 自有项目选择指南</span></p>
  <h1>${esc(comparison.title.en)}</h1>
  <p class="lede" lang="zh-CN">${esc(comparison.title.zh)}</p>
  <p class="facts">Source checked ${esc(comparison.checked_on)} <span lang="zh-CN">· 源码核对日期</span></p>
  <p>${rich(comparison.answer.en)}</p>
  <p lang="zh-CN">${rich(comparison.answer.zh)}</p>
  <aside class="migration-note" aria-label="First-party disclosure">
    <p><strong>First-party disclosure.</strong> ${rich(comparison.disclosure.en)}</p>
    <p lang="zh-CN"><strong>自有项目披露。</strong> ${rich(comparison.disclosure.zh)}</p>
  </aside>
  <nav class="terms" aria-label="Comparison axes">
    ${comparisonAxes.map(([field, en, zh]) => `<a href="#axis-${field}">${esc(en)} <span lang="zh-CN">· ${esc(zh)}</span></a>`).join(' ')}
  </nav>
  ${comparisonAxes.map(([field, en, zh]) => `<section aria-labelledby="axis-${field}">
    <h2 id="axis-${field}">${esc(en)} <span lang="zh-CN">· ${esc(zh)}</span></h2>
    <div class="cols">${axisList(field, 'en')}${axisList(field, 'zh')}</div>
  </section>`).join('')}
  <h2>Small reversible trial <span lang="zh-CN">· 小范围可回退试用</span></h2>
  <div class="cols">${list(comparison.checklist.en)}${list(comparison.checklist.zh, 'zh')}</div>
  <h2>Comparison boundaries <span lang="zh-CN">· 比较边界</span></h2>
  <div class="cols">${list(comparison.boundaries.en)}${list(comparison.boundaries.zh, 'zh')}</div>
  <h2>Versioned first-party sources <span lang="zh-CN">· 版本化第一方来源</span></h2>
  ${comparison.options.map((option) => {
    const project = optionProject(option)
    return `<section aria-labelledby="sources-${project.slug}">
      <h3 id="sources-${project.slug}"><a href="/${project.slug}">${esc(project.name.en)}</a> <span lang="zh-CN">· ${esc(project.name.zh)}</span></h3>
      <div class="cols">${list(option.sources.en)}${list(option.sources.zh, 'zh')}</div>
    </section>`
  }).join('')}
</article>`
  write(route, page({ route, title: `${comparison.title.en} | ZenStory AI`, description: comparison.answer.en, ld, body }))
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
  <p class="actions">${comparisons.map((comparison) => `<a class="btn ghost" href="/compare/${comparison.slug}">${esc(comparison.title.en)}</a>`).join('')}</p>

  <h2>Start with what you want to make <span lang="zh-CN">· 从目标开始</span></h2>
  <div class="cards">${projects.map((p) => `
    <a class="card" href="/${p.slug}">
      <p class="eyebrow">${esc(p.format.en)}</p>
      <h3>${esc(p.name.en)}</h3>
      <p>${esc(p.tagline.en)}</p>
      <p lang="zh-CN">${esc(p.tagline.zh)}</p>
      <p class="facts">${p.stars ? `${p.stars.toLocaleString('en-US')} ★ as of ${esc(org.proof.as_of)}` : ''}${p.skills ? ` · ${p.skills} skills` : ''}</p>
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
homePage()
projectsIndex()
projects.forEach(projectPage)
guides.forEach(guidePage)
comparisons.forEach(comparisonPage)
glossaryIndex()
glossary.forEach(termPage)

const routes = ['/org-home', '/projects', ...projects.map((p) => `/${p.slug}`), ...guideRoutes, ...comparisonRoutes, '/glossary', ...glossary.map((g) => `/glossary/${g.slug}`)]
console.log(`org pages: wrote ${routes.length} routes to ${outDir}\n  ${routes.join('  ')}`)
