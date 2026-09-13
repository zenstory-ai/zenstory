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

const num = (n) => Number(n).toLocaleString('en-US')

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
const guideList = (items) => `<ul class="guide-list">${items.map((g) => `<li>${guideLink(g)}</li>`).join('')}</ul>`

// ---------- small UI glyphs (inline, monochrome; the star keeps the brand cyan) ----------

const starGlyph = '<svg class="star" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M8 1.2c.62 2.6 1.66 3.64 4.26 4.26-2.6.62-3.64 1.66-4.26 4.26-.62-2.6-1.66-3.64-4.26-4.26 2.6-.62 3.64-1.66 4.26-4.26Z" fill="#22D3EE"/></svg>'
const arrowGlyph = '<span class="arrow" aria-hidden="true">→</span>'
const extGlyph = '<span class="arrow" aria-hidden="true">↗</span>'
const proof = (html, cls = '') => `<span class="proof${cls ? ` ${cls}` : ''}">${html}</span>`
const proofRow = (chips, asOf) => `<p class="proof-row">${chips.join('')}${asOf ? `<small class="asof">${both(`as of ${esc(asOf)}`, `截至 ${esc(asOf)}`)}</small>` : ''}</p>`

/** Copy affordance: hidden until the inline classic script finds a clipboard. */
const copyButton = (text) => `<button type="button" class="copy" data-copy="${esc(text)}" hidden><span class="copy-label">${both('Copy', '复制')}</span><span class="copy-done" hidden>${both('Copied', '已复制')}</span></button>`
const installBlock = (p, { label = true } = {}) => p.install ? `<div class="install-block">${label ? `<span class="install-label">${both(`Install ${esc(p.name.en)}`, `安装 ${esc(p.name.en)}`)}</span>` : ''}<div class="install-row"><code class="install">${esc(p.install)}</code>${copyButton(p.install)}</div></div>` : ''

// ---------- layout ----------

// Runs before first paint so the chosen language does not flash. Classic script
// (no type="module"): organization pages must stay free of app JavaScript.
const langScript = `<script>(function(){var l=null;try{l=localStorage.getItem('zs-lang')}catch(e){}if(l!=='en'&&l!=='zh'&&l!=='both'){l=/^zh/i.test(navigator.language||'')?'zh':'en'}document.documentElement.setAttribute('data-lang',l)})()</script>`
const switchScript = `<script>(function(){var b=document.querySelectorAll('.lang-switch button');for(var i=0;i<b.length;i++){b[i].addEventListener('click',function(){var v=this.getAttribute('data-lang');document.documentElement.setAttribute('data-lang',v);try{localStorage.setItem('zs-lang',v)}catch(e){}})}
if(navigator.clipboard&&navigator.clipboard.writeText){var c=document.querySelectorAll('button.copy');for(var j=0;j<c.length;j++){c[j].hidden=false;c[j].addEventListener('click',function(){var s=this;navigator.clipboard.writeText(s.getAttribute('data-copy')).then(function(){var l=s.querySelector('.copy-label'),d=s.querySelector('.copy-done');l.hidden=true;d.hidden=false;setTimeout(function(){l.hidden=false;d.hidden=true},1600)},function(){})})}}})()</script>`

const langSwitch = `<div class="lang-switch" role="group" aria-label="Language · 语言">
      <button type="button" data-lang="en" lang="en" aria-label="English">EN</button>
      <button type="button" data-lang="zh" lang="zh-CN" aria-label="中文">中文</button>
    </div>`

const brandMark = (size = 28) => `<img src="/brand/zenstory-ai-mark.svg" alt="" width="${size}" height="${size}">`

const navSection = (route) => {
  if (route === '/projects' || projects.some((p) => `/${p.slug}` === route)) return '/projects'
  if (route === '/guides' || guideRoutes.has(route)) return '/guides'
  if (route === '/glossary' || route.startsWith('/glossary/')) return '/glossary'
  return null
}

const nav = (route) => {
  const active = navSection(route)
  const item = (href, label) => `<a href="${href}"${active === href ? ' aria-current="page"' : ''}>${label}</a>`
  return `
<header class="top">
  <div class="wrap top-row">
    <a class="brand" href="/">${brandMark(28)}<span class="wordmark">ZenStory AI</span></a>
    <nav aria-label="Site">
      ${item('/projects', both('Projects', '项目'))}
      ${item('/guides', both('Guides', '指南'))}
      ${item('/glossary', both('Glossary', '术语'))}
      ${item('/docs', both('Docs', '文档'))}
      ${item(org.github, 'GitHub')}
    </nav>
    <div class="top-tools">
      ${langSwitch}
      <a class="nav-app" href="${APP}">${both('Open app', '打开工作台')}${arrowGlyph}</a>
    </div>
  </div>
</header>`
}

