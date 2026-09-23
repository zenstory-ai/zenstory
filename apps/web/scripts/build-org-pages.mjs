#!/usr/bin/env node
/**
 * Generate the static ZenStory AI organization pages into the Vite output dir.
 *
 * Runs after `vite build`. Emits complete, crawler-readable HTML (title, meta,
 * Open Graph, hreflang, JSON-LD) for:
 *   /org-home            – the apex organization homepage (internal output)
 *   /projects            – the six-project overview
 *   /<project-slug>      – one page per project
 *   /<project>/<guide>   – one page per practical guide
 *   /guides              – the index of every practical guide
 *   /compare/<slug>      – first-party comparisons
 *   /glossary            – the terminology index
 *   /glossary/<term>     – one page per term
 *
 * Every route is written twice: English at the root and Chinese under `/zh`
 * (`/zh` itself is the Chinese homepage). Each page is one language; the two
 * name each other with hreflang links (see site-shell.mjs).
 *
 * Vercel matches these files on the filesystem before the SPA rewrite, so the
 * React app is untouched. Content lives in ../content/*.json.
 *
 * Usage: node scripts/build-org-pages.mjs [outDir]   (default: ../dist)
 */
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'
import { SITE, APP, LANGS, LOCALE, webRoot, org, projects, esc, orgNode, arrowGlyph, extGlyph, localized, alternatesOf, page as shellPage, t as tt } from './site-shell.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(process.argv[2] ?? join(webRoot, 'dist'))

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

/** English routes; every one also exists under /zh. */
const orgRoutes = new Set(['/', '/projects', ...projects.map((p) => `/${p.slug}`), ...guideRoutes, '/guides', ...comparisonRoutes, '/glossary', ...glossary.map((g) => `/glossary/${g.slug}`)])

// ---------- language ----------
// The generator runs once per language. `LANG` is the language of the page
// being written; every helper below reads it.

let LANG = 'en'
/** Pick the rendering for the current language. */
const t = (en, zh) => tt(LANG, en, zh)
/** `{en, zh}` field of the content JSON in the current language. */
const pick = (field) => field[LANG]
/** Path of an English route on the current language's site. */
const L = (route) => localized(LANG, route)
/** Absolute URL of an English route on the current language's site. */
const U = (route) => `${SITE}${L(route)}`

// ---------- helpers ----------

/** Escape, then turn `code` spans into <code> and [text](url) into links. */
const rich = (s) => esc(s)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2">$1</a>')

const breadcrumb = (items) => ({
  '@type': 'BreadcrumbList',
  itemListElement: items.map(([name, url], i) => ({ '@type': 'ListItem', position: i + 1, name, item: url })),
})

/**
 * Body markup for the current language. On Chinese pages, internal links to
 * organization routes — relative or written as absolute https://zenstory.ai
 * URLs in the content JSON — are re-pointed at the Chinese site (links to
 * single-URL pages such as /docs, legal pages and llms.txt, and external links stay), and
 * per-element zh-CN markers are dropped because the whole document is zh-CN.
 */
const localizeBody = (html) => (LANG === 'zh'
  ? html
    .replace(/href="(?:https:\/\/zenstory\.ai)?(\/[^"#?]*)([#?][^"]*)?"/g, (m, path, rest) => (orgRoutes.has(path) ? `href="${L(path)}${rest ?? ''}"` : m))
    .replace(/ lang="zh-CN"/g, '')
  : html)

/** Write the current language's rendering of an English route. */
const write = (route, html) => {
  const dir = join(outDir, LANG === 'en' && route === '/' ? '/org-home' : L(route))
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'index.html'), html)
}

const num = (n) => Number(n).toLocaleString('en-US')

/**
 * Meta description from a longer answer: markdown stripped, cut at the last
 * sentence end that fits (English ≈160 characters, Chinese ≈90), otherwise at
 * a word boundary with an ellipsis. Search engines truncate around there; the
 * full text stays in the page body and in JSON-LD.
 */
