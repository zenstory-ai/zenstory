#!/usr/bin/env node
/**
 * Generate the static ZenStory AI organization pages into the Vite output dir.
 *
 * Runs after `vite build`. Emits complete, crawler-readable HTML (title, meta,
 * Open Graph, JSON-LD, bilingual body) for:
 *   /org-home            – the apex organization homepage (internal output)
 *   /projects            – the six-project overview
 *   /<project-slug>      – one page per project
 *   /<project>/<guide>   – one page per practical guide
 *   /guides              – the index of every practical guide
 *   /compare/<slug>      – first-party comparisons
 *   /glossary            – the terminology index
 *   /glossary/<term>     – one page per term
 *
 * Vercel matches these files on the filesystem before the SPA rewrite, so the
 * React app is untouched. Content lives in ../content/*.json.
 *
 * Both languages are always delivered in the initial HTML (English first, then
 * 中文). A small inline script reads a stored preference or navigator.language
 * and sets <html data-lang="en|zh">; the stylesheet then shows one language.
 * Without JavaScript (crawlers, the no-JS smoke tests) both languages render.
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

// ---------- bilingual helpers ----------
// `.l-en` / `.l-zh` mark the two renderings of the same content. With
// <html data-lang> set by the inline script, CSS shows one of them; without it
// (no JavaScript) both render, English first. Content that exists in one
// language only (Chinese glossary terms, code, links) is never wrapped.

const en = (html, tag = 'div') => `<${tag} class="l-en">${html}</${tag}>`
const zh = (html, tag = 'div') => `<${tag} class="l-zh" lang="zh-CN">${html}</${tag}>`
/** Two block renderings of the same content. */
const pair = (enHtml, zhHtml, cls = '') => `<div class="pair${cls ? ` ${cls}` : ''}">${en(enHtml)}${zh(zhHtml)}</div>`
/** Two inline renderings (headings, eyebrows, link labels). */
const both = (enText, zhText) => `${en(enText, 'span')}${zh(zhText, 'span')}`
/** Heading with an inline bilingual label; `id` first so tests can match `<h2 id="…">`. */
const heading = (level, enText, zhText, id) => `<h${level}${id ? ` id="${id}"` : ''}>${both(enText, zhText)}</h${level}>`

const list = (items, lang) => `<ul>${items.map((i) => `<li${lang === 'zh' ? ' lang="zh-CN"' : ''}>${rich(i)}</li>`).join('')}</ul>`
const steps = (items, lang) => `<ol class="steps">${items.map(([k, v]) => `<li${lang === 'zh' ? ' lang="zh-CN"' : ''}><b>${rich(k)}</b> ${rich(v)}</li>`).join('')}</ol>`
const comparisonLinks = (project) => comparisons.filter((comparison) => comparison.options.some((option) => option.project === project))
const guidesOf = (slug) => guides.filter((g) => g.owner === slug)
const guideLink = (g) => `<a href="/${g.owner}/${g.slug}">${both(esc(g.title.en), esc(g.title.zh))}</a>`

// ---------- layout ----------

// Runs before first paint so the chosen language does not flash. Classic script
// (no type="module"): organization pages must stay free of app JavaScript.
const langScript = `<script>(function(){var l=null;try{l=localStorage.getItem('zs-lang')}catch(e){}if(l!=='en'&&l!=='zh'&&l!=='both'){l=/^zh/i.test(navigator.language||'')?'zh':'en'}document.documentElement.setAttribute('data-lang',l)})()</script>`
const switchScript = `<script>(function(){var b=document.querySelectorAll('.lang-switch button');for(var i=0;i<b.length;i++){b[i].addEventListener('click',function(){var v=this.getAttribute('data-lang');document.documentElement.setAttribute('data-lang',v);try{localStorage.setItem('zs-lang',v)}catch(e){}})}})()</script>`

const langSwitch = `<div class="lang-switch" role="group" aria-label="Language · 语言">
      <button type="button" data-lang="en" lang="en" aria-label="English">EN</button>
      <button type="button" data-lang="zh" lang="zh-CN" aria-label="中文">中文</button>
    </div>`