const roster = (current) => `
<section class="roster band-cream" aria-labelledby="roster-h">
  <div class="wrap">
  ${heading(2, 'Part of ZenStory AI', 'ZenStory AI 项目', 'roster-h')}
  ${pair(`<p>${esc(org.canonical.en)}</p>`, `<p lang="zh-CN">${esc(org.canonical.zh)}</p>`)}
  <table>
    <thead><tr><th>Project</th><th>Format</th><th>What it does</th></tr></thead>
    <tbody>${projects.map((p) => `
      <tr${p.slug === current ? ' class="here"' : ''}>
        <td><a href="/${p.slug}"${p.slug === current ? ' aria-current="page"' : ''}>${esc(p.name.en)}</a></td>
        <td>${esc(p.format.en)}</td>
        <td>${esc(p.tagline.en)}</td>
      </tr>`).join('')}
    </tbody>
  </table>
  </div>
</section>`

const footer = `
<footer class="bottom">
  <div class="wrap">
    <div class="foot-grid">
      <div class="foot-brand">
        <a class="brand" href="/">${brandMark(28)}<span class="wordmark">ZenStory AI</span></a>
        <p class="foot-desc">${both(esc(org.tagline.en), esc(org.tagline.zh))}</p>
        <p class="foot-desc">${both(`Open-source tools for creating and adapting stories, ${esc(org.proof.license)}-licensed.`, `用于创作与改编故事的开源工具，${esc(org.proof.license)} 许可。`)}</p>
        <p><a href="${org.github}">GitHub${extGlyph}</a></p>
      </div>
      <nav aria-label="Footer: projects">
        <h2 class="foot-h">${both('Projects', '项目')}</h2>
        <ul>${projects.map((p) => `<li><a href="/${p.slug}">${esc(p.name.en)}</a></li>`).join('')}</ul>
      </nav>
      <nav aria-label="Footer: learn">
        <h2 class="foot-h">${both('Learn', '学习')}</h2>
        <ul>
          <li><a href="/guides">${both('Guides', '实用指南')}</a></li>
          <li><a href="/glossary">${both('Glossary', '术语表')}</a></li>
          <li><a href="/llms.txt">llms.txt</a></li>
        </ul>
      </nav>
      <nav aria-label="Footer: workbench">
        <h2 class="foot-h">${both('Workbench', '工作台')}</h2>
        <ul>
          <li><a href="${APP}">${both('Open app', '打开工作台')}</a></li>
          <li><a href="/docs">${both('Workbench docs', '工作台文档')}</a></li>
          <li><a href="/privacy-policy">${both('Privacy policy', '隐私政策')}</a></li>
          <li><a href="/terms-of-service">${both('Terms of service', '服务条款')}</a></li>
        </ul>
      </nav>
    </div>
    <p class="copyright">© ${new Date().getUTCFullYear()} ZenStory AI · MIT-licensed open source · <a href="${org.github}">github.com/zenstory-ai</a> · <a href="/llms.txt">llms.txt</a></p>
  </div>
</footer>`