const summary = (text) => {
  const plain = String(text).replace(/\[([^\]]+)\]\((?:https?:\/\/[^)]+)\)/g, '$1').replace(/`/g, '').replace(/\s+/g, ' ').trim()
  const max = LANG === 'zh' ? 90 : 160
  if (plain.length <= max) return plain
  const head = plain.slice(0, max)
  const stop = Math.max(...(LANG === 'zh' ? ['。', '！', '？', '；'] : ['. ', '! ', '? ']).map((mark) => head.lastIndexOf(mark)))
  if (stop >= max / 2) return head.slice(0, stop + 1).trim()
  const space = LANG === 'zh' ? max - 1 : head.lastIndexOf(' ')
  return `${head.slice(0, space > max / 2 ? space : max - 1).trim()}…`
}

// ---------- language-specific renderings ----------
// `pair(enHtml, zhHtml, cls)` keeps its bilingual call sites: it renders the
// current language only, inside the `.pair` block the stylesheet lays out.

const pair = (enHtml, zhHtml, cls = '') => `<div class="pair${cls ? ` ${cls}` : ''}"><div class="l-${LANG}"${LANG === 'zh' ? ' lang="zh-CN"' : ''}>${t(enHtml, zhHtml)}</div></div>`
const both = t
/** Heading; `id` first so tests can match `<h2 id="…">`. */
const heading = (level, enText, zhText, id) => `<h${level}${id ? ` id="${id}"` : ''}>${t(enText, zhText)}</h${level}>`

const list = (items) => `<ul>${items.map((i) => `<li>${rich(i)}</li>`).join('')}</ul>`
const steps = (items) => `<ol class="steps">${items.map(([k, v]) => `<li><b>${rich(k)}</b> ${rich(v)}</li>`).join('')}</ol>`
const comparisonLinks = (project) => comparisons.filter((comparison) => comparison.options.some((option) => option.project === project))
const guidesOf = (slug) => guides.filter((g) => g.owner === slug)
/** "Oh Story（网文写作 skill 包）" → the parenthetical becomes a subordinate line (same characters). */
const zhName = (p) => { const m = p.name.zh.match(/^(.*?)(（.*）)$/); return m ? `${esc(m[1])}<span class="paren">${esc(m[2])}</span>` : esc(p.name.zh) }
/** Project name as a heading: English name, or the Chinese name with its parenthetical subordinate. */
const projectName = (p) => t(esc(p.name.en), zhName(p))

// ---------- small UI glyphs (inline, monochrome; the star keeps the brand cyan) ----------

const starGlyph = '<svg class="star" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M8 1.2c.62 2.6 1.66 3.64 4.26 4.26-2.6.62-3.64 1.66-4.26 4.26-.62-2.6-1.66-3.64-4.26-4.26 2.6-.62 3.64-1.66 4.26-4.26Z" fill="#22D3EE"/></svg>'
const proof = (html, cls = '') => `<span class="proof${cls ? ` ${cls}` : ''}">${html}</span>`
/** Chip row. The date the star counts were read is stated once per page, in the closing facts line, not next to every number. */
const proofRow = (chips) => `<p class="proof-row">${chips.join('')}</p>`

/** Navigational list row: title block grows, arrow is its own flex item so it never wraps alone. */
const listLink = (href, enText, zhText) => `<a href="${href}"><span class="guide-title">${t(enText, zhText)}</span>${arrowGlyph}</a>`
const guideLink = (g) => listLink(`/${g.owner}/${g.slug}`, esc(g.title.en), esc(g.title.zh))
const guideList = (items) => `<ul class="guide-list">${items.map((g) => `<li>${guideLink(g)}</li>`).join('')}</ul>`

/** In-page jump links (chips on phones, a side rail on wide project pages). */
const jumpNav = (items, cls) => `<nav class="${cls}" aria-label="${t('On this page', '本页导航')}"><p class="jump-label"><b>${t('On this page', '本页导航')}</b></p><ul>${items.map(([id, enText, zhText]) => `<li><a href="#${id}">${t(enText, zhText)}</a></li>`).join('')}</ul></nav>`

/** Copy affordance: hidden until the inline classic script finds a clipboard; the label is a live region so "Copied" is announced. */
const copyButton = (text) => `<button type="button" class="copy" data-copy="${esc(text)}" hidden><span class="copy-label" aria-live="polite"><span class="copy-idle">${t('Copy', '复制')}</span><span class="copy-done" hidden>${t('Copied', '已复制')}</span></span></button>`
/** A shell command whose whitespace-separated tokens never break internally (each token is nowrap). */
const cmd = (s) => esc(s).split(' ').map((tok) => `<span class="tok">${tok}</span>`).join(' ')
const installBlock = (p, { label = true } = {}) => p.install ? `<div class="install-block">${label ? `<span class="install-label">${t(`Install ${esc(p.name.en)}`, `安装 ${esc(p.name.en)}`)}</span>` : ''}<div class="install-row"><code class="install">${cmd(p.install)}</code>${copyButton(p.install)}</div></div>` : ''

// ---------- layout ----------

const navSection = (route) => {
  if (route === '/projects' || projects.some((p) => `/${p.slug}` === route)) return '/projects'
  if (route === '/guides' || guideRoutes.has(route)) return '/guides'
  if (route === '/glossary' || route.startsWith('/glossary/')) return '/glossary'
  return null
}

/** A complete document for the English route `route` in the current language. */
const page = ({ route, title, description, ogType = 'article', ld, body }) =>
  shellPage({ lang: LANG, route: L(route), alternates: alternatesOf(route), section: navSection(route), title, description, ogType, ld, body: localizeBody(body) })

const roster = (current) => `
<section class="roster band-cream" aria-labelledby="roster-h">
  <div class="wrap">
  ${heading(2, 'Part of ZenStory AI', 'ZenStory AI 项目', 'roster-h')}
  ${pair(`<p>${esc(org.intro.en)}</p>`, `<p>${esc(org.intro.zh)}</p>`)}
  <table>
    <thead><tr><th>${t('Project', '项目')}</th><th>${t('Format', '形态')}</th><th>${t('What it does', '用途')}</th></tr></thead>
    <tbody>${projects.map((p) => `
      <tr${p.slug === current ? ' class="here"' : ''}>
        <td><a href="/${p.slug}"${p.slug === current ? ' aria-current="page"' : ''}>${esc(p.name.en)}</a></td>
        <td>${esc(pick(p.format))}</td>
        <td>${esc(pick(p.tagline))}</td>
      </tr>`).join('')}
    </tbody>
  </table>
  </div>
</section>`

// ---------- shared blocks ----------

const taskChoices = [
  ['Write web fiction', '写网文', 'oh-story'],
  ['Produce a short drama or motion comic', '制作短剧或漫剧', 'drama-skills'],
  ['Adapt a novel into a playable game', '把小说改编成可玩的游戏', 'novel-to-game'],
  ['Turn footage into a narrated recap', '把视频做成解说成片', 'video-recap'],
  ['Use the story stack in DeepSeek Harness', '在 DeepSeek Harness 中使用故事工具链', 'dsh'],
  ['Write in a hosted browser workspace', '在浏览器工作台中写作', 'workbench'],
]

/** Proof chips for one project: stars and skills; the license is stated once per page (footer and closing facts line). */
const projectChips = (p) => [
  p.stars ? proof(`${starGlyph}${num(p.stars)} GitHub ${t('stars', 'star')}`) : '',
  p.skills ? proof(t(`${p.skills} skills`, `${p.skills} 个技能`)) : '',
].filter(Boolean)

/** One card component shared by the home page and /projects: task, name, one sentence, two facts, two links. The install command lives on the project page. */
const projectCard = (p, eyebrow) => `
      <article class="project-card">
        <p class="eyebrow">${eyebrow}</p>
        <h3><a href="/${p.slug}">${esc(p.name.en)}</a></h3>
        ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p>${esc(p.tagline.zh)}</p>`, 'tagline')}
        ${proofRow([proof(esc(pick(p.format)), 'format'), ...projectChips(p)])}
        <p class="card-actions"><a href="/${p.slug}">${t(p.install ? 'Install and details' : 'Project details', p.install ? '安装与详情' : '项目详情')}${arrowGlyph}</a><a href="${p.github}">${t('Source on GitHub', 'GitHub 源码')}${extGlyph}</a>${p.slug === 'workbench' ? `<a href="${APP}">${t('Open app', '打开工作台')}${extGlyph}</a>` : ''}</p>
      </article>`

/** Hero diagram: idea → novel → short drama / game / video recap, labelled with the tools. */
const pipelineSvg = () => {
  const node = (x, y, w, enText, zhText, tool) => `
    <rect x="${x}" y="${y}" width="${w}" height="56" rx="8" class="node"/>
    <text x="${x + w / 2}" y="${y + 33}" class="node-label">${t(enText, zhText)}</text>
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

const featuredGuideSlugs = ['agent-skills-for-writers', 'novel-opening', 'import-and-continue', 'revise-ai-prose', 'novel-to-short-drama', 'quick-start']

const homePage = () => {
  const route = '/'
  const title = t('ZenStory AI — Open-source AI tools for writing and adapting stories', 'ZenStory AI — 开源 AI 写小说、做短剧、改游戏与视频解说工具')
  const description = t(
    'Open-source AI tools for writers: novel-writing skills for Claude Code and Codex, short-drama storyboards, novel-to-game adaptation and video recaps.',
    '六个开源项目：用 Claude Code、Codex 写网文的 Oh Story，AI 短剧剧本与分镜，小说改游戏，视频解说，以及在线小说写作工作台。全部 MIT 许可。',
  )
  const ld = [
    orgNode,
    {
      '@type': 'WebSite', '@id': `${SITE}/#website`, name: org.name, url: SITE,
      description, publisher: { '@id': `${SITE}/#org` }, inLanguage: LOCALE[LANG],
    },
  ]
  const flagship = projects[0]
  const featured = featuredGuideSlugs.map((slug) => guides.find((g) => g.slug === slug)).filter(Boolean)
  const hosts = org.proof.harnesses
  const body = `
<article class="home">
  <div class="hero">
    <div class="wrap hero-grid">
      <div class="hero-copy">
        <p class="eyebrow">${esc(pick(org.tagline))}</p>
        <h1>ZenStory AI <span class="headline">${t('open-source AI tools to write novels and adapt them into drama, games and video', '用 AI 写小说，再改成短剧、游戏和解说视频')}</span></h1>
        ${pair(`<p class="lede">${esc(org.intro.en)}</p>`, `<p class="lede">${esc(org.intro.zh)}</p>`)}
        <p class="actions home-actions">
          <a class="btn" href="/projects">${t('Explore the six projects', '浏览六个项目')}</a>
          <a class="btn ghost" href="${APP}">${t('Open the web workbench', '打开网页工作台')}</a>
        </p>
        ${installBlock(flagship)}
        <p class="hero-note">${t(`Then run <code>${esc(flagship.entry)}</code> in your writing-project folder. Works in ${hosts.slice(0, 3).map(esc).join(', ')}&nbsp;<a href="/projects#hosts-h">and ${hosts.length - 3} more hosts</a>.`, `然后在写作项目目录里运行 <code>${esc(flagship.entry)}</code>。支持 ${hosts.slice(0, 3).map(esc).join('、')}&nbsp;<a href="/projects#hosts-h">等 ${hosts.length} 个 Agent 宿主</a>。`)}</p>
        <p class="hero-aside">${t('Not sure which? ', '不确定选哪个？')}${comparisons.map((comparison) => `<a href="/compare/${comparison.slug}">${esc(pick(comparison.title))}</a>`).join('')}${arrowGlyph}</p>
      </div>
      <figure class="hero-art">${pipelineSvg()}<figcaption class="sr-only">${t('Story pipeline: idea → novel → short drama, game or video recap', '故事流程：灵感 → 小说 → 短剧、游戏或视频解说')}</figcaption></figure>
    </div>
    <div class="wrap">${jumpNav([['choose-h', 'Choose by task', '按任务选择'], ['guides-h', 'Guides', '创作与改编指南'], ['model-h', 'How the pieces fit', '项目如何协作']], 'jump-row')}</div>
  </div>

  <section class="band band-cream" aria-labelledby="choose-h">
    <div class="wrap">
    ${heading(2, 'Choose by task', '按任务选择', 'choose-h')}
    <p class="section-lede">${t('Six open-source projects. Pick the card that matches what you want to make.', '六个开源项目，按你想做的东西选一张卡片。')}</p>
    <div class="project-grid">${projects.map((p) => {
      const [taskEn, taskZh] = taskChoices.find(([, , slug]) => slug === p.slug)
      return projectCard(p, t(esc(taskEn), esc(taskZh)))
    }).join('')}
    </div>
    </div>
  </section>

  <section class="band" aria-labelledby="guides-h">
    <div class="wrap">
    ${heading(2, 'Writing and adaptation guides', '创作与改编入门', 'guides-h')}
    <p class="section-lede">${t('Each guide answers one working question with a worked example.', '每篇指南回答一个具体的创作问题，附完整示例。')}</p>
    <h3 class="sub-h">${t('Start here', '先看这些')}</h3>
    <div class="guide-cards">${featured.map((g) => {
      const owner = projects.find((p) => p.slug === g.owner)
      return `
      <a class="guide-card" href="/${g.owner}/${g.slug}">
        <span class="eyebrow">${esc(owner.name.en)}</span>
        <span class="guide-card-title">${esc(pick(g.title))}</span>
      </a>`
    }).join('')}
    </div>
    <h3 class="sub-h">${t(`All ${guides.length} guides, by project`, `全部 ${guides.length} 篇指南，按项目分组`)}</h3>
    <div class="guide-groups">${projects.filter((p) => guidesOf(p.slug).length).map((p) => `
      <div class="guide-group${guidesOf(p.slug).length > 6 ? ' wide' : ''}">
        <h4><a href="/${p.slug}">${esc(p.name.en)}</a> <span class="count">${guidesOf(p.slug).length}</span></h4>
        ${guideList(guidesOf(p.slug))}
      </div>`).join('')}
    </div>
    <p class="more"><a href="/guides">${t('All guides, grouped by project', '按项目查看全部指南')}${arrowGlyph}</a></p>
    </div>
  </section>

  <section class="band band-cream" aria-labelledby="model-h">
    <div class="wrap">
    ${heading(2, 'How the pieces fit together', '项目如何协作', 'model-h')}
    <div class="model">${pair(steps(org.model.en), steps(org.model.zh), 'cols')}</div>
    <p class="facts">${t(`${num(org.proof.stars_total)} GitHub stars across the organization as of ${esc(org.proof.as_of)}. All repositories listed here are ${esc(org.proof.license)}-licensed.`, `截至 ${esc(org.proof.as_of)}，组织合计 ${num(org.proof.stars_total)} 个 GitHub star。这里列出的仓库全部采用 ${esc(org.proof.license)} 许可。`)}</p>
    </div>
  </section>

  <div class="wrap">
    <p class="migration-note">${t('The writing workbench has moved to <a href="' + APP + '">app.zenstory.ai</a>; sign in there with your existing account.', '写作工作台已迁至 <a href="' + APP + '">app.zenstory.ai</a>，用原来的账号在新域名登录即可。')}</p>
  </div>
</article>`
  write(route, page({ route, title, description, ogType: 'website', ld, body }))
}