const nav = `
<header class="top">
  <a class="brand" href="/"><img src="/brand/zenstory-ai-mark.svg" alt="" width="22" height="22"> ZenStory AI</a>
  <nav aria-label="Site">
    <a href="/projects">${both('Projects', '项目')}</a>
    <a href="/guides">${both('Guides', '指南')}</a>
    <a href="/glossary">${both('Glossary', '术语')}</a>
    <a href="/docs">${both('Docs', '文档')}</a>
    <a href="${org.github}">GitHub</a>
    ${langSwitch}
    <a class="nav-app" href="${APP}">${both('Open app', '打开工作台')}</a>
  </nav>
</header>`

const roster = (current) => `
<section class="roster" aria-labelledby="roster-h">
  ${heading(2, 'Part of ZenStory AI', 'ZenStory AI 项目', 'roster-h')}
  ${pair(`<p>${esc(org.canonical.en)}</p>`, `<p lang="zh-CN">${esc(org.canonical.zh)}</p>`)}
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
${langScript}
${jsonld({ '@context': 'https://schema.org', '@graph': ld })}
</head>
<body>
${nav}
<main>
${body}
</main>
${footer}
${switchScript}
</body>
</html>
`

// ---------- organization homepage ----------

const taskChoices = [
  ['Write web fiction', '写网文', 'oh-story'],
  ['Produce a short drama or motion comic', '制作短剧或漫剧', 'drama-skills'],
  ['Adapt a novel into a playable game', '把小说改编成可玩的游戏', 'novel-to-game'],
  ['Turn footage into a narrated recap', '把视频做成解说成片', 'video-recap'],
  ['Use the story stack in DeepSeek Harness', '在 DeepSeek Harness 中使用故事工具链', 'dsh'],
  ['Write in a hosted browser workspace', '在浏览器工作台中写作', 'workbench'],
]

const projectFacts = (p) => `${p.stars ? `${p.stars.toLocaleString('en-US')} GitHub stars as of ${esc(org.proof.as_of)} · ` : ''}${p.skills ? `${p.skills} skills · ` : ''}${esc(org.proof.license)} license`

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
  <p class="eyebrow">${both(esc(org.tagline.en), esc(org.tagline.zh))}</p>
  <h1>ZenStory AI turns stories into many forms</h1>
  ${pair(`<p class="lede">${esc(org.canonical.en)}</p>`, `<p class="lede" lang="zh-CN">${esc(org.canonical.zh)}</p>`)}
  <p class="actions home-actions">
    <a class="btn" href="/projects">${both('Explore the six projects', '浏览六个项目')}</a>
    ${comparisons.map((comparison) => `<a class="btn ghost" href="/compare/${comparison.slug}">${both(esc(comparison.title.en), esc(comparison.title.zh))}</a>`).join('')}
    <a class="btn ghost" href="${APP}">Open the web workbench</a>
  </p>
  <p class="migration-note">${both('The hosted writing workbench now lives at <a href="' + APP + '">app.zenstory.ai</a>. You may need to sign in again; account data is not copied through this page.', '托管写作工作台现位于 <a href="' + APP + '">app.zenstory.ai</a>。你可能需要重新登录；此页面不会传递账户数据。')}</p>

  <section aria-labelledby="choose-h">
    ${heading(2, 'Choose by task', '按任务选择', 'choose-h')}
    <div class="task-grid">${taskChoices.map(([en, zh, slug]) => `
      <a class="task" href="/${slug}"><strong>${both(esc(en), esc(zh))}</strong></a>`).join('')}
    </div>
  </section>

  <section aria-labelledby="guides-h">
    ${heading(2, 'Writing and adaptation guides', '创作与改编入门', 'guides-h')}
    <ul>${guides.map((g) => `<li>${guideLink(g)}</li>`).join('')}</ul>
    <p><a href="/guides">${both('All guides, grouped by project', '按项目查看全部指南')}</a></p>
  </section>

  <section aria-labelledby="projects-h">
    ${heading(2, 'Six open-source projects', '六个开源项目', 'projects-h')}
    <div class="project-grid">${projects.map((p) => `
      <article class="project-card">
        <p class="eyebrow">${both(esc(p.format.en), esc(p.format.zh))}</p>
        <h3><a href="/${p.slug}">${esc(p.name.en)}</a></h3>
        ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p lang="zh-CN">${esc(p.tagline.zh)}</p>`)}
        <p class="facts">${projectFacts(p)}</p>
        ${p.install ? `<code class="install">${esc(p.install)}</code>` : ''}
        <p class="card-actions"><a href="/${p.slug}">${both('Project details', '项目详情')}</a><a href="${p.github}">Source on GitHub</a>${p.slug === 'workbench' ? `<a href="${APP}">${both('Open app', '打开工作台')}</a>` : ''}</p>
      </article>`).join('')}
    </div>
  </section>

  <section aria-labelledby="model-h">
    ${heading(2, 'How the pieces fit together', '项目如何协作', 'model-h')}
    ${pair(steps(org.model.en), steps(org.model.zh, 'zh'))}
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
  const own = guidesOf(p.slug)
  const body = `