const FONTS = 'https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Plus+Jakarta+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap'

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
<meta name="theme-color" content="#081431">
<link rel="stylesheet" href="/org/org.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preload" as="style" href="${FONTS}">
<link rel="stylesheet" href="${FONTS}" media="print" onload="this.media='all'">
<noscript><link rel="stylesheet" href="${FONTS}"></noscript>
${langScript}
${jsonld({ '@context': 'https://schema.org', '@graph': ld })}
</head>
<body>
<a class="skip" href="#main">Skip to content · 跳到正文</a>
${nav(route)}
<main id="main">
${body}
</main>
${footer}
${switchScript}
</body>
</html>
`

// ---------- shared blocks ----------

const taskChoices = [
  ['Write web fiction', '写网文', 'oh-story'],
  ['Produce a short drama or motion comic', '制作短剧或漫剧', 'drama-skills'],
  ['Adapt a novel into a playable game', '把小说改编成可玩的游戏', 'novel-to-game'],
  ['Turn footage into a narrated recap', '把视频做成解说成片', 'video-recap'],
  ['Use the story stack in DeepSeek Harness', '在 DeepSeek Harness 中使用故事工具链', 'dsh'],
  ['Write in a hosted browser workspace', '在浏览器工作台中写作', 'workbench'],
]

/** Proof chips for one project: stars, skills, license (+ format on project heroes). */
const projectChips = (p, { format = false } = {}) => [
  p.stars ? proof(`${starGlyph}${num(p.stars)} GitHub stars`) : '',
  p.skills ? proof(`${p.skills} skills`) : '',
  proof(both(`${esc(org.proof.license)} license`, `${esc(org.proof.license)} 许可`)),
  format ? proof(both(`Format: ${esc(p.format.en)}`, `形态：${esc(p.format.zh)}`)) : '',
].filter(Boolean)

/** One card component shared by the home page and /projects. */
const projectCard = (p, eyebrow) => `
      <article class="project-card">
        <p class="eyebrow">${eyebrow}</p>
        <h3><a href="/${p.slug}">${esc(p.name.en)}</a></h3>
        ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p lang="zh-CN">${esc(p.tagline.zh)}</p>`, 'tagline')}
        ${proofRow([proof(both(esc(p.format.en), esc(p.format.zh)), 'format'), ...projectChips(p)], p.stars ? org.proof.as_of : null)}
        ${installBlock(p, { label: false })}
        <p class="card-actions"><a href="/${p.slug}">${both('Project details', '项目详情')}${arrowGlyph}</a><a href="${p.github}">Source on GitHub${extGlyph}</a>${p.slug === 'workbench' ? `<a href="${APP}">${both('Open app', '打开工作台')}${extGlyph}</a>` : ''}</p>
      </article>`

/** Hero diagram: idea → novel → short drama / game / video recap, labelled with the tools. */
const pipelineSvg = () => {
  const label = (x, y, enText, zhText, cls = '') => `<text x="${x}" y="${y - 3}" class="l-en ${cls}">${enText}</text><text x="${x}" y="${y + 13}" class="l-zh ${cls}" lang="zh-CN">${zhText}</text>`
  const node = (x, y, w, enText, zhText, tool) => `
    <rect x="${x}" y="${y}" width="${w}" height="56" rx="8" class="node"/>
    ${label(x + w / 2, y + 30, enText, zhText, 'node-label')}
    ${tool ? `<text x="${x + w / 2}" y="${y + 74}" class="tool">${tool}</text>` : ''}`
  const arrow = (d) => `<path d="${d}" class="edge" marker-end="url(#pipe-arrow)"/>`
  return `<svg class="pipe" viewBox="0 0 560 336" width="560" height="336" aria-hidden="true" focusable="false">
    <defs><marker id="pipe-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5 0 10z" class="edge-head"/></marker></defs>
    ${node(12, 132, 104, 'Idea', '灵感')}
    ${arrow('M118 160H184')}
    ${node(188, 132, 132, 'Novel', '小说', 'Oh Story · DSH · Workbench')}
    ${arrow('M322 160C360 160 360 44 402 44')}
    ${arrow('M322 160H402')}
    ${arrow('M322 160C360 160 360 276 402 276')}
    ${node(406, 16, 142, 'Short drama', '短剧 / 漫剧', 'Drama Skills')}
    ${node(406, 132, 142, 'Game', '互动游戏', 'Novel to Game')}
    ${node(406, 248, 142, 'Video recap', '解说视频', 'Video Recap Skills')}
  </svg>`
}

// ---------- organization homepage ----------

const projectFacts = (p) => `${p.stars ? `${num(p.stars)} GitHub stars as of ${esc(org.proof.as_of)} · ` : ''}${p.skills ? `${p.skills} skills · ` : ''}${esc(org.proof.license)} license`