// ---------- project pages ----------

const needBlock = (p) => {
  const isPack = Boolean(p.install)
  const host = p.slug === 'workbench'
    ? t(`A browser. The workbench runs at <a href="${APP}">app.zenstory.ai</a>.`, `一个浏览器。工作台运行在 <a href="${APP}">app.zenstory.ai</a>。`)
    : p.slug === 'dsh'
      ? t('DeepSeek Harness (DSH) as the host.', '以 DeepSeek Harness（DSH）为宿主。')
      : t(`An agent host: ${org.proof.harnesses.map(esc).join(', ')}.`, `一个 Agent 宿主：${org.proof.harnesses.map(esc).join('、')}。`)
  return `
  <section class="need" aria-labelledby="need-h">
    ${heading(2, 'What you need', '你需要什么', 'need-h')}
    <ul class="need-list">
      <li><b>${t('License', '许可')}</b><span>${t(`${esc(org.proof.license)}, open source.`, `${esc(org.proof.license)}，开源。`)}</span></li>
      <li><b>${t(p.slug === 'workbench' ? 'Where it runs' : 'Host', p.slug === 'workbench' ? '在哪里运行' : '宿主')}</b><span>${host}</span></li>
      ${isPack ? `<li><b>${t('Install', '安装')}</b><span>${t('One command:', '一条命令：')} <code class="cmd">${cmd(p.install)}</code></span></li>` : ''}
      ${p.slug === 'workbench' ? `<li><b>${t('Docs', '文档')}</b><span>${t('The <a href="/docs">workbench documentation</a>: getting started, user guide, reference and troubleshooting.', '<a href="/docs">工作台文档</a>：快速入门、用户指南、参考资料与故障排除。')}</span></li>` : ''}
      ${p.slug !== 'workbench' ? `<li><b>${t('Or in the browser', '或在浏览器里')}</b><span>${t(`The separate <a href="/workbench">ZenStory Workbench</a> at <a href="${APP}">app.zenstory.ai</a>.`, `独立的 <a href="/workbench">ZenStory 工作台</a>：<a href="${APP}">app.zenstory.ai</a>。`)}</span></li>` : ''}
    </ul>
  </section>`
}