<article class="project">
  <p class="eyebrow">${both(esc(p.format.en), esc(p.format.zh))}</p>
  <h1>${esc(p.name.en)}</h1>
  ${pair(`<p class="lede">${esc(p.tagline.en)}</p>`, `<p class="lede" lang="zh-CN">${esc(p.tagline.zh)}</p>`)}
  <p class="facts">${facts.map(esc).join(' · ')}</p>
  <p class="actions">
    <a class="btn" href="${p.github}">Source on GitHub</a>
    ${p.readme_en && p.readme_en !== p.github ? `<a class="btn ghost" href="${p.readme_en}">English README</a>` : ''}
    ${p.install ? `<code class="install">${esc(p.install)}</code>` : ''}
    ${p.slug === 'workbench' ? `<a class="btn ghost" href="${p.entry}">${both('Open the workbench', '打开工作台')}</a>` : ''}
  </p>

  ${pair(`<h2>What it is</h2>
  <p>${rich(p.definition.en)}</p>`, `<h2 lang="zh-CN">它是什么</h2>
  <p lang="zh-CN">${rich(p.definition.zh)}</p>`)}

  ${heading(2, 'Who it is for', '适合谁')}
  ${pair(list(p.audience.en), list(p.audience.zh, 'zh'), 'cols')}

  ${heading(2, 'How it works', '流程')}
  ${pair(steps(p.method.en), steps(p.method.zh, 'zh'))}

  ${heading(2, 'What makes it different', '有什么不同')}
  ${pair(list(p.distinctive.en), list(p.distinctive.zh, 'zh'), 'cols')}

  ${comparisonLinks(p.slug).length ? `${heading(2, 'Compare writing workflows', '比较写作环境')}
  <ul>${comparisonLinks(p.slug).map((comparison) => `<li><a href="/compare/${comparison.slug}">${both(esc(comparison.title.en), esc(comparison.title.zh))}</a></li>`).join('')}</ul>` : ''}

  ${heading(2, 'Source notes', '来源与边界')}
  <p class="facts">${both(`Source checked ${esc(p.sources.checked_on)}. Links identify the reviewed version, not a guarantee about later releases.`, `源码核对日期 ${esc(p.sources.checked_on)}。链接指向已核对的版本，不保证后续版本一致。`)}</p>
  ${pair(list(p.sources.en), list(p.sources.zh, 'zh'), 'cols')}

  ${own.length ? `${heading(2, 'Practical guides', '实用指南')}
  <ul>${own.map((g) => `<li>${guideLink(g)}</li>`).join('')}</ul>` : ''}

  ${p.vocabulary?.length ? `${heading(2, 'Terms it uses', '相关术语')}<p class="terms">${p.vocabulary.map((t) => {
    const g = glossary.find((x) => x.term === t)
    return g ? `<a href="/glossary/${g.slug}" lang="zh-CN">${esc(t)}</a>` : `<span lang="zh-CN">${esc(t)}</span>`
  }).join(' ')}</p>` : ''}