const featuredGuideSlugs = ['agent-skills-for-writers', 'novel-opening', 'import-and-continue', 'revise-ai-prose', 'novel-to-short-drama', 'quick-start']

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
  const flagship = projects[0]
  const featured = featuredGuideSlugs.map((slug) => guides.find((g) => g.slug === slug)).filter(Boolean)
  const body = `
<article class="home">
  <div class="hero">
    <div class="wrap hero-grid">
      <div class="hero-copy">
        <p class="eyebrow">${both(esc(org.tagline.en), esc(org.tagline.zh))}</p>
        <h1>ZenStory AI turns stories into many forms</h1>
        ${pair(`<p class="lede">${esc(org.canonical.en)}</p>`, `<p class="lede" lang="zh-CN">${esc(org.canonical.zh)}</p>`)}
        <p class="actions home-actions">
          <a class="btn" href="/projects">${both('Explore the six projects', '浏览六个项目')}</a>
          <a class="btn ghost" href="${APP}">Open the web workbench</a>
        </p>
        ${installBlock(flagship)}
        ${proofRow([
          proof(`${starGlyph}${num(org.proof.stars_total)} GitHub stars`),
          proof(both(`${esc(org.proof.license)} license`, `${esc(org.proof.license)} 许可`)),
          proof(both(`${projects.length} projects`, `${projects.length} 个项目`)),
          proof(both(`${org.proof.harnesses.length} agent hosts`, `${org.proof.harnesses.length} 个 Agent 宿主`)),
        ], org.proof.as_of)}
        <p class="hero-aside">${en(`Not sure which? ${comparisons.map((comparison) => `<a href="/compare/${comparison.slug}">${esc(comparison.title.en)}</a>`).join('')}${arrowGlyph}`, 'span')}${zh(`不确定选哪个？${comparisons.map((comparison) => `<a href="/compare/${comparison.slug}">${esc(comparison.title.zh)}</a>`).join('')}${arrowGlyph}`, 'span')}</p>
      </div>
      <div class="hero-art">${pipelineSvg()}</div>
    </div>
  </div>

  <section class="band band-cream" aria-labelledby="choose-h">
    <div class="wrap">
    ${heading(2, 'Choose by task', '按任务选择', 'choose-h')}
    <p class="section-lede">${both('Six open-source projects, one story stack. Each card is one project; the label above it is the job it does.', '六个开源项目，一条故事工具链。每张卡片是一个项目，卡片上方的标签是它负责的任务。')}</p>
    <div class="project-grid">${projects.map((p) => {
      const [taskEn, taskZh] = taskChoices.find(([, , slug]) => slug === p.slug)
      return projectCard(p, both(esc(taskEn), esc(taskZh)))
    }).join('')}
    </div>
    </div>
  </section>

  <section class="band" aria-labelledby="guides-h">
    <div class="wrap">
    ${heading(2, 'Writing and adaptation guides', '创作与改编入门', 'guides-h')}
    <p class="section-lede">${both('Each guide answers one working question, in English and 中文, with an original example and dated source citations.', '每篇指南回答一个具体的创作问题，中英双语，附原创示例和带日期的源码引用。')}</p>
    <h3 class="sub-h">${both('Start here', '先看这些')}</h3>
    <div class="guide-cards">${featured.map((g) => {
      const owner = projects.find((p) => p.slug === g.owner)
      return `
      <a class="guide-card" href="/${g.owner}/${g.slug}">
        <span class="eyebrow">${esc(owner.name.en)}</span>
        <span class="guide-card-title">${both(esc(g.title.en), esc(g.title.zh))}</span>
        <span class="facts">${both(`Checked ${esc(g.checked_on)}`, `核对于 ${esc(g.checked_on)}`)}</span>
      </a>`
    }).join('')}
    </div>
    <h3 class="sub-h">${both(`All ${guides.length} guides, by project`, `全部 ${guides.length} 篇指南，按项目分组`)}</h3>
    <div class="guide-groups">${projects.filter((p) => guidesOf(p.slug).length).map((p) => `
      <div class="guide-group${guidesOf(p.slug).length > 6 ? ' wide' : ''}">
        <h4><a href="/${p.slug}">${esc(p.name.en)}</a> <span class="count">${guidesOf(p.slug).length}</span></h4>
        ${guideList(guidesOf(p.slug))}
      </div>`).join('')}
    </div>
    <p class="more"><a href="/guides">${both('All guides, grouped by project', '按项目查看全部指南')}${arrowGlyph}</a></p>
    </div>
  </section>

  <section class="band band-cream" aria-labelledby="model-h">
    <div class="wrap">
    ${heading(2, 'How the pieces fit together', '项目如何协作', 'model-h')}
    <div class="model">${pair(steps(org.model.en), steps(org.model.zh, 'zh'), 'cols')}</div>
    <p class="facts">${num(org.proof.stars_total)} GitHub stars across the organization as of ${esc(org.proof.as_of)}. All repositories listed here are ${esc(org.proof.license)}-licensed.</p>
    </div>
  </section>

  <div class="wrap">
    <p class="migration-note">${both('The hosted writing workbench now lives at <a href="' + APP + '">app.zenstory.ai</a>. You may need to sign in again; account data is not copied through this page.', '托管写作工作台现位于 <a href="' + APP + '">app.zenstory.ai</a>。你可能需要重新登录；此页面不会传递账户数据。')}</p>
  </div>
</article>`
  write('/org-home', page({ route, title, description, ogType: 'website', ld, body }))
}

// ---------- project pages ----------