const startBlock = (p, own) => {
  if (!p.install || !p.entry) return ''
  return `
  <section class="start" aria-labelledby="start-h">
    ${heading(2, 'Start in 3 steps', '三步开始', 'start-h')}
    <ol class="start-list">
      <li><b>${t('Install', '安装')}</b><span><code class="cmd">${cmd(p.install)}</code></span></li>
      <li><b>${t('Entry command', '入口命令')}</b><span>${t(`Run <code>${esc(p.entry)}</code> in your agent host.`, `在 Agent 宿主中运行 <code>${esc(p.entry)}</code>。`)}</span></li>
      <li><b>${t('Follow a guide', '按指南操作')}</b><span>${own.length ? t(`Pick one of the ${own.length} guides below for your first task.`, `从下方 ${own.length} 篇指南中选一个，完成第一个任务。`) : t('Read the source README for the first task.', '按源码 README 完成第一个任务。')}</span></li>
    </ol>
  </section>`
}

const projectPage = (p) => {
  const route = `/${p.slug}`
  const title = pick(p.seo.title)
  const ld = [
    orgNode,
    {
      '@type': p.slug === 'workbench' ? 'SoftwareApplication' : 'SoftwareSourceCode',
      '@id': `${U(route)}#software`,
      name: p.name.en,
      alternateName: p.repo,
      description: pick(p.definition),
      url: U(route),
      codeRepository: p.github,
      license: 'https://opensource.org/licenses/MIT',
      publisher: { '@id': `${SITE}/#org` },
      inLanguage: LOCALE[LANG],
      ...(p.slug === 'workbench' ? { applicationCategory: 'BusinessApplication', operatingSystem: 'Web' } : { programmingLanguage: 'Markdown' }),
      keywords: (p.vocabulary ?? []).join(', '),
    },
    breadcrumb([['ZenStory AI', U('/')], [t('Projects', '项目'), U('/projects')], [p.name.en, U(route)]]),
  ]
  const own = guidesOf(p.slug)
  const body = `
<article class="project">
  <header class="page-hero">
    <div class="wrap">
      <p class="crumbs"><a href="/">ZenStory AI</a> <span aria-hidden="true">/</span> <a href="/projects">${t('Projects', '项目')}</a></p>
      <p class="eyebrow">${esc(pick(p.format))}</p>
      <h1>${projectName(p)}</h1>
      ${pair(`<p class="lede">${esc(p.tagline.en)}</p>`, `<p class="lede">${esc(p.tagline.zh)}</p>`)}
      ${proofRow(projectChips(p))}
      <p class="actions">
        <a class="btn" href="${p.github}">${t('Source on GitHub', '在 GitHub 查看源码')}${extGlyph}</a>
        ${p.readme_en && p.readme_en !== p.github ? `<a class="btn ghost" href="${p.readme_en}">${t('English README', '英文 README')}${extGlyph}</a>` : ''}
        ${p.slug === 'workbench' ? `<a class="btn ghost" href="${p.entry}">${t('Open the workbench', '打开工作台')}${extGlyph}</a><a class="btn ghost" href="/docs">${t('Workbench docs', '工作台文档')}${arrowGlyph}</a>` : ''}
      </p>
      ${installBlock(p)}
    </div>
  </header>

  <div class="wrap page-grid">
  ${jumpNav([
    ['need-h', 'What you need', '你需要什么'],
    ...(p.install && p.entry ? [['start-h', 'Start in 3 steps', '三步开始']] : []),
    ...(own.length ? [['guides-h', 'Practical guides', '实用指南']] : []),
    ['method-h', 'How it works', '流程'],
    ['sources-h', 'Sources', '来源'],
  ], 'page-rail')}
  <div class="page-body">
  ${pair(`<h2>What it is</h2>
  <p>${rich(p.definition.en)}</p>`, `<h2>它是什么</h2>
  <p>${rich(p.definition.zh)}</p>`, 'definition')}

  ${needBlock(p)}
  ${startBlock(p, own)}

  ${own.length ? `<section aria-labelledby="guides-h">${heading(2, 'Practical guides', '实用指南', 'guides-h')}
  ${guideList(own)}</section>` : ''}

  <section aria-labelledby="method-h">
  ${heading(2, 'How it works', '流程', 'method-h')}
  <div class="qa">${pair(steps(p.method.en), steps(p.method.zh), 'cols')}</div>
  </section>

  ${heading(2, 'What makes it different', '有什么不同')}
  ${pair(list(p.distinctive.en), list(p.distinctive.zh), 'cols')}

  ${heading(2, 'Who it is for', '适合谁')}
  ${pair(list(p.audience.en), list(p.audience.zh), 'cols')}

  ${comparisonLinks(p.slug).length ? `${heading(2, 'Compare writing workflows', '比较写作环境')}
  <ul class="guide-list">${comparisonLinks(p.slug).map((comparison) => `<li>${listLink(`/compare/${comparison.slug}`, esc(comparison.title.en), esc(comparison.title.zh))}</li>`).join('')}</ul>` : ''}

  ${p.vocabulary?.length ? `${heading(2, 'Terms it uses', '相关术语')}<p class="terms">${p.vocabulary.map((term) => {
    const g = glossary.find((x) => x.term === term)
    return g ? `<a href="/glossary/${g.slug}" lang="zh-CN">${esc(term)}</a>` : `<span lang="zh-CN">${esc(term)}</span>`
  }).join(' ')}</p>` : ''}

  <section class="sources" aria-labelledby="sources-h">
  ${heading(2, 'Sources', '来源', 'sources-h')}
  <p class="facts">${t(`This page describes the source as read on ${esc(p.sources.checked_on)}${p.stars ? `; star count as of ${esc(org.proof.as_of)}` : ''}. Links point at that version.`, `本页依据 ${esc(p.sources.checked_on)} 读取的源码${p.stars ? `，star 数截至 ${esc(org.proof.as_of)}` : ''}；链接指向该版本。`)}</p>
  ${pair(list(p.sources.en), list(p.sources.zh), 'cols')}
  </section>
  </div>
  </div>
</article>
${roster(p.slug)}`
  write(route, page({ route, title, description: pick(p.seo.description), ogType: 'website', ld, body }))
}