</article>
${roster(p.slug)}`
  write(route, page({ route, title, description: p.definition.en, ogType: 'website', ld, body }))
}

// ---------- task guides ----------

const guideExample = (text, lang) => `
<section class="guide-example" lang="${lang === 'zh' ? 'zh-CN' : 'en'}" aria-labelledby="example-${lang}">
  <h3 id="example-${lang}">${lang === 'zh' ? '中文示例' : 'English example'}</h3>
  ${text.split(/\n{2,}/).map(paragraph => `<p>${esc(paragraph)}</p>`).join('\n  ')}
</section>`

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
  <p class="eyebrow">${both('Practical guide', '实用指南')}</p>
  <h1>${esc(g.title.en)}</h1>
  <p class="lede" lang="zh-CN">${esc(g.title.zh)}</p>
  <p class="facts">${both(`Source checked ${esc(g.checked_on)}`, `源码核对日期 ${esc(g.checked_on)}`)}</p>
  ${pair(`<p>${rich(g.answer.en)}</p>`, `<p lang="zh-CN">${rich(g.answer.zh)}</p>`, 'answer')}
  <p class="actions"><a class="btn ghost" href="/${owner.slug}">${esc(owner.name.en)}</a><a class="btn ghost" href="${owner.github}">Source on GitHub</a></p>
  <nav class="guide-contents" aria-label="On this page · 本页导航">
    <p><b>${both('On this page', '本页导航')}</b></p>
    <ul>
      <li><a href="#before-you-start">${both('Before you start', '开始之前')}</a></li>
      <li><a href="#steps">${both('Steps', '操作步骤')}</a></li>
      <li class="l-en"><a href="#example-en" lang="en">English example</a></li>
      <li class="l-zh"><a href="#example-zh" lang="zh-CN">中文示例</a></li>
      <li><a href="#expected-files">${both('Expected files', '预期文件')}</a></li>
      <li><a href="#verify-result">${both('Result boundaries', '结果边界')}</a></li>
      <li><a href="#sources">${both('Sources', '来源')}</a></li>
    </ul>
  </nav>
  ${heading(2, 'Before you start', '开始之前', 'before-you-start')}
  ${pair(list(g.prerequisites.en), list(g.prerequisites.zh, 'zh'))}
  ${heading(2, 'Steps', '操作步骤', 'steps')}
  ${pair(steps(g.steps.en), steps(g.steps.zh, 'zh'))}
  ${heading(2, 'Example', '示例', 'examples')}
  ${pair(guideExample(g.example.en, 'en'), guideExample(g.example.zh, 'zh'))}
  ${heading(2, 'Expected files', '预期文件', 'expected-files')}
  ${pair(list(g.outputs.en), list(g.outputs.zh, 'zh'))}
  ${heading(2, 'Verify the result', '验证结果与边界', 'verify-result')}
  ${pair(list(g.verification.en), list(g.verification.zh, 'zh'))}
  ${heading(2, 'Sources and version notes', '来源与版本说明', 'sources')}
  ${pair(list(g.sources.en), list(g.sources.zh, 'zh'))}
</article>`
  write(route, page({ route, title: `${g.title.en} | ZenStory AI`, description: g.answer.en, ld, body }))
}

// ---------- guides index ----------