const needBlock = (p) => {
  const isPack = Boolean(p.install)
  const host = p.slug === 'workbench'
    ? both(`A browser. The workbench runs at <a href="${APP}">app.zenstory.ai</a>.`, `一个浏览器。工作台运行在 <a href="${APP}">app.zenstory.ai</a>。`)
    : p.slug === 'dsh'
      ? both('DeepSeek Harness (DSH) as the host.', '以 DeepSeek Harness（DSH）为宿主。')
      : both(`An agent host: ${org.proof.harnesses.map(esc).join(', ')}.`, `一个 Agent 宿主：${org.proof.harnesses.map(esc).join('、')}。`)
  return `
  <section class="need" aria-labelledby="need-h">
    ${heading(2, 'What you need', '你需要什么', 'need-h')}
    <ul class="need-list">
      <li><b>${both('License', '许可')}</b><span>${both(`${esc(org.proof.license)}, open source.`, `${esc(org.proof.license)}，开源。`)}</span></li>
      <li><b>${both(p.slug === 'workbench' ? 'Where it runs' : 'Host', p.slug === 'workbench' ? '在哪里运行' : '宿主')}</b><span>${host}</span></li>
      ${isPack ? `<li><b>${both('Install', '安装')}</b><span>${both('One command:', '一条命令：')} <code>${esc(p.install)}</code></span></li>` : ''}
      ${p.slug !== 'workbench' ? `<li><b>${both('Or in the browser', '或在浏览器里')}</b><span>${both(`The separate <a href="/workbench">ZenStory Workbench</a> at <a href="${APP}">app.zenstory.ai</a>.`, `独立的 <a href="/workbench">ZenStory 工作台</a>：<a href="${APP}">app.zenstory.ai</a>。`)}</span></li>` : ''}
    </ul>
  </section>`
}

const startBlock = (p, own) => {
  if (!p.install || !p.entry) return ''
  return `
  <section class="start" aria-labelledby="start-h">
    ${heading(2, 'Start in 3 steps', '三步开始', 'start-h')}
    <ol class="start-list">
      <li><b>${both('Install', '安装')}</b><span><code>${esc(p.install)}</code></span></li>
      <li><b>${both('Entry command', '入口命令')}</b><span>${both(`Run <code>${esc(p.entry)}</code> in your agent host.`, `在 Agent 宿主中运行 <code>${esc(p.entry)}</code>。`)}</span></li>
      <li><b>${both('Follow a guide', '按指南操作')}</b><span>${own.length ? both(`Pick one of the ${own.length} guides below for your first task.`, `从下方 ${own.length} 篇指南中选一个，完成第一个任务。`) : both('Read the source README for the first task.', '按源码 README 完成第一个任务。')}</span></li>
    </ol>
  </section>`
}