// ---------- task guides ----------

const guideExample = (text) => `
<section class="guide-example" aria-labelledby="example-${LANG}">
  <h3 id="example-${LANG}">${t('Example', '示例')}</h3>
  ${text.split(/\n{2,}/).map(paragraph => `<p>${esc(paragraph)}</p>`).join('\n  ')}
</section>`

const guidePage = (g) => {
  const owner = projects.find((p) => p.slug === g.owner)
  const route = `/${g.owner}/${g.slug}`
  const ld = [
    orgNode,
    { '@type': 'TechArticle', '@id': `${U(route)}#article`, url: U(route),
      headline: pick(g.title), description: pick(g.answer), inLanguage: LOCALE[LANG],
      dateModified: g.checked_on, publisher: { '@id': `${SITE}/#org` } },
    breadcrumb([['ZenStory AI', U('/')], [owner.name.en, U(`/${owner.slug}`)], [pick(g.title), U(route)]]),
  ]
  const body = `
<article class="guide">
  <header class="guide-head">
  <p class="eyebrow">${t('Practical guide', '实用指南')}</p>
  <h1>${esc(pick(g.title))}</h1>
  <p class="facts">${t(`${esc(owner.name.en)} · Updated ${esc(g.checked_on)}`, `${esc(owner.name.en)} · 更新于 ${esc(g.checked_on)}`)}</p>
  ${pair(`<p>${rich(g.answer.en)}</p>`, `<p>${rich(g.answer.zh)}</p>`, 'answer')}
  <p class="actions guide-actions"><a class="crumb" href="/${owner.slug}">${t('Part of', '所属项目')} <b>${esc(owner.name.en)}</b>${arrowGlyph}</a><a class="crumb" href="${owner.github}">${t('Source on GitHub', '在 GitHub 查看源码')}${extGlyph}</a></p>
  </header>
  <nav class="guide-contents" aria-label="${t('On this page', '本页导航')}">
    <p><b>${t('On this page', '本页导航')}</b></p>
    <ul>
      <li><a href="#before-you-start">${t('Before you start', '开始之前')}</a></li>
      <li><a href="#steps">${t('Steps', '操作步骤')}</a></li>
      <li><a href="#example-${LANG}">${t('Example', '示例')}</a></li>
      <li><a href="#expected-files">${t('Expected files', '预期文件')}</a></li>
      <li><a href="#verify-result">${t('Result boundaries', '结果边界')}</a></li>
      <li><a href="#sources">${t('Sources', '来源')}</a></li>
    </ul>
  </nav>
  <div class="guide-body">
  ${heading(2, 'Before you start', '开始之前', 'before-you-start')}
  ${pair(list(g.prerequisites.en), list(g.prerequisites.zh), 'cols')}
  ${heading(2, 'Steps', '操作步骤', 'steps')}
  ${pair(steps(g.steps.en), steps(g.steps.zh), 'cols')}
  ${heading(2, 'Example', '示例', 'examples')}
  ${pair(guideExample(g.example.en), guideExample(g.example.zh), 'cols')}
  ${heading(2, 'Expected files', '预期文件', 'expected-files')}
  ${pair(list(g.outputs.en), list(g.outputs.zh), 'cols')}
  ${heading(2, 'Verify the result', '验证结果与边界', 'verify-result')}
  ${pair(list(g.verification.en), list(g.verification.zh), 'cols')}
  ${heading(2, 'Sources and version notes', '来源与版本说明', 'sources')}
  ${pair(list(g.sources.en), list(g.sources.zh), 'cols')}
  <p class="actions guide-end"><a class="btn ghost" href="/${owner.slug}">${t(`More about ${esc(owner.name.en)}`, `了解 ${esc(owner.name.en)}`)}${arrowGlyph}</a><a class="btn ghost" href="/guides">${t('All guides', '全部指南')}${arrowGlyph}</a></p>
  </div>
</article>`
  write(route, page({ route, title: `${pick(g.title)} | ZenStory AI`, description: summary(pick(g.answer)), ld, body }))
}