const guidesIndex = () => {
  const route = '/guides'
  const title = `Practical guides — ${guides.length} bilingual, source-cited answers on writing, adapting and producing stories | ZenStory AI`
  const description = `${guides.length} practical guides from ZenStory AI, each bilingual (English and 中文), opening with a direct answer, with an original worked example and fixed-commit source citations. Grouped by project: Oh Story, Drama Skills, Novel to Game, Video Recap Skills, Oh Story DSH and the ZenStory workbench.`
  const ld = [
    orgNode,
    {
      '@type': 'ItemList', name: 'ZenStory AI practical guides', url: `${SITE}${route}`,
      itemListElement: guides.map((g, i) => ({ '@type': 'ListItem', position: i + 1, name: g.title.en, url: `${SITE}/${g.owner}/${g.slug}` })),
    },
    breadcrumb([['ZenStory AI', SITE], ['Guides', `${SITE}${route}`]]),
  ]
  const body = `
<article class="guides-index">
  <p class="eyebrow">${both('Practical guides', '实用指南')}</p>
  <h1>Practical guides</h1>
  ${pair(`<p class="lede">Each guide answers one working question, in English and 中文, with an original example and dated source citations to the project it describes.</p>`, `<p class="lede" lang="zh-CN">每篇指南回答一个具体的创作问题，中英双语，附原创示例和带日期的源码引用。</p>`)}
  ${projects.filter((p) => guidesOf(p.slug).length).map((p) => `
  <section aria-labelledby="guides-${p.slug}">
    ${heading(2, esc(p.name.en), esc(p.name.zh), `guides-${p.slug}`)}
    ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p lang="zh-CN">${esc(p.tagline.zh)}</p>`)}
    <ul>${guidesOf(p.slug).map((g) => `<li>${guideLink(g)}</li>`).join('')}</ul>
    <p class="facts"><a href="/${p.slug}">${both(`About ${esc(p.name.en)}`, `关于 ${esc(p.name.zh)}`)}</a></p>
  </section>`).join('')}