const projectPage = (p) => {
  const route = `/${p.slug}`
  const title = `${p.name.en} — ${p.tagline.en.replace(/\.$/, '')} | ZenStory AI`
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
  <header class="page-hero">
    <div class="wrap">
      <p class="crumbs"><a href="/">ZenStory AI</a> <span aria-hidden="true">/</span> <a href="/projects">${both('Projects', '项目')}</a></p>
      <p class="eyebrow">${both(esc(p.format.en), esc(p.format.zh))}</p>
      <h1>${esc(p.name.en)}</h1>
      ${pair(`<p class="lede">${esc(p.tagline.en)}</p>`, `<p class="lede" lang="zh-CN">${esc(p.tagline.zh)}</p>`)}
      ${proofRow(projectChips(p, { format: true }), p.stars ? org.proof.as_of : null)}
      <p class="actions">
        <a class="btn" href="${p.github}">Source on GitHub${extGlyph}</a>
        ${p.readme_en && p.readme_en !== p.github ? `<a class="btn ghost" href="${p.readme_en}">English README${extGlyph}</a>` : ''}
        ${p.slug === 'workbench' ? `<a class="btn ghost" href="${p.entry}">${both('Open the workbench', '打开工作台')}${extGlyph}</a>` : ''}
      </p>
      ${installBlock(p)}
    </div>
  </header>

  <div class="wrap page-body">
  ${pair(`<h2>What it is</h2>
  <p>${rich(p.definition.en)}</p>`, `<h2 lang="zh-CN">它是什么</h2>
  <p lang="zh-CN">${rich(p.definition.zh)}</p>`, 'definition')}

  ${needBlock(p)}
  ${startBlock(p, own)}

  ${own.length ? `<section aria-labelledby="guides-h">${heading(2, 'Practical guides', '实用指南', 'guides-h')}
  ${guideList(own)}</section>` : ''}

  <section aria-labelledby="method-h">
  ${heading(2, 'How it works', '流程', 'method-h')}
  <div class="qa">${pair(steps(p.method.en), steps(p.method.zh, 'zh'), 'cols')}</div>
  </section>

  ${heading(2, 'What makes it different', '有什么不同')}
  ${pair(list(p.distinctive.en), list(p.distinctive.zh, 'zh'), 'cols')}

  ${heading(2, 'Who it is for', '适合谁')}
  ${pair(list(p.audience.en), list(p.audience.zh, 'zh'), 'cols')}

  ${comparisonLinks(p.slug).length ? `${heading(2, 'Compare writing workflows', '比较写作环境')}
  <ul class="guide-list">${comparisonLinks(p.slug).map((comparison) => `<li><a href="/compare/${comparison.slug}">${both(esc(comparison.title.en), esc(comparison.title.zh))}</a></li>`).join('')}</ul>` : ''}

  ${p.vocabulary?.length ? `${heading(2, 'Terms it uses', '相关术语')}<p class="terms">${p.vocabulary.map((t) => {
    const g = glossary.find((x) => x.term === t)
    return g ? `<a href="/glossary/${g.slug}" lang="zh-CN">${esc(t)}</a>` : `<span lang="zh-CN">${esc(t)}</span>`
  }).join(' ')}</p>` : ''}

  <section class="sources" aria-labelledby="sources-h">
  ${heading(2, 'Source notes', '来源与边界', 'sources-h')}
  <p class="facts">${both(`Source checked ${esc(p.sources.checked_on)}. Links identify the reviewed version, not a guarantee about later releases.`, `源码核对日期 ${esc(p.sources.checked_on)}。链接指向已核对的版本，不保证后续版本一致。`)}</p>
  ${pair(list(p.sources.en), list(p.sources.zh, 'zh'), 'cols')}
  </section>
  </div>
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
  <header class="guide-head">
  <p class="eyebrow">${both('Practical guide', '实用指南')}</p>
  <h1>${esc(g.title.en)}</h1>
  <p class="lede" lang="zh-CN">${esc(g.title.zh)}</p>
  <p class="facts">${both(`Source checked ${esc(g.checked_on)}`, `源码核对日期 ${esc(g.checked_on)}`)}</p>
  ${pair(`<p>${rich(g.answer.en)}</p>`, `<p lang="zh-CN">${rich(g.answer.zh)}</p>`, 'answer')}
  <p class="actions guide-actions"><a class="crumb" href="/${owner.slug}">${both('Part of', '所属项目')} <b>${esc(owner.name.en)}</b>${arrowGlyph}</a><a class="crumb" href="${owner.github}">Source on GitHub${extGlyph}</a></p>
  </header>
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
  <div class="guide-body">
  ${heading(2, 'Before you start', '开始之前', 'before-you-start')}
  ${pair(list(g.prerequisites.en), list(g.prerequisites.zh, 'zh'), 'cols')}
  ${heading(2, 'Steps', '操作步骤', 'steps')}
  ${pair(steps(g.steps.en), steps(g.steps.zh, 'zh'), 'cols')}
  ${heading(2, 'Example', '示例', 'examples')}
  ${pair(guideExample(g.example.en, 'en'), guideExample(g.example.zh, 'zh'), 'cols')}
  ${heading(2, 'Expected files', '预期文件', 'expected-files')}
  ${pair(list(g.outputs.en), list(g.outputs.zh, 'zh'), 'cols')}
  ${heading(2, 'Verify the result', '验证结果与边界', 'verify-result')}
  ${pair(list(g.verification.en), list(g.verification.zh, 'zh'), 'cols')}
  ${heading(2, 'Sources and version notes', '来源与版本说明', 'sources')}
  ${pair(list(g.sources.en), list(g.sources.zh, 'zh'), 'cols')}
  <p class="actions guide-end"><a class="btn ghost" href="/${owner.slug}">${both(`More about ${esc(owner.name.en)}`, `了解 ${esc(owner.name.en)}`)}${arrowGlyph}</a><a class="btn ghost" href="/guides">${both('All guides', '全部指南')}${arrowGlyph}</a></p>
  </div>
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
  const groups = projects.filter((p) => guidesOf(p.slug).length)
  const body = `
<article class="guides-index">
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${both('Practical guides', '实用指南')}</p>
      <h1>Practical guides</h1>
      ${pair(`<p class="lede">Each guide answers one working question, in English and 中文, with an original example and dated source citations to the project it describes.</p>`, `<p class="lede" lang="zh-CN">每篇指南回答一个具体的创作问题，中英双语，附原创示例和带日期的源码引用。</p>`)}
      <p class="terms jump" aria-label="Jump to project">${groups.map((p) => `<a href="#guides-${p.slug}">${esc(p.name.en)} <span class="count">${guidesOf(p.slug).length}</span></a>`).join(' ')}</p>
    </div>
  </header>
  <div class="wrap page-body wide">
  ${groups.map((p) => `
  <section class="guide-section" aria-labelledby="guides-${p.slug}">
    <div class="guide-section-head">
    ${heading(2, esc(p.name.en), esc(p.name.zh), `guides-${p.slug}`)}
    ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p lang="zh-CN">${esc(p.tagline.zh)}</p>`, 'tagline')}
    <p class="facts"><a href="/${p.slug}">${both(`About ${esc(p.name.en)}`, `关于 ${esc(p.name.zh)}`)}${arrowGlyph}</a></p>
    </div>
    ${guideList(guidesOf(p.slug))}
  </section>`).join('')}
  </div>
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
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${both('First-party comparison', '自有项目选择指南')}</p>
      <h1>${esc(comparison.title.en)}</h1>
      <p class="lede" lang="zh-CN">${esc(comparison.title.zh)}</p>
      <p class="facts">${both(`Source checked ${esc(comparison.checked_on)}`, `源码核对日期 ${esc(comparison.checked_on)}`)}</p>
      ${pair(`<p>${rich(comparison.answer.en)}</p>`, `<p lang="zh-CN">${rich(comparison.answer.zh)}</p>`, 'answer')}
      <p class="terms options" aria-label="Compared projects">${comparison.options.map((option) => { const project = optionProject(option); return `<a href="/${project.slug}">${esc(project.name.en)}</a>` }).join(' ')}</p>
    </div>
  </header>
  <div class="wrap page-body wide">
  <aside class="migration-note" aria-label="First-party disclosure">
    ${pair(`<p><strong>First-party disclosure.</strong> ${rich(comparison.disclosure.en)}</p>`, `<p lang="zh-CN"><strong>自有项目披露。</strong> ${rich(comparison.disclosure.zh)}</p>`)}
  </aside>
  <nav class="terms axes" aria-label="Comparison axes">
    ${comparisonAxes.map(([field, en, zh]) => `<a href="#axis-${field}">${both(esc(en), esc(zh))}</a>`).join(' ')}
  </nav>
  ${comparisonAxes.map(([field, en, zh]) => `<section aria-labelledby="axis-${field}">
    ${heading(2, esc(en), esc(zh), `axis-${field}`)}
    <div class="axis">${pair(axisList(field, 'en'), axisList(field, 'zh'), 'cols')}</div>
  </section>`).join('')}
  ${heading(2, 'Small reversible trial', '小范围可回退试用')}
  ${pair(list(comparison.checklist.en), list(comparison.checklist.zh, 'zh'), 'cols')}
  ${heading(2, 'Comparison boundaries', '比较边界')}
  ${pair(list(comparison.boundaries.en), list(comparison.boundaries.zh, 'zh'), 'cols')}
  <section class="sources" aria-labelledby="versioned-sources">
  ${heading(2, 'Versioned first-party sources', '版本化第一方来源', 'versioned-sources')}
  ${comparison.options.map((option) => {
    const project = optionProject(option)
    return `<section aria-labelledby="sources-${project.slug}">
      <h3 id="sources-${project.slug}"><a href="/${project.slug}">${esc(project.name.en)}</a> <span class="l-zh" lang="zh-CN">${esc(project.name.zh)}</span></h3>
      ${pair(list(option.sources.en), list(option.sources.zh, 'zh'), 'cols')}
    </section>`
  }).join('')}
  </section>
  </div>
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
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${both(esc(org.tagline.en), esc(org.tagline.zh))}</p>
      <h1>Six open-source projects, one story stack</h1>
      ${pair(`<p class="lede">${esc(org.canonical.en)}</p>`, `<p class="lede" lang="zh-CN">${esc(org.canonical.zh)}</p>`)}
      ${proofRow([
        proof(`${starGlyph}${num(org.proof.stars_total)} GitHub stars`),
        proof(both(`${esc(org.proof.license)} license`, `${esc(org.proof.license)} 许可`)),
        proof(both(`${org.proof.harnesses.length} agent hosts`, `${org.proof.harnesses.length} 个 Agent 宿主`)),
      ], org.proof.as_of)}
      <p class="actions">${comparisons.map((comparison) => `<a class="btn ghost" href="/compare/${comparison.slug}">${both(esc(comparison.title.en), esc(comparison.title.zh))}${arrowGlyph}</a>`).join('')}</p>
    </div>
  </header>

  <section class="band" aria-labelledby="catalog-h">
    <div class="wrap">
    ${heading(2, 'Start with what you want to make', '从目标开始', 'catalog-h')}
    <div class="project-grid">${projects.map((p) => {
      const [taskEn, taskZh] = taskChoices.find(([, , slug]) => slug === p.slug)
      return projectCard(p, both(esc(taskEn), esc(taskZh)))
    }).join('')}
    </div>
    </div>
  </section>

  <section class="band band-cream" aria-labelledby="model-h">
    <div class="wrap">
    ${heading(2, 'How the pieces fit together', '项目如何协作', 'model-h')}
    <div class="model">${pair(steps(org.model.en), steps(org.model.zh, 'zh'), 'cols')}</div>
    </div>
  </section>

  <section class="band" aria-labelledby="hosts-h">
    <div class="wrap">
    ${heading(2, 'Runs inside the agents you already use', '在你已经使用的 Agent 里运行', 'hosts-h')}
    <p class="terms hosts">${org.proof.harnesses.map((h) => `<span>${esc(h)}</span>`).join(' ')}</p>
    <p>${org.proof.harnesses.map(esc).join(' · ')}. ${both(`All ${esc(org.proof.license)}-licensed. ${num(org.proof.stars_total)} GitHub stars across the organization as of ${esc(org.proof.as_of)}.`, `全部 ${esc(org.proof.license)} 许可。截至 ${esc(org.proof.as_of)}，组织合计 ${num(org.proof.stars_total)} 个 GitHub star。`)}</p>
    </div>
  </section>
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
  <header class="page-hero">
    <div class="wrap">
      <p class="crumbs"><a href="/">ZenStory AI</a> <span aria-hidden="true">/</span> <a href="/glossary">${both('Glossary', '术语表')}</a></p>
      <p class="eyebrow">${both('Glossary', '术语')}</p>
      <h1 lang="zh-CN">${esc(g.term)}</h1>
      <p class="lede">${esc(g.bridge)}</p>
    </div>
  </header>
  <div class="wrap page-body">
  ${pair(`<h2>Definition</h2>
  <p>${rich(g.definition.en)}</p>`, `<h2 lang="zh-CN">定义</h2>
  <p lang="zh-CN">${rich(g.definition.zh)}</p>`, 'definition')}

  ${heading(2, 'In practice', '在工具里')}
  ${pair(`<p>${rich(g.in_practice.en)}</p>`, `<p lang="zh-CN">${rich(g.in_practice.zh)}</p>`)}
  ${owner ? `<p class="facts">${both(`Implemented in <a href="/${owner.slug}">${esc(owner.name.en)}</a> · <a href="${owner.github}">source</a>`, `实现于 <a href="/${owner.slug}">${esc(owner.name.zh)}</a> · <a href="${owner.github}">源码</a>`)}</p>` : ''}
  ${g.related?.length ? `<p class="terms related"><span class="terms-label">${both('Related:', '相关术语：')}</span> ${g.related.map((r) => { const x = glossary.find((y) => y.slug === r); return x ? `<a href="/glossary/${x.slug}" lang="zh-CN">${esc(x.term)}</a>` : '' }).join(' ')}</p>` : ''}
  </div>
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
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${both('Glossary', '术语表')}</p>
      <h1>Terms of the story pipeline</h1>
      ${pair(`<p class="lede">${esc(description)}</p>`, `<p class="lede" lang="zh-CN">网文与短剧创作中的行话，附英文对照，以及 ZenStory AI 工具把它们落实为具体流程步骤的方式。</p>`)}
    </div>
  </header>
  <div class="wrap page-body wide">
  <dl class="glossary">${glossary.map((g) => {
    const owner = projects.find((p) => p.slug === g.owner)
    return `
    <div class="entry">
    <dt><a href="/glossary/${g.slug}" lang="zh-CN">${esc(g.term)}</a> <span class="bridge">${esc(g.bridge)}</span></dt>
    <dd>${both(esc(g.definition.en.split('. ')[0]) + '.', esc(g.definition.zh.split(/(?<=。)/)[0]))}${owner ? `<span class="owner">${esc(owner.name.en)}</span>` : ''}</dd>
    </div>`
  }).join('')}
  </dl>
  </div>
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