// ---------- guides index ----------

const guidesIndex = () => {
  const route = '/guides'
  const title = t('Guides — writing, adapting and producing stories with AI | ZenStory AI', '创作指南 — 用 AI 写小说、改短剧、做游戏与视频解说 | ZenStory AI')
  const description = t(`${guides.length} practical guides on writing novels with AI, adapting them into short drama and games, and producing video recaps. Grouped by project.`, `${guides.length} 篇实用指南：AI 写小说、续写与改稿，小说改短剧与游戏，视频解说制作。每篇附具体示例，按项目分组。`)
  const ld = [
    orgNode,
    {
      '@type': 'ItemList', name: t('ZenStory AI practical guides', 'ZenStory AI 实用指南'), url: U(route),
      itemListElement: guides.map((g, i) => ({ '@type': 'ListItem', position: i + 1, name: pick(g.title), url: U(`/${g.owner}/${g.slug}`) })),
    },
    breadcrumb([['ZenStory AI', U('/')], [t('Guides', '指南'), U(route)]]),
  ]
  const groups = projects.filter((p) => guidesOf(p.slug).length)
  const body = `
<article class="guides-index">
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${t('Practical guides', '实用指南')}</p>
      <h1>${t('Practical guides', '实用指南')}</h1>
      ${pair(`<p class="lede">Each guide answers one working question with a worked example, and links to the project that does the work.</p>`, `<p class="lede">每篇指南回答一个具体的创作问题，附完整示例，并链接到负责这件事的项目。</p>`)}
      <nav class="terms jump" aria-label="${t('Jump to project', '跳到项目')}">${groups.map((p) => `<a href="#guides-${p.slug}">${esc(p.name.en)} <span class="count">${guidesOf(p.slug).length}</span></a>`).join(' ')}</nav>
    </div>
  </header>
  <div class="wrap page-body wide">
  ${groups.map((p) => `
  <section class="guide-section" aria-labelledby="guides-${p.slug}">
    <div class="guide-section-head">
    <h2 id="guides-${p.slug}">${projectName(p)}</h2>
    ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p>${esc(p.tagline.zh)}</p>`, 'tagline')}
    <p class="facts"><a href="/${p.slug}">${t(`About ${esc(p.name.en)}`, `关于 ${esc(p.name.en)}`)}${arrowGlyph}</a></p>
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
  const url = U(route)
  const optionProject = (option) => projects.find((project) => project.slug === option.project)
  const axisList = (field) => `<ul>${comparison.options.map((option) => {
    const project = optionProject(option)
    return `<li><b><a href="/${project.slug}">${esc(project.name.en)}</a></b> ${rich(pick(option[field]))}</li>`
  }).join('')}</ul>`
  const ld = [
    orgNode,
    { '@type': 'TechArticle', '@id': `${url}#article`, url,
      headline: pick(comparison.title), description: pick(comparison.answer),
      inLanguage: LOCALE[LANG], dateModified: comparison.checked_on,
      publisher: { '@id': `${SITE}/#org` } },
    breadcrumb([['ZenStory AI', U('/')], [pick(comparison.title), url]]),
  ]
  const body = `
<article class="comparison">
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${t('First-party comparison', '自有项目选择指南')}</p>
      <h1>${esc(pick(comparison.title))}</h1>
      <p class="facts">${t(`Updated ${esc(comparison.checked_on)}`, `更新于 ${esc(comparison.checked_on)}`)}</p>
      ${pair(`<p>${rich(comparison.answer.en)}</p>`, `<p>${rich(comparison.answer.zh)}</p>`, 'answer')}
      <nav class="terms options" aria-label="${t('Compared projects', '比较的项目')}">${comparison.options.map((option) => { const project = optionProject(option); return `<a href="/${project.slug}">${esc(project.name.en)}</a>` }).join(' ')}</nav>
    </div>
  </header>
  <div class="wrap page-body wide">
  <aside class="migration-note" aria-label="${t('First-party disclosure', '自有项目披露')}">
    ${pair(`<p><strong>First-party disclosure.</strong> ${rich(comparison.disclosure.en)}</p>`, `<p><strong>自有项目披露。</strong> ${rich(comparison.disclosure.zh)}</p>`)}
  </aside>
  <nav class="terms axes" aria-label="${t('Comparison axes', '比较维度')}">
    ${comparisonAxes.map(([field, en, zh]) => `<a href="#axis-${field}">${t(esc(en), esc(zh))}</a>`).join(' ')}
  </nav>
  ${comparisonAxes.map(([field, en, zh]) => `<section aria-labelledby="axis-${field}">
    ${heading(2, esc(en), esc(zh), `axis-${field}`)}
    <div class="axis"><div class="pair cols"><div class="l-${LANG}">${axisList(field)}</div></div></div>
  </section>`).join('')}
  ${heading(2, 'Small reversible trial', '小范围可回退试用')}
  ${pair(list(comparison.checklist.en), list(comparison.checklist.zh), 'cols')}
  ${heading(2, 'Comparison boundaries', '比较边界')}
  ${pair(list(comparison.boundaries.en), list(comparison.boundaries.zh), 'cols')}
  <section class="sources" aria-labelledby="versioned-sources">
  ${heading(2, 'Versioned first-party sources', '版本化第一方来源', 'versioned-sources')}
  ${comparison.options.map((option) => {
    const project = optionProject(option)
    return `<section aria-labelledby="sources-${project.slug}">
      <h3 id="sources-${project.slug}"><a href="/${project.slug}">${projectName(project)}</a></h3>
      ${pair(list(option.sources.en), list(option.sources.zh), 'cols')}
    </section>`
  }).join('')}
  </section>
  </div>
</article>`
  write(route, page({ route, title: `${pick(comparison.title)} | ZenStory AI`, description: summary(pick(comparison.answer)), ld, body }))
}