</article>
${roster()}`
  write(route, page({ route, title, description, ogType: 'website', ld, body }))
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
  <p class="eyebrow">${both('First-party comparison', '自有项目选择指南')}</p>
  <h1>${esc(comparison.title.en)}</h1>
  <p class="lede" lang="zh-CN">${esc(comparison.title.zh)}</p>
  <p class="facts">${both(`Source checked ${esc(comparison.checked_on)}`, `源码核对日期 ${esc(comparison.checked_on)}`)}</p>
  ${pair(`<p>${rich(comparison.answer.en)}</p>`, `<p lang="zh-CN">${rich(comparison.answer.zh)}</p>`, 'answer')}
  <aside class="migration-note" aria-label="First-party disclosure">
    ${pair(`<p><strong>First-party disclosure.</strong> ${rich(comparison.disclosure.en)}</p>`, `<p lang="zh-CN"><strong>自有项目披露。</strong> ${rich(comparison.disclosure.zh)}</p>`)}
  </aside>
  <nav class="terms" aria-label="Comparison axes">
    ${comparisonAxes.map(([field, en, zh]) => `<a href="#axis-${field}">${both(esc(en), esc(zh))}</a>`).join(' ')}
  </nav>
  ${comparisonAxes.map(([field, en, zh]) => `<section aria-labelledby="axis-${field}">
    ${heading(2, esc(en), esc(zh), `axis-${field}`)}
    ${pair(axisList(field, 'en'), axisList(field, 'zh'), 'cols')}
  </section>`).join('')}
  ${heading(2, 'Small reversible trial', '小范围可回退试用')}
  ${pair(list(comparison.checklist.en), list(comparison.checklist.zh, 'zh'), 'cols')}
  ${heading(2, 'Comparison boundaries', '比较边界')}
  ${pair(list(comparison.boundaries.en), list(comparison.boundaries.zh, 'zh'), 'cols')}
  ${heading(2, 'Versioned first-party sources', '版本化第一方来源')}
  ${comparison.options.map((option) => {
    const project = optionProject(option)
    return `<section aria-labelledby="sources-${project.slug}">
      <h3 id="sources-${project.slug}"><a href="/${project.slug}">${esc(project.name.en)}</a> <span class="l-zh" lang="zh-CN">${esc(project.name.zh)}</span></h3>
      ${pair(list(option.sources.en), list(option.sources.zh, 'zh'), 'cols')}
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
<article class="projects-index">
  <p class="eyebrow">${both(esc(org.tagline.en), esc(org.tagline.zh))}</p>
  <h1>Six open-source projects, one story stack</h1>
  ${pair(`<p class="lede">${esc(org.canonical.en)}</p>`, `<p class="lede" lang="zh-CN">${esc(org.canonical.zh)}</p>`)}
  <p class="actions">${comparisons.map((comparison) => `<a class="btn ghost" href="/compare/${comparison.slug}">${both(esc(comparison.title.en), esc(comparison.title.zh))}</a>`).join('')}</p>

  ${heading(2, 'Start with what you want to make', '从目标开始')}
  <div class="cards">${projects.map((p) => `
    <a class="card" href="/${p.slug}">
      <p class="eyebrow">${both(esc(p.format.en), esc(p.format.zh))}</p>
      <h3>${esc(p.name.en)}</h3>
      ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p lang="zh-CN">${esc(p.tagline.zh)}</p>`)}
      <p class="facts">${p.stars ? `${p.stars.toLocaleString('en-US')} ★ as of ${esc(org.proof.as_of)}` : ''}${p.skills ? ` · ${p.skills} skills` : ''}</p>
    </a>`).join('')}
  </div>

  ${heading(2, 'How the pieces fit together', '项目如何协作')}
  ${pair(steps(org.model.en), steps(org.model.zh, 'zh'))}

  ${heading(2, 'Runs inside the agents you already use', '在你已经使用的 Agent 里运行')}
  <p>${org.proof.harnesses.map(esc).join(' · ')}. ${both(`All ${esc(org.proof.license)}-licensed. ${org.proof.stars_total.toLocaleString('en-US')} GitHub stars across the organization as of ${esc(org.proof.as_of)}.`, `全部 ${esc(org.proof.license)} 许可。截至 ${esc(org.proof.as_of)}，组织合计 ${org.proof.stars_total.toLocaleString('en-US')} 个 GitHub star。`)}</p>
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
  <p class="eyebrow">${both('Glossary', '术语')}</p>
  <h1 lang="zh-CN">${esc(g.term)}</h1>
  <p class="lede">${esc(g.bridge)}</p>

  ${pair(`<h2>Definition</h2>
  <p>${rich(g.definition.en)}</p>`, `<h2 lang="zh-CN">定义</h2>
  <p lang="zh-CN">${rich(g.definition.zh)}</p>`)}

  ${heading(2, 'In practice', '在工具里')}
  ${pair(`<p>${rich(g.in_practice.en)}</p>`, `<p lang="zh-CN">${rich(g.in_practice.zh)}</p>`)}
  ${owner ? `<p class="facts">${both(`Implemented in <a href="/${owner.slug}">${esc(owner.name.en)}</a> · <a href="${owner.github}">source</a>`, `实现于 <a href="/${owner.slug}">${esc(owner.name.zh)}</a> · <a href="${owner.github}">源码</a>`)}</p>` : ''}
  ${g.related?.length ? `<p class="terms">${both('Related:', '相关术语：')} ${g.related.map((r) => { const x = glossary.find((y) => y.slug === r); return x ? `<a href="/glossary/${x.slug}" lang="zh-CN">${esc(x.term)}</a>` : '' }).join(' ')}</p>` : ''}
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
<article class="glossary-index">
  <p class="eyebrow">${both('Glossary', '术语表')}</p>
  <h1>Terms of the story pipeline</h1>
  ${pair(`<p class="lede">${esc(description)}</p>`, `<p class="lede" lang="zh-CN">网文与短剧创作中的行话，附英文对照，以及 ZenStory AI 工具把它们落实为具体流程步骤的方式。</p>`)}
  <dl class="glossary">${glossary.map((g) => `
    <dt><a href="/glossary/${g.slug}" lang="zh-CN">${esc(g.term)}</a> <span>${esc(g.bridge)}</span></dt>
    <dd>${both(esc(g.definition.en.split('. ')[0]) + '.', esc(g.definition.zh.split(/(?<=。)/)[0]))}</dd>`).join('')}
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
guidesIndex()
comparisons.forEach(comparisonPage)
glossaryIndex()
glossary.forEach(termPage)

const routes = ['/org-home', '/projects', ...projects.map((p) => `/${p.slug}`), ...guideRoutes, '/guides', ...comparisonRoutes, '/glossary', ...glossary.map((g) => `/glossary/${g.slug}`)]
console.log(`org pages: wrote ${routes.length} routes to ${outDir}\n  ${routes.join('  ')}`)