// ---------- projects index ----------

const projectsIndex = () => {
  const route = '/projects'
  const title = t('Projects — six open-source AI story tools | ZenStory AI', '全部项目 — 六个开源 AI 故事创作工具 | ZenStory AI')
  const description = t(
    'All six ZenStory AI projects with their install commands: Oh Story, Drama Skills, Novel to Game, Video Recap Skills, Oh Story DSH and the ZenStory Workbench.',
    'ZenStory AI 全部六个项目及安装方式：Oh Story、Drama Skills、Novel to Game、Video Recap Skills、Oh Story DSH 与 ZenStory 工作台。按你想做的东西来选。',
  )
  const ld = [
    orgNode,
    {
      '@type': 'ItemList', name: t('ZenStory AI projects', 'ZenStory AI 项目'),
      itemListElement: projects.map((p, i) => ({ '@type': 'ListItem', position: i + 1, name: p.name.en, url: U(`/${p.slug}`) })),
    },
    breadcrumb([['ZenStory AI', U('/')], [t('Projects', '项目'), U(route)]]),
  ]
  const body = `
<article class="projects-index">
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${esc(pick(org.tagline))}</p>
      <h1>${t('Six open-source projects, one story stack', '六个开源项目，一条故事工具链')}</h1>
      ${pair(`<p class="lede">${esc(org.intro.en)}</p>`, `<p class="lede">${esc(org.intro.zh)}</p>`)}
      <p class="actions">${comparisons.map((comparison) => `<a class="btn ghost" href="/compare/${comparison.slug}">${esc(pick(comparison.title))}${arrowGlyph}</a>`).join('')}</p>
    </div>
  </header>

  <section class="band" aria-labelledby="catalog-h">
    <div class="wrap">
    ${heading(2, 'Start with what you want to make', '从目标开始', 'catalog-h')}
    <div class="project-grid">${projects.map((p) => {
      const [taskEn, taskZh] = taskChoices.find(([, , slug]) => slug === p.slug)
      return projectCard(p, t(esc(taskEn), esc(taskZh)))
    }).join('')}
    </div>
    </div>
  </section>

  <section class="band band-cream" aria-labelledby="model-h">
    <div class="wrap">
    ${heading(2, 'How the pieces fit together', '项目如何协作', 'model-h')}
    <div class="model">${pair(steps(org.model.en), steps(org.model.zh), 'cols')}</div>
    </div>
  </section>

  <section class="band" aria-labelledby="hosts-h">
    <div class="wrap">
    ${heading(2, 'Runs inside the agents you already use', '在你已经使用的 Agent 里运行', 'hosts-h')}
    <p class="terms hosts">${org.proof.harnesses.map((h) => `<span>${esc(h)}</span>`).join(' ')}</p>
    <p>${org.proof.harnesses.map(esc).join(' · ')}. ${t(`All ${esc(org.proof.license)}-licensed. ${num(org.proof.stars_total)} GitHub stars across the organization as of ${esc(org.proof.as_of)}.`, `全部 ${esc(org.proof.license)} 许可。截至 ${esc(org.proof.as_of)}，组织合计 ${num(org.proof.stars_total)} 个 GitHub star。`)}</p>
    </div>
  </section>
</article>`
  write(route, page({ route, title, description, ogType: 'website', ld, body }))
}

// ---------- glossary ----------

const termPage = (g) => {
  const route = `/glossary/${g.slug}`
  const owner = projects.find((p) => p.slug === g.owner)
  const bridge = g.bridge.split(/\s*[/(]/)[0].trim()
  const title = t(`${g.term} (${bridge}) — meaning in web fiction and short drama | ZenStory AI`, `${g.term}是什么意思 — ${bridge} | ZenStory AI 术语表`)
  const ld = [
    orgNode,
    {
      '@type': 'DefinedTerm', '@id': `${U(route)}#term`, name: g.term, alternateName: g.bridge,
      description: pick(g.definition), url: U(route), inLanguage: LOCALE[LANG],
      inDefinedTermSet: { '@type': 'DefinedTermSet', name: t('ZenStory AI glossary', 'ZenStory AI 术语表'), url: U('/glossary') },
    },
    breadcrumb([['ZenStory AI', U('/')], [t('Glossary', '术语表'), U('/glossary')], [g.term, U(route)]]),
  ]
  const body = `
<article class="term">
  <header class="page-hero">
    <div class="wrap">
      <p class="crumbs"><a href="/">ZenStory AI</a> <span aria-hidden="true">/</span> <a href="/glossary">${t('Glossary', '术语表')}</a></p>
      <p class="eyebrow">${t('Glossary', '术语')}</p>
      <h1 lang="zh-CN">${esc(g.term)}</h1>
      <p class="lede">${esc(g.bridge)}</p>
    </div>
  </header>
  <div class="wrap page-grid">
  <div class="page-body">
  ${pair(`<h2>Definition</h2>
  <p>${rich(g.definition.en)}</p>`, `<h2>定义</h2>
  <p>${rich(g.definition.zh)}</p>`, 'definition')}

  ${heading(2, 'In practice', '在工具里')}
  ${pair(`<p>${rich(g.in_practice.en)}</p>`, `<p>${rich(g.in_practice.zh)}</p>`)}
  ${owner ? `<p class="facts">${t(`Implemented in <a href="/${owner.slug}">${esc(owner.name.en)}</a> · <a href="${owner.github}">source</a>`, `实现于 <a href="/${owner.slug}">${esc(owner.name.zh)}</a> · <a href="${owner.github}">源码</a>`)}</p>` : ''}
  ${g.related?.length ? `<p class="terms related"><span class="terms-label">${t('Related terms', '相关术语')}</span> ${g.related.map((r) => { const x = glossary.find((y) => y.slug === r); return x ? `<a href="/glossary/${x.slug}" lang="zh-CN">${esc(x.term)}</a>` : '' }).join(' ')}</p>` : ''}
  </div>
  </div>
</article>
${roster(g.owner)}`
  write(route, page({ route, title, description: summary(pick(g.definition)), ld, body }))
}

const glossaryIndex = () => {
  const route = '/glossary'
  const title = t('ZenStory AI glossary — 扫榜, 拆文, 去AI味, 漫剧 and other terms of the story pipeline', 'ZenStory AI 术语表 — 扫榜、拆文、去AI味、漫剧等故事流程术语')
  const description = t('Definitions, with English bridges, of the Chinese web-fiction and short-drama craft terms that ZenStory AI tools implement as concrete pipeline steps.', '网文与短剧创作中的行话，附英文对照，以及 ZenStory AI 工具把它们落实为具体流程步骤的方式。')
  const ld = [
    orgNode,
    { '@type': 'DefinedTermSet', '@id': `${U(route)}#set`, name: t('ZenStory AI glossary', 'ZenStory AI 术语表'), url: U(route), inLanguage: LOCALE[LANG],
      hasDefinedTerm: glossary.map((g) => ({ '@type': 'DefinedTerm', name: g.term, alternateName: g.bridge, url: U(`/glossary/${g.slug}`) })) },
    breadcrumb([['ZenStory AI', U('/')], [t('Glossary', '术语表'), U(route)]]),
  ]
  const body = `
<article class="glossary-index">
  <header class="page-hero">
    <div class="wrap">
      <p class="eyebrow">${t('Glossary', '术语表')}</p>
      <h1>${t('Terms of the story pipeline', '故事流程里的术语')}</h1>
      <p class="lede">${esc(description)}</p>
    </div>
  </header>
  <div class="wrap page-body wide">
  <dl class="glossary">${glossary.map((g) => {
    const owner = projects.find((p) => p.slug === g.owner)
    return `
    <div class="entry">
    <dt><a href="/glossary/${g.slug}" lang="zh-CN">${esc(g.term)}</a> <span class="bridge">${esc(g.bridge)}</span></dt>
    <dd>${t(esc(g.definition.en.split('. ')[0]) + '.', esc(g.definition.zh.split(/(?<=。)/)[0]))}${owner ? `<span class="owner">${esc(owner.name.en)}</span>` : ''}</dd>
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
for (const lang of LANGS) {
  LANG = lang
  homePage()
  projectsIndex()
  projects.forEach(projectPage)
  guides.forEach(guidePage)
  guidesIndex()
  comparisons.forEach(comparisonPage)
  glossaryIndex()
  glossary.forEach(termPage)
}

const routes = [...orgRoutes].map((route) => (route === '/' ? '/org-home' : route))
console.log(`org pages: wrote ${routes.length} routes × ${LANGS.length} languages to ${outDir}\n  ${routes.join('  ')}`)
