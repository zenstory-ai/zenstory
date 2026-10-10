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
 *   /<project>/<article> – one page per craft article (content/articles.json):
 *                          free-form sections; Chinese always, English only
 *                          when the article lists it (Chinese-only articles
 *                          exist under /zh alone and carry no hreflang pair)
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
import { SITE, appHref, LANGS, LOCALE, webRoot, org, projects, esc, orgNode, arrowGlyph, extGlyph, localized, alternatesOf, page as shellPage, t as tt, HOME_FONTS } from './site-shell.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(process.argv[2] ?? join(webRoot, 'dist'))

const glossary = JSON.parse(readFileSync(join(webRoot, 'content/glossary.json'), 'utf8'))
const guides = JSON.parse(readFileSync(join(webRoot, 'content/guides.json'), 'utf8'))
const comparisons = JSON.parse(readFileSync(join(webRoot, 'content/comparisons.json'), 'utf8'))
const topics = JSON.parse(readFileSync(join(webRoot, 'content/guide-topics.json'), 'utf8'))
/** Reader-facing names of the skills behind guides, from each skill's own description (topic page group headings). */
const skillLabels = JSON.parse(readFileSync(join(webRoot, 'content/skill-labels.json'), 'utf8'))
const topicIds = new Set(topics.map((topic) => topic.slug))
assert.equal(topicIds.size, topics.length, 'Duplicate guide topic')
for (const topic of topics) {
  assert.match(topic.slug, /^[a-z0-9-]+$/, 'Invalid guide topic')
  for (const field of ['title', 'description']) for (const lang of LANGS) assert.ok(topic[field]?.[lang]?.trim(), 'Invalid guide topic label')
}
// Older content and isolated markup fixtures can omit a topic. Published content
// has explicit editorial assignments, checked by the catalog regression test.
const topicOf = (item) => item.topic ?? ({'oh-story':'plot-and-outline', 'drama-skills':'short-drama', 'novel-to-game':'interactive-games', 'video-recap':'video-recaps', dsh:'getting-started'})[item.owner]

const guideRoutes = new Set()
for (const guide of guides) {
  assert.ok(projects.filter((p) => p.slug === guide.owner).length === 1 && /^[a-z0-9-]+$/.test(guide.slug), 'Invalid guide identity')
  const route = `/${guide.owner}/${guide.slug}`
  assert.ok(!guideRoutes.has(route), 'Duplicate guide route')
  // Optional questions people search for, answered from the guide's own material.
  // Answers are one paragraph of inline markup (`code`, **bold**, links); a blank line is rejected.
  const faq = guide.faq ?? []
  assert.ok(Array.isArray(faq) && faq.every((item) => item && ['q', 'a'].every((k) => LANGS.every((l) => typeof item[k]?.[l] === 'string' && item[k][l].trim()))) && faq.every((item) => LANGS.every((l) => !/\n\s*\n/.test(item.a[l]))), `Invalid guide FAQ: ${route}`)
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

const articles = JSON.parse(readFileSync(join(webRoot, 'content/articles.json'), 'utf8'))
/** Craft articles published in both languages (English route; the Chinese one is under /zh). */
const articleRoutes = new Set()
/** Craft articles published in Chinese only (English route form; the page exists only under /zh). */
const zhOnlyRoutes = new Set()
const isDate = (value) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
/** Title and meta-description budgets per language (characters), matching what results pages show. */
const ARTICLE_LIMITS = { seo_title: { en: 60, zh: 30 }, description: { en: 160, zh: 90 } }
for (const article of articles) {
  assert.ok(projects.filter((p) => p.slug === article.owner).length === 1 && /^[a-z0-9-]+$/.test(article.slug ?? ''), 'Invalid article identity')
  const route = `/${article.owner}/${article.slug}`
  assert.ok(!guideRoutes.has(route) && !articleRoutes.has(route) && !zhOnlyRoutes.has(route), `Duplicate article route ${route}`)
  const langs = article.langs
  assert.ok(Array.isArray(langs) && langs.includes('zh') && langs.every((lang) => LANGS.includes(lang)) && new Set(langs).size === langs.length, `Invalid article languages: ${route}`)
  assert.ok(isDate(article.published_on) && isDate(article.updated_on) && article.updated_on >= article.published_on, `Invalid article dates: ${route}`)
  assert.ok(Array.isArray(article.sections) && article.sections.length >= 2, `Invalid article content: ${route} needs sections`)
  const ids = article.sections.map((section) => section.id)
  assert.ok(ids.every((id) => /^[a-z0-9-]+$/.test(id ?? '') && !['faq', 'use-the-skill', 'related', 'main'].includes(id)) && new Set(ids).size === ids.length, `Invalid article section ids: ${route}`)
  assert.ok(typeof article.skill?.name === 'string' && /^[a-z0-9-]+$/.test(article.skill.name), `Invalid article skill: ${route}`)
  for (const lang of langs) {
    for (const field of ['title', 'seo_title', 'description', 'answer']) assert.ok(article[field]?.[lang]?.trim(), `Invalid article content: ${route} ${field}.${lang}`)
    for (const [field, limit] of Object.entries(ARTICLE_LIMITS)) assert.ok([...article[field][lang]].length <= limit[lang], `${route}: ${field}.${lang} exceeds ${limit[lang]} characters`)
    for (const section of article.sections) assert.ok(section.heading?.[lang]?.trim() && section.body?.[lang]?.trim(), `Invalid article content: ${route} section ${section.id} (${lang})`)
    for (const item of article.faq ?? []) assert.ok(item.q?.[lang]?.trim() && item.a?.[lang]?.trim(), `Invalid article content: ${route} faq (${lang})`)
    assert.ok(article.skill.text?.[lang]?.trim(), `Invalid article skill: ${route} (${lang})`)
  }
  ;(langs.includes('en') ? articleRoutes : zhOnlyRoutes).add(route)
}

const TOPIC_PAGE_SIZE = 24
const topicPageCount = (topic, lang) => Math.max(1, Math.ceil([...guides, ...articles.filter((a) => a.langs.includes(lang))].filter((item) => topicOf(item) === topic.slug).length / TOPIC_PAGE_SIZE))
const topicRoute = (topic, number = 1) => `/guides/${topic.slug}${number > 1 ? `/page/${number}` : ''}`
const paginatedTopicRoutes = topics.flatMap((topic) => Array.from({length:topicPageCount(topic,'en')}, (_, i) => topicRoute(topic,i+1)))
for (const topic of topics) for (let number = topicPageCount(topic,'en') + 1; number <= topicPageCount(topic,'zh'); number++) zhOnlyRoutes.add(topicRoute(topic,number))
/** English routes; every one also exists under /zh. */
for (const item of [...guides, ...articles]) assert.ok(topicIds.has(topicOf(item)), `Unknown guide topic: ${item.slug}`)
const orgRoutes = new Set(['/', ...paginatedTopicRoutes, '/projects', ...projects.map((p) => `/${p.slug}`), ...guideRoutes, ...articleRoutes, '/guides', ...comparisonRoutes, '/glossary', ...glossary.map((g) => `/glossary/${g.slug}`)])
/** Whether an English-form organization route exists on the site of `lang`. */
const routeExists = (lang, route) => orgRoutes.has(route) || (lang === 'zh' && zhOnlyRoutes.has(route))
for (const article of articles) {
  for (const route of article.related ?? []) assert.ok(routeExists('zh', route), `/${article.owner}/${article.slug} lists an unknown related page ${route}`)
}

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
    .replace(/href="(?:https:\/\/zenstory\.ai)?(\/[^"#?]*)([#?][^"]*)?"/g, (m, path, rest) => (routeExists('zh', path) ? `href="${L(path)}${rest ?? ''}"` : m))
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

/** Site paths served on one URL for both languages; they are not organization routes. */
const SINGLE_URL = /^\/(?:docs(?:\/|$)|llms\.txt$|privacy-policy$|terms-of-service$)/
/**
 * Inline markup for article prose: `code`, **bold**, and [text](url) links where
 * the url is absolute or a site path such as /oh-story/novel-opening. Links to this
 * site (relative, or written as https://zenstory.ai/…) are output as paths, so
 * Chinese pages localize them, and must name a page that exists in the current
 * language. Code spans are literal: no bold or links inside them.
 */
const prose = (s) => esc(s)
  .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  .replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^)\s]*)\)/g, (m, text, url) => {
    let href = url
    const own = url.match(/^https:\/\/(?:www\.)?zenstory\.ai(?=\/|$)(.*)$/)
    if (own) href = own[1] || '/'
    if (href.startsWith('/')) {
      const [, path, rest] = href.match(/^([^?#]*)(.*)$/)
      const route = path.length > 1 ? path.replace(/\/$/, '') : path
      assert.ok(SINGLE_URL.test(route) || routeExists(LANG, route), `Article links a page that does not exist in ${LANG}: ${url}`)
      href = `${route}${rest}`
    }
    return `<a href="${href}">${text}</a>`
  })
const inline = (s) => String(s).split(/(`[^`]+`)/).map((part, i) => (i % 2 ? `<code>${esc(part.slice(1, -1))}</code>` : prose(part))).join('')

/**
 * Block markup for article bodies (blocks separated by a blank line): a block is
 * a paragraph, a "- " list (all "- [ ] " lines make a checklist), a "1. " list, a "| a | b |" table (second row is the
 * |---| divider), a "> " quote (used for original examples) or one "### " heading.
 * A block that starts like a list, table or quote must be one throughout.
 */
const md = (text) => String(text).trim().split(/\n{2,}/).map((block) => {
  const lines = block.split('\n')
  const all = (pattern) => lines.every((line) => pattern.test(line))
  const kind = /^- /.test(lines[0]) ? 'ul' : /^\d+\. /.test(lines[0]) ? 'ol' : /^\|/.test(lines[0]) ? 'table' : /^> ?/.test(lines[0]) ? 'quote' : /^### /.test(lines[0]) ? 'h3' : 'p'
  if (kind === 'ul') {
    assert.ok(all(/^- /), `Mixed list block: ${lines[0]}`)
    // "- [ ] item" is a copyable checklist line.
    const checklist = lines.every((line) => /^- \[ \] /.test(line))
    return `<ul${checklist ? ' class="checklist"' : ''}>${lines.map((line) => `<li>${inline(checklist ? line.slice(6) : line.slice(2))}</li>`).join('')}</ul>`
  }
  if (kind === 'ol') { assert.ok(all(/^\d+\. /), `Mixed list block: ${lines[0]}`); return `<ol>${lines.map((line) => `<li>${inline(line.replace(/^\d+\. /, ''))}</li>`).join('')}</ol>` }
  if (kind === 'quote') { assert.ok(all(/^> ?/), `Mixed quote block: ${lines[0]}`); return `<blockquote>${lines.map((line) => line.replace(/^> ?/, '').trim()).filter(Boolean).map((para) => `<p>${inline(para)}</p>`).join('')}</blockquote>` }
  if (kind === 'h3') { assert.equal(lines.length, 1, `A ### heading is its own block: ${lines[0]}`); return `<h3>${inline(lines[0].slice(4))}</h3>` }
  if (kind === 'table') {
    assert.ok(all(/^\|.*\|$/) && lines.length >= 3 && /^\|[\s:|-]+\|$/.test(lines[1]), `Malformed table: ${lines[0]}`)
    const cells = (line) => line.slice(1, -1).split('|').map((cell) => cell.trim())
    const width = cells(lines[0]).length
    assert.ok(lines.every((line) => cells(line).length === width), `Ragged table: ${lines[0]}`)
    // A scroll container must be keyboard-focusable and named (axe: scrollable-region-focusable).
    const label = `${t('Table', '表格')}: ${cells(lines[0]).join(' / ')}`.replace(/[`*]/g, '')
    return `<div class="table-wrap" role="region" tabindex="0" aria-label="${esc(label)}"><table><thead><tr>${cells(lines[0]).map((cell) => `<th scope="col">${inline(cell)}</th>`).join('')}</tr></thead><tbody>${lines.slice(2).map((line) => `<tr>${cells(line).map((cell) => `<td>${inline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
  }
  // A list, table, quote or heading line inside a paragraph means a missing blank line.
  assert.ok(!/^#{1,6} /.test(lines[0]) && lines.slice(1).every((line) => !/^(?:- |\d+\. |\||> ?|#{1,6} )/.test(line)), `Block markup inside a paragraph (add a blank line before it): ${lines[0]}`)
  return `<p>${lines.map(inline).join('<br>')}</p>`
}).join('\n')
const comparisonLinks = (project) => comparisons.filter((comparison) => comparison.options.some((option) => option.project === project))
const guidesOf = (slug) => guides.filter((g) => g.owner === slug)
/** "Oh Story（网文写作 skill 包）" → the parenthetical becomes a subordinate line (same characters). */
const zhName = (p) => { const m = p.name.zh.match(/^(.*?)(（.*）)$/); return m ? `${esc(m[1])}<span class="paren">${esc(m[2])}</span>` : esc(p.name.zh) }
/** Project name as a heading: English name, or the Chinese name with its parenthetical subordinate. */
const projectName = (p) => t(esc(p.name.en), zhName(p))

// ---------- small UI glyphs (inline, monochrome; the star keeps the brand cyan) ----------

const starGlyph = '<svg class="star" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M8 1.2c.62 2.6 1.66 3.64 4.26 4.26-2.6.62-3.64 1.66-4.26 4.26-.62-2.6-1.66-3.64-4.26-4.26 2.6-.62 3.64-1.66 4.26-4.26Z" fill="#22D3EE"/></svg>'
const proof = (html, cls = '') => `<span class="proof${cls ? ` ${cls}` : ''}">${html}</span>`
/** Chip row. The date the star counts were read is stated once per page, in a facts line, not next to every number. */
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
  if (route === '/guides' || route.startsWith('/guides/') || guideRoutes.has(route) || articleRoutes.has(route) || zhOnlyRoutes.has(route)) return '/guides'
  if (route === '/glossary' || route.startsWith('/glossary/')) return '/glossary'
  return null
}

/** A complete document for the English route `route` in the current language. */
const page = ({ route, title, description, ogType = 'article', ld, body, alternates = alternatesOf(route), switchLinks = alternates, pageId, fonts }) =>
  shellPage({ lang: LANG, route: L(route), alternates, switchLinks, section: navSection(route), title, description, ogType, ld, body: localizeBody(body), pageId, fonts })

const roster = (current) => `
<section class="roster band-cream" aria-labelledby="roster-h">
  <div class="wrap">
  ${heading(2, 'Part of ZenStory AI', 'ZenStory AI 项目', 'roster-h')}
  ${pair(`<p>${esc(org.intro.en)}</p>`, `<p>${esc(org.intro.zh)}</p>`)}
  <table>
    <thead><tr><th>${t('Project', '项目')}</th><th>${t('Format', '形态')}</th><th>${t('What it does', '用途')}</th></tr></thead>
    <tbody>${projects.map((p) => `
      <tr${p.slug === current ? ' class="here"' : ''}>
        <td><a href="/${p.slug}"${p.slug === current ? ' aria-current="page"' : ''}>${toolName(p)}</a></td>
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
  ['Write in ZenStory Workbench', '在 ZenStory 工作台里写作', 'workbench'],
]

/** Proof chips for one project: stars and skills; the license is stated once per page (footer and project facts). */
const projectChips = (p) => [
  p.stars ? proof(`${starGlyph}${num(p.stars)} GitHub ${t('stars', 'star')}`) : '',
  p.skills ? proof(t(`${p.skills} skills`, `${p.skills} 个 skill`)) : '',
].filter(Boolean)

/** One card component shared by the home page and /projects: task, name, one sentence, two facts, two links. The install command lives on the project page. */
const projectCard = (p, eyebrow) => `
      <article class="project-card">
        <p class="eyebrow">${eyebrow}</p>
        <h3><a href="/${p.slug}">${toolName(p)}</a></h3>
        ${pair(`<p>${esc(p.tagline.en)}</p>`, `<p>${esc(p.tagline.zh)}</p>`, 'tagline')}
        ${proofRow([proof(esc(pick(p.format)), 'format'), ...projectChips(p)])}
        <p class="card-actions">${p.install && p.entry ? `<a href="/${p.slug}#start-h">${t('Install & first run', '安装与上手')}${arrowGlyph}</a>` : ''}<a href="${p.github}">${t('Source on GitHub', 'GitHub 源码')}${extGlyph}</a>${p.slug === 'workbench' ? `<a href="${appHref(LANG, '/login', 'org_projects')}">${t('Open app', '打开工作台')}${extGlyph}</a>` : ''}</p>
      </article>`

/** Relationship diagram (closing band): idea → novel → short drama / game, and footage → video recap, labelled with the tools. */
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
    ${node(188, 132, 132, 'Novel', '小说', 'Oh Story · Workbench')}
    ${arrow('M322 160C360 160 360 44 402 44')}
    ${arrow('M322 160H402')}
    ${node(188, 248, 132, 'Footage', '视频素材')}
    ${arrow('M322 276H402')}
    ${node(406, 16, 142, 'Short drama', '短剧 / 漫剧', 'Drama Skills')}
    ${node(406, 132, 142, 'Game', '互动游戏', 'Novel to Game')}
    ${node(406, 248, 142, 'Video recap', '解说视频', 'Video Recap Skills')}
  </svg>`
}

// ---------- organization homepage ----------

const homeReading = JSON.parse(readFileSync(join(webRoot, 'content/home-reading.json'), 'utf8'))
const showcases = JSON.parse(readFileSync(join(webRoot, 'content/showcases.json'), 'utf8'))
const showcaseIds = new Set()
for (const item of showcases) {
  assert.match(item.slug, /^[a-z0-9-]+$/, 'Invalid showcase identity')
  assert.ok(!showcaseIds.has(item.slug), 'Duplicate showcase identity')
  showcaseIds.add(item.slug)
  const owner = projects.find((p) => p.slug === item.owner)
  assert.ok(owner, 'Unknown showcase project')
  assert.ok(item.source.startsWith(`${owner.github}/blob/`), 'Showcase source belongs to another project')
  assert.ok(item.method.startsWith(`/${item.owner}/`) && LANGS.every((lang) => routeExists(lang, item.method)), `Unknown showcase method: ${item.method}`)
  if (item.demo) assert.match(item.demo, /^https:\/\/[^\s"<>]+$/, 'Invalid showcase demo URL')
  assert.match(item.source, /^https:\/\/github\.com\/zenstory-ai\/[^/]+\/blob\/[a-f0-9]{40}\/.+$/, 'Showcase source must be pinned')
  for (const field of ['title', 'kind', 'description', 'input', 'output']) for (const lang of LANGS) assert.ok(item[field]?.[lang]?.trim(), `Missing showcase ${field}`)
  if (item.video) {
    assert.match(item.video.url, /^https:\/\/github\.com\/user-attachments\/assets\/[a-f0-9-]{36}$/, 'Invalid showcase video source')
    assert.match(item.video.poster, /^\/org\/demos\/[a-z0-9-]+\.jpg$/, 'Invalid video poster')
  } else {
    assert.match(item.image.file, /^\/org\/demos\/[a-z0-9-]+\.jpg$/, 'Invalid showcase image')
    for (const lang of LANGS) assert.ok(item.image.alt?.[lang]?.trim(), 'Missing showcase image description')
  }
  if (item.chain) {
    // The hero's single verified case: source text → adaptation documents → playable result, all in the owner's pinned repository.
    const { source, rule, files, result, figure, example } = item.chain
    const pinned = new RegExp(`^${owner.github.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/(?:blob|tree)/[a-f0-9]{40}/`)
    for (const url of [example, source?.url, rule?.url, result?.source, figure?.source]) assert.match(url ?? '', pinned, `Showcase chain must cite ${owner.github} at a pinned commit`)
    assert.ok(source.file && source.heading?.trim() && source.excerpt?.trim() && LANGS.every((lang) => source.note?.[lang]?.trim()), 'Invalid showcase chain source')
    assert.ok(rule.file && ['name', 'fact', 'design'].every((field) => LANGS.every((lang) => rule[field]?.[lang]?.trim())), 'Invalid showcase chain rule')
    assert.ok(Array.isArray(files) && files.length && files.every((file) => typeof file === 'string' && file.trim()), 'Invalid showcase chain files')
    for (const asset of [result, figure]) {
      assert.match(asset?.file ?? '', /^\/org\/demos\/[a-z0-9-]+\.(?:jpg|webp)$/, 'Invalid showcase chain asset')
      assert.ok(Number.isInteger(asset.width) && Number.isInteger(asset.height) && LANGS.every((lang) => asset.alt?.[lang]?.trim()), 'Invalid showcase chain asset')
    }
    assert.ok(item.demo, 'The hero case needs a playable demo')
  }
}
const showcaseImage = (item, eager = false) => `<img src="${esc(item.image.file)}" width="${item.image.width}" height="${item.image.height}" alt="${esc(pick(item.image.alt))}" loading="${eager ? 'eager' : 'lazy'}" decoding="async"${eager ? ' fetchpriority="high"' : ''}>`
/** Media shape decides the case's place in the grid: the first case is the feature, a vertical video gets a phone-shaped stage. */
const caseShape = (item, index) => (index === 0 ? ' is-feature' : item.video && item.video.height > item.video.width ? ' is-tall' : '')
/** Showcase cards in content order (content/showcases.json is the homepage curation). */
/** The hero already shows the case with a verified chain; the showcase carries the others. */
const showcaseCards = () => showcases.filter((item) => !item.chain).map((item, index) => {
  const owner = projects.find((p) => p.slug === item.owner)
  const tall = item.video && item.video.height > item.video.width
  // Native controls stay in the HTML (no-JS playback); playerScript swaps them for one play button until the first play.
  const media = item.video
    ? `<video controls playsinline preload="none" poster="${esc(item.video.poster)}" width="${item.video.width}" height="${item.video.height}" aria-label="${esc(pick(item.title))}"><source src="${esc(item.video.url)}" type="video/mp4">${item.video.captions ? `<track kind="captions" src="${esc(item.video.captions)}" srclang="zh" label="中文">` : ''}<a href="${esc(item.video.url)}">${t('Watch the video', '观看视频')}</a></video><button type="button" class="play" hidden><span class="sr-only">${t('Play', '播放')}: ${esc(pick(item.title))}</span></button>`
    : showcaseImage(item)
  const links = [
    item.video ? `<a href="${esc(item.video.url)}">${t('Watch directly', '直接观看视频')}${extGlyph}</a>` : '',
    item.demo ? `<a href="${esc(item.demo)}">${t('Play the prototype', '试玩原型')}${extGlyph}</a>` : '',
    `<a href="${esc(item.source)}">${t('README source', 'README 来源')}</a>`,
    item.transcript_source ? `<a href="${esc(item.transcript_source)}">${t('Caption transcript', '字幕稿')}</a>` : '',
  ].filter(Boolean)
  return `<article class="case-card${caseShape(item, index)}" aria-labelledby="case-${esc(item.slug)}">
    <figure class="case-media"><div class="media-frame"${tall ? ` style="--poster:url('${esc(item.video.poster)}')"` : ''}>${media}</div><figcaption><span class="case-kind">${esc(pick(item.kind))}</span><span class="case-owner">${esc(owner.name.en)}</span></figcaption></figure>
    <div class="case-copy"><h3 id="case-${esc(item.slug)}">${esc(pick(item.title))}</h3><p>${esc(pick(item.description))}</p>
    <dl class="case-io"><div><dt>${starGlyph}${t('Input', '输入')}</dt><dd>${esc(pick(item.input))}</dd></div><div><dt>${starGlyph}${t('Output', '产物')}</dt><dd>${esc(pick(item.output))}</dd></div></dl>
    <p class="case-actions"><a class="case-method" href="${esc(item.method)}">${t('Follow the method', '看创作方法')}${arrowGlyph}</a></p><p class="case-provenance">${links.join('<span aria-hidden="true"> · </span>')}</p></div>
  </article>`
}).join('')

const chainImage = (asset, { eager = false, cls = '' } = {}) => `<img${cls ? ` class="${cls}"` : ''} src="${esc(asset.file)}" width="${asset.width}" height="${asset.height}" alt="${esc(pick(asset.alt))}" loading="${eager ? 'eager' : 'lazy'}" decoding="async"${eager ? ' fetchpriority="high"' : ''}>`
/** A small label attached to one object on the stage: the mark's star, the step and where it comes from. */
const objLabel = (step, name, meta = '') => `<p class="obj-label">${starGlyph}<span class="obj-step">${step}</span><span class="obj-name">${name}</span>${meta ? `<span class="obj-meta">${meta}</span>` : ''}</p>`
/**
 * Hero stage: one verified case as one scene. The playable prototype (browser QA capture) is the
 * protagonist, with the game's own Sun Wukong stepping out of it; the source chapter lies beside it
 * on manuscript paper and a slip from the Novel to Game source bible is pinned across the seam.
 * A dashed story thread runs from the chapter to the level. Every piece links to the owner
 * repository at a pinned commit; the other cases follow in the showcase.
 */
const heroStage = () => {
  const item = showcases.find((s) => s.chain)
  assert.ok(item, 'Homepage hero needs one showcase with a verified chain')
  const { source, rule, result, figure, example } = item.chain
  const owner = projects.find((p) => p.slug === item.owner)
  const host = esc(new URL(item.demo).host)
  return `<figure class="stage">
        <div class="stage-canvas">
          <svg class="stage-thread" viewBox="0 0 1200 545" preserveAspectRatio="none" aria-hidden="true" focusable="false"><path d="M298 156C362 108 434 120 495 194"/></svg>
          <div class="stage-obj obj-source">
            ${objLabel('01', t('Source', '原著'), `<a href="${esc(source.url)}" title="${esc(pick(source.note))}">${esc(source.file)}</a>`)}
            <div class="manuscript" lang="zh-Hant"><p class="ms-heading">${esc(source.heading)}</p><p class="ms-text">${esc(source.excerpt)}</p></div>
          </div>
          <div class="stage-obj obj-result">
            ${objLabel('03', esc(pick(item.kind)))}
            <div class="tile-media"><div class="tile-bar"><span class="tile-dots" aria-hidden="true"><span></span><span></span><span></span></span><span class="tile-url">${host}</span><a class="tile-play" href="${esc(item.demo)}">${t('Play in your browser', '在浏览器里试玩')}${extGlyph}</a></div>${chainImage(result, { eager: true })}</div>
            ${chainImage(figure, { eager: true, cls: 'act-figure' })}
          </div>
          <div class="stage-obj obj-rule">
            <div class="doc-card">
              ${objLabel('02', esc(owner.name.en))}
              <p class="doc-file"><a href="${esc(rule.url)}">${esc(rule.file)}</a></p>
              <p class="doc-rule">${t(`“${esc(rule.name.en)}”`, `「${esc(rule.name.zh)}」`)}</p>
              <dl class="doc-rows"><div><dt>${t('Source', '原作')}</dt><dd>${esc(pick(rule.fact))}</dd></div><div><dt>${t('Design', '改编')}</dt><dd>${esc(pick(rule.design))}</dd></div></dl>
              ${rule.translated ? `<p class="doc-note">${t('Translated from the Chinese source bible.', '摘自原著设定集。')}</p>` : ''}
            </div>
          </div>
          <span class="thread-star" aria-hidden="true">${starGlyph}</span>
        </div>
        <figcaption><span class="stage-case">${t('<cite>Journey to the West</cite> → a playable game', '《西游记》→ 可玩游戏')}</span><a class="stage-method" href="/${item.owner}#start-h" aria-label="${t(`Start adapting: set up ${esc(owner.name.en)}`, `开始改编：安装 ${esc(owner.name.en)}`)}">${t('Start adapting', '开始改编')}${arrowGlyph}</a><a href="${esc(example)}">${t('Case files', '案例源码')}${extGlyph}</a><a href="#examples-h">${t('More demos', '更多演示')}<span class="arrow" aria-hidden="true">↓</span></a></figcaption>
      </figure>`
}
/** A knowledge-level entry `workflow:<id>` links that /guides workflow instead of repeating articles the paths already link. */
const workflowLink = (ref) => {
  const flow = WORKFLOWS.find((candidate) => `workflow:${candidate.id}` === ref)
  assert.ok(flow, `Unknown homepage workflow: ${ref}`)
  return `<li><a href="/guides#path-${flow.id}"><span class="guide-title">${t(...flow.title)}</span><span class="step-count">${t(`${flow.steps.length} steps`, `${flow.steps.length} 步`)}</span>${arrowGlyph}</a></li>`
}
const homeReadingItems = (slugs) => slugs.map((slug) => {
  const matches = readingOf().filter((item) => item.slug === slug)
  assert.equal(matches.length, 1, `Unknown or ambiguous homepage reading slug: ${slug} (${LANG})`)
  return matches[0]
})
/** Short tool name for homepage labels: the pack's English name, or the workbench in the page language. */
const toolName = (p) => (p.slug === 'workbench' ? esc(pick(p.name)) : esc(p.name.en))
const homeReadingLinks = (slugs, labels, branching = false) => {
  const items = homeReadingItems(slugs)
  // An adaptation branch names the tool it needs (the article's owner), so "choose one" reads as one more tool, not two.
  const tool = (item) => toolName(projects.find((p) => p.slug === item.owner))
  // …and shows that tool's first showcase result (the same local poster or screenshot the showcase uses), so the choice is between outcomes.
  const branchMedia = (item) => {
    const shown = showcases.find((s) => s.owner === item.owner)
    const media = shown?.image ?? (shown?.video && { file: shown.video.poster, width: shown.video.width, height: shown.video.height })
    return media ? `<span class="branch-media"><img src="${esc(media.file)}" width="${media.width}" height="${media.height}" alt="" loading="lazy" decoding="async"></span>` : ''
  }
  const links = items.map((item, i) => {
    if (!labels) return `<li>${guideLink(item)}</li>`
    const label = esc(pick(labels[i]))
    return branching && i > 0
      ? `<li><a href="/${item.owner}/${item.slug}" aria-label="${label} · ${tool(item)}: ${esc(pick(item.title))}">${branchMedia(item)}<span class="branch-label">${label}</span><span class="branch-meta"><span class="branch-tool">${tool(item)}</span>${arrowGlyph}</span></a></li>`
      : `<li><a href="/${item.owner}/${item.slug}" aria-label="${label}: ${esc(pick(item.title))}">${label}${arrowGlyph}</a></li>`
  })
  return branching ? `<ol>${links[0]}</ol><p class="path-branch-label">${t('To adapt it, choose one:', '需要改编时，任选一条：')}</p><ul class="path-branches">${links.slice(1).join('')}</ul>` : `<ol>${links.join('')}</ol>`
}
/** The tool a path starts with, linked to its row in the tool table below (where setup lives). */
const pathTools = (slugs) => {
  const list = slugs.map((slug) => projects.find((p) => p.slug === slug))
  return `<p class="path-tools"><span class="path-tools-label">${t('Start with', '起步工具')}</span>${list.map((p) => `<a href="#tool-${p.slug}">${toolName(p)}</a>`).join(`<span class="path-tools-or">${t('or', '或')}</span>`)}</p>`
}
/** A path the browser workbench can start: one direct action into its signup, for visitors with no agent to install (on a phone, say). */
const workbenchStart = (tools) => (tools.includes('workbench')
  ? `<p class="path-direct"><a href="${appHref(LANG, '/register', 'org_home_path')}">${t('No agent to install? Start writing in your browser', '不装 Agent，在浏览器里直接开写')}${extGlyph}</a></p>`
  : '')
/** Where a tool row's setup link lands: the project page's install-and-entry steps when the project has them. */
const setupLink = (p) => (p.install && p.entry
  ? `<a href="/${p.slug}#start-h">${t('Install & first run', '安装与上手')}</a>`
  : `<a href="/${p.slug}">${t(p.slug === 'workbench' ? 'About the workbench' : 'Project details', p.slug === 'workbench' ? '工作台介绍' : '项目详情')}</a>`)
const toolCards = (list) => list.map((p) => {
  const [en, zh] = taskChoices.find(([, , slug]) => slug === p.slug)
  // The title opens the project page; a separate setup link only when it lands somewhere else (the install steps).
  const setup = p.install && p.entry ? `${setupLink(p)} · ` : ''
  return `<article class="tool-choice" id="tool-${p.slug}"><span class="eyebrow">${toolName(p)}</span><h3><a href="/${p.slug}">${t(esc(en), esc(zh))}${arrowGlyph}</a></h3><span class="tool-role">${esc(pick(homeReading.tools[p.slug]))}</span><p class="tool-links">${setup}<a href="${esc(p.github)}">${t('Source on GitHub', 'GitHub 源码')}${extGlyph}</a></p></article>`
}).join('')
/** A Chinese heading in phrases: each phrase stays on one line (see `.ph`). */
const phrases = (...parts) => parts.map((part) => `<span class="ph">${part}</span>`).join('')
/**
 * Progressive enhancement for the showcase players: with JavaScript, a video shows one play button
 * instead of the native control bar until it starts; the click starts playback (never autoplay) and
 * hands over to native controls. Without JavaScript the native controls in the HTML are used.
 */
const playerScript = `<script>(function(){var f=document.querySelectorAll('.media-frame');for(var i=0;i<f.length;i++){(function(frame){var v=frame.querySelector('video'),b=frame.querySelector('button.play');if(!v||!b)return;v.controls=false;b.hidden=false;function start(){b.hidden=true;v.controls=true}b.addEventListener('click',function(){start();var p=v.play();if(p&&p.catch)p.catch(function(){})});v.addEventListener('play',start)})(f[i])}})()</script>`
const homePage = () => {
  const route = '/'
  const title = t('ZenStory AI — Open-source AI tools for writing and adapting stories', 'ZenStory AI — 开源 AI 写小说、做短剧、改游戏与视频解说工具')
  const description = t('Write novels with AI, adapt them into short drama or playable games, and turn footage into video recaps. Explore real open-source demos, workflows and practical guides.', '用 AI 写小说，再改成短剧或可玩游戏；已有视频素材，也能做成解说。看真实开源项目演示，按步骤走完创作流程，从任务、案例与模板进入知识库。')
  const ld = [orgNode, { '@type': 'WebSite', '@id': `${SITE}/#website`, name: org.name, url: SITE, description, publisher: { '@id': `${SITE}/#org` }, inLanguage: LOCALE[LANG] }]
  const { paths, levels } = homeReading
  const creative = projects.filter((p) => !['dsh', 'workbench'].includes(p.slug))
  const environments = projects.filter((p) => ['dsh', 'workbench'].includes(p.slug))
  for (const [number, , , , , slugs, , tools] of paths) {
    // A path names the tools it starts with; its ordered steps must be taught by one of them.
    assert.ok(Array.isArray(tools) && tools.length && tools.every((slug) => projects.some((p) => p.slug === slug)), `Homepage path ${number} needs known starting tools`)
    const steps = homeReadingItems(number === '02' ? slugs.slice(0, 1) : slugs)
    assert.ok(steps.every((item) => tools.includes(item.owner)), `Homepage path ${number} steps belong to a tool it does not name`)
  }
  for (const p of projects) assert.ok(LANGS.every((lang) => homeReading.tools?.[p.slug]?.[lang]?.trim()), `Homepage tool table needs a short note for ${p.slug}`)
  for (const [en, , , , , more, label] of levels) assert.ok(routeExists(LANG, more.split('#')[0]) && LANGS.every((lang) => label?.[lang]?.trim()), `Homepage knowledge level "${en}" needs a published destination and its own label`)
  const body = `
<article class="home">
  <div class="hero">
    <div class="wrap hero-head">
      <h1>${t('Write stories. <br>Make them real.', '写下故事。<br>做出作品。')}</h1>
      <p class="lede">${t('Write novels with AI, adapt them into drama or games, or turn footage into a narrated recap.', '用 AI 写小说，再改成短剧或可玩的游戏；已有视频素材，也能做成解说。')}</p>
      <div class="hero-cta">
        <p class="actions home-actions"><a class="btn" href="#start-h">${t('Start with what you have', '选一条创作路径')}<span class="arrow" aria-hidden="true">↓</span></a></p>
      </div>
    </div>
    <div class="wrap">
      ${heroStage()}
    </div>
  </div>
  <section class="band stage-band" aria-labelledby="examples-h"><div class="wrap">
    <header class="section-head">
      <p class="eyebrow">${starGlyph}<span>${t('Real demos', '真实演示')}</span></p>
      ${heading(2, 'Real projects. Visible results.', phrases('真实项目，', '看得见的产物'), 'examples-h')}
      <p class="section-lede">${t('Demos and playable prototypes from our project READMEs, each with the method behind it.', '各项目 README 里的演示与可试玩原型，每个都附创作方法。')}</p>
    </header>
    <p class="swipe-hint" aria-hidden="true">${((n) => t(`Swipe to see all ${n} demos`, `左右滑动，查看全部 ${n} 个演示`))(showcases.filter((item) => !item.chain).length)}<span class="arrow">→</span></p>
    <div class="showcase-grid">${showcaseCards()}</div>
    <p class="more stage-next"><a href="#start-h">${t('Pick a path for what you have', '看看从哪里开始')}<span class="arrow" aria-hidden="true">↓</span></a></p>
  </div></section>
  <section class="band" aria-labelledby="start-h"><div class="wrap">
    <header class="split-head">
      <p class="eyebrow">${starGlyph}<span>${t('Creative paths', '创作路径')}</span></p>
      ${heading(2, 'Start with what you have', phrases('你手上有什么，', '就从哪里开始'), 'start-h')}
      <p class="section-lede">${t('Pick one and follow the steps.', '选一条，照步骤做。')}</p>
    </header>
    <div class="learning-paths home-paths">${paths.map(([number, en, zh, desc, descZh, slugs, labels, tools]) => `<article class="learning-path"><h3>${t(en, zh)}</h3><p>${t(desc, descZh)}</p>${homeReadingLinks(slugs, labels, number === '02')}${pathTools(tools)}${workbenchStart(tools)}</article>`).join('')}</div>
  </div></section>
  <section class="band" aria-labelledby="choose-h"><div class="wrap">
    <header class="section-head">
      <p class="eyebrow">${starGlyph}<span>${t('Tools and where to use them', '工具与使用环境')}</span></p>
      ${heading(2, 'Choose by what you want to make', phrases('按你想做的作品，', '选择工具'), 'choose-h')}
      <p class="section-lede">${t('Each row leads to its install and entry command.', '每一行都直达安装与入口命令。')}</p>
    </header>
    <h3 class="tool-group-title">${t('Creative tools', '创作工具')}<span class="count">${creative.length}</span></h3>
    <div class="tool-grid">${toolCards(creative)}</div>
    <h3 class="tool-group-title">${t('Where to work', '在哪里使用')}<span class="count">${environments.length}</span></h3>
    <p class="tool-group-note">${t('The four packs install into an agent you already use, such as Claude Code or Codex. Or work in one of these:', '四套 skill 装进你已在用的 Agent（如 Claude Code、Codex）；也可以用下面两种方式：')}</p>
    <div class="environment-grid">${toolCards(environments)}</div>
    <p class="more"><a href="/compare/writing-workflows">${t('Compare writing environments', '比较写作环境')}${arrowGlyph}</a><a href="/projects">${t(`All ${projects.length} projects`, `全部 ${projects.length} 个项目`)}${arrowGlyph}</a></p>
  </div></section>
  <section class="band" aria-labelledby="guides-h"><div class="wrap library">
    <div class="library-intro">
      <p class="eyebrow">${starGlyph}<span>${t('Knowledge library', '知识体系')}</span></p>
      ${heading(2, 'From first steps to fixes, layer by layer', phrases('从上手到排错，', '按层次找到方法'), 'guides-h')}
      <form class="library-search" action="${L('/guides')}" method="get" role="search"><label for="home-search">${t('Search the library', '搜索知识库')}</label><span class="library-search-row"><input id="home-search" name="q" type="search" placeholder="${t('e.g. storyboard, CapCut draft', '例如：去AI味、分镜、剪映草稿')}"><button type="submit">${t('Search', '搜索')}</button></span></form>
      <p class="more"><a href="/guides">${t('Browse the complete library', '浏览完整知识库')}${arrowGlyph}</a></p>
    </div>
    <div class="knowledge-grid">${levels.map(([en, zh, desc, descZh, slugs, more, label]) => `<article class="knowledge-level"><h3>${t(en, zh)}</h3><p>${t(desc, descZh)}</p>${slugs.every((slug) => slug.startsWith('workflow:')) ? `<ol>${slugs.map(workflowLink).join('')}</ol>` : homeReadingLinks(slugs)}<a class="topic-more" href="${more}">${esc(pick(label))}${arrowGlyph}</a></article>`).join('')}</div>
  </div></section>
  <section class="band closing" aria-labelledby="model-h"><div class="wrap">
    <header class="section-head">
      <p class="eyebrow">${starGlyph}<span>${t('How the projects connect', '项目关系')}</span></p>
      ${heading(2, 'One story. Different ways to build.', phrases('围绕故事，', '把创作连接起来'), 'model-h')}
    </header>
    <div class="home-system"><figure>${pipelineSvg()}<figcaption>${t('A novel can become a short drama or a game; a recap starts from existing footage.', '一部小说可以改成短剧或游戏；解说视频则从现成的视频素材开始。')}</figcaption></figure></div>
    <p class="actions"><a class="btn" href="${appHref(LANG, '/register', 'org_home_closing')}">${t('Open ZenStory Workbench', '打开 ZenStory 工作台')}${extGlyph}</a><a class="btn ghost" href="#choose-h">${t('Install a skill pack in your agent', '在 Agent 中安装 skill 包')}<span class="arrow" aria-hidden="true">↑</span></a></p>
  </div></section>
</article>
${playerScript}`
  write(route, page({ route, title, description, ogType: 'website', ld, body, pageId: 'home', fonts: HOME_FONTS }))
}

// ---------- project pages ----------

/** Projects whose job is novel writing; only these offer the browser workbench as an alternative (it does not run the drama, game or recap packs). */
const WRITING_ENTRIES = new Set(['oh-story'])
const needBlock = (p) => {
  const isPack = Boolean(p.install)
  const host = p.slug === 'workbench'
    ? t(`A browser. The workbench runs at <a href="${appHref(LANG, '/login', 'org_workbench_need')}">app.zenstory.ai</a>.`, `一个浏览器。工作台运行在 <a href="${appHref(LANG, '/login', 'org_workbench_need')}">app.zenstory.ai</a>。`)
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
      ${WRITING_ENTRIES.has(p.slug) ? `<li><b>${t('Or in the browser', '或在浏览器里')}</b><span>${t(`For novel writing only: the separate <a href="/workbench">ZenStory Workbench</a> at <a href="${appHref(LANG, '/register', `org_${p.slug.replace(/-/g, '_')}_need`)}">app.zenstory.ai</a>.`, `仅限小说写作：独立的 <a href="/workbench">ZenStory 工作台</a>，<a href="${appHref(LANG, '/register', `org_${p.slug.replace(/-/g, '_')}_need`)}">app.zenstory.ai</a>。`)}</span></li>` : ''}
    </ul>
  </section>`
}

const startBlock = (p, own, listed = own) => {
  if (!p.install || !p.entry) return ''
  return `
  <section class="start" aria-labelledby="start-h">
    ${heading(2, 'Start in 3 steps', '三步开始', 'start-h')}
    <ol class="start-list">
      <li><b>${t('Install', '安装')}</b><span><code class="cmd">${cmd(p.install)}</code></span></li>
      <li><b>${t('Entry command', '入口命令')}</b><span>${t(`Run <code>${esc(p.entry)}</code> in your agent host.`, `在 Agent 宿主中运行 <code>${esc(p.entry)}</code>。`)}</span></li>
      <li><b>${t('Follow a guide', '按指南操作')}</b><span>${listed.length ? t(`Pick one of the ${listed.length} guides below for your first task.`, `从下方 ${listed.length} 篇指南中选一个，完成第一个任务。`) : own.length ? t('Find your first task under “How it works” below and open its guide.', '在下方「流程」里找到你的第一个问题，打开对应的指南。') : t('Read the source README for the first task.', '按源码 README 完成第一个任务。')}</span></li>
    </ol>
  </section>`
}

/**
 * A project's craft articles as a way in, not a dump: the complete workflow built on this project's
 * skills (if any), then its creative tasks with how many articles each holds, linking the task pages.
 */
const projectCraft = (p, craft) => {
  const flows = WORKFLOWS.filter((flow) => flow.steps.every((ref) => ref.startsWith(`${p.slug}/`)) && workflowItems(flow).length > 1)
  const tasks = topics.map((topic) => [topic, craft.filter((a) => topicOf(a) === topic.slug).length]).filter(([, count]) => count)
  return `<p class="section-lede">${t(`${craft.length} craft articles, each built on a method file in this project's skills.${flows.length ? ' Start with the complete workflow, or open a task.' : ''}`, `共 ${craft.length} 篇写作技法，每篇都依据本项目 skill 里的方法文件。${flows.length ? '可以先走一遍完整创作路径，或按任务进入。' : ''}`)}</p>
  ${flows.map((flow) => `<div class="learning-path project-path" id="path-${flow.id}"><p class="path-start">${t('Complete workflow', '完整创作路径')}</p><h3>${t(...flow.title)}</h3><ol>${workflowItems(flow).map(skillRow).join('')}</ol></div>`).join('')}
  <h3 class="task-index-title">${t('By creative task', '按任务查看')}</h3>
  <ul class="task-index">${tasks.map(([topic, count]) => `<li><a href="/guides/${topic.slug}"><span class="task-name">${esc(pick(topic.title))}</span><span class="task-count">${t(`${count} from ${esc(p.name.en)}`, `${count} 篇`)}</span></a></li>`).join('')}</ul>`
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
  // "How it works" already links guides as worked examples; the guide list only adds the ones it doesn't.
  const methodRefs = new Set([...JSON.stringify(p.method).matchAll(/\]\((?:https:\/\/zenstory\.ai)?(\/[a-z0-9-]+\/[a-z0-9-]+)/g)].map((m) => m[1]))
  const listed = own.filter((g) => !methodRefs.has(`/${g.owner}/${g.slug}`))
  const craft = articlesOf(p.slug)
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
        ${p.slug === 'workbench' ? `<a class="btn ghost" href="${appHref(LANG, '/login', 'org_workbench')}">${t('Open the workbench', '打开工作台')}${extGlyph}</a><a class="btn ghost" href="/docs">${t('Workbench docs', '工作台文档')}${arrowGlyph}</a>` : ''}
      </p>
      ${installBlock(p)}
    </div>
  </header>

  <div class="wrap page-grid">
  ${jumpNav([
    ['need-h', 'What you need', '你需要什么'],
    ...(p.install && p.entry ? [['start-h', 'Start in 3 steps', '三步开始']] : []),
    ...(listed.length ? [['guides-h', 'Practical guides', '实用指南']] : []),
    ...(craft.length ? [['craft-h', 'Writing craft', '写作技法']] : []),
    ['method-h', 'How it works', '流程'],
    ['sources-h', 'Sources', '来源'],
  ], 'page-rail')}
  <div class="page-body">
  ${pair(`<h2>What it is</h2>
  <p>${rich(p.definition.en)}</p>`, `<h2>它是什么</h2>
  <p>${rich(p.definition.zh)}</p>`, 'definition')}

  ${needBlock(p)}
  ${startBlock(p, own, listed)}

  ${listed.length ? `<section aria-labelledby="guides-h">${heading(2, 'Practical guides', '实用指南', 'guides-h')}
  ${guideList(listed)}</section>` : ''}
  ${craft.length ? `<section aria-labelledby="craft-h">${heading(2, 'Writing craft', '写作技法', 'craft-h')}
  ${projectCraft(p, craft)}</section>` : ''}

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
  <p class="facts">${t(`Based on the source as of ${esc(p.sources.checked_on)}${p.stars ? ` (star counts from ${p.sources.checked_on === org.proof.as_of ? 'the same day' : esc(org.proof.as_of)})` : ''}; links point at that version.`, `本页内容依据 ${esc(p.sources.checked_on)} 的源码${p.stars ? `（star 数${p.sources.checked_on === org.proof.as_of ? '同日统计' : `统计于 ${esc(org.proof.as_of)}`}）` : ''}，链接指向该版本。`)}</p>
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

/**
 * "On this page": a sticky side rail on wide screens; on phones a closed disclosure (one row), so the
 * article starts on the first screen. The inline script opens it on wide screens before it is painted
 * beside the article; without JavaScript it stays closed and opens on tap.
 */
const tocNav = (entries) => `<nav class="guide-contents" aria-label="${t('On this page', '本页导航')}"><details><summary><b>${t('On this page', '本页导航')}</b><span class="toc-count">${t(`${entries.length} sections`, `${entries.length} 节`)}</span></summary>
    <ul>${entries.map(([id, text]) => `<li><a href="#${id}">${esc(text)}</a></li>`).join('')}</ul>
  </details><script>if(matchMedia('(min-width: 961px)').matches)document.currentScript.previousElementSibling.open=true</script></nav>`
/**
 * Complete workflows on /guides, grouped by what the writer starts with (the homepage's material paths).
 * Steps are `owner/slug` of guides or articles; every step page shows its place in the workflow and the next step.
 */
const WORKFLOWS = [
  { id: 'first-chapter', start: ['From an idea', '从一个想法开始'], title: ['Write your first chapter', '写出第一章'], steps: ['oh-story/claude-code-novel-writing', 'oh-story/novel-outline-template', 'oh-story/novel-opening', 'oh-story/revise-ai-prose'] },
  { id: 'first-short-drama', start: ['From a novel', '已经有小说'], title: ['Make your first short-drama episode', '做出第一集短剧'], steps: ['drama-skills/novel-to-short-drama', 'drama-skills/episode-outline-template', 'drama-skills/script-to-storyboard', 'drama-skills/character-consistency'] },
  { id: 'first-game', start: ['From a novel', '已经有小说'], title: ['Build a playable game prototype', '做出可玩的游戏原型'], steps: ['novel-to-game/quick-start', 'novel-to-game/choose-gameplay', 'novel-to-game/prototype-a-story-driven-game-vertical-slice', 'novel-to-game/playtest-a-novel-game-adaptation'] },
  { id: 'footage-recap', start: ['From footage', '已经有视频'], title: ['Turn footage into a recap', '把视频剪成解说'], steps: ['video-recap/video-to-narration', 'video-recap/recap-script', 'video-recap/original-audio-and-narration', 'video-recap/capcut-draft'] },
]
const itemRoute = (item) => `/${item.owner}/${item.slug}`
/** Published steps of a workflow in this language (test fixtures swap the article library; the real one is checked in guide-library tests). */
const workflowItems = (flow) => flow.steps.map((ref) => readingOf().find((candidate) => `${candidate.owner}/${candidate.slug}` === ref)).filter(Boolean)
/**
 * The skill behind a guide or article and the method files it cites. Articles name them; guides cite
 * them as markdown links in their sources. Every link points at a pinned commit of the owner repository.
 */
const methodOf = (item) => {
  // A source labelled only "method used in this article" is named by its file, which says more.
  const fileLabel = (label, url) => (/^(?:本文方法来源|Method used in this article)$/.test(label) ? url.replace(/[#?].*$/, '').split('/').pop().replace(/\.(?:md|py|sh)$/, '') : label)
  if (item.skill) return { name: item.skill.name, url: item.skill.url ?? `${projects.find((p) => p.slug === item.owner).github}/tree/main/skills/${item.skill.name}`, files: (item.sources ?? []).map((source) => ({ label: fileLabel(pick(source.label), source.url), url: source.url })) }
  const links = [...pick(item.sources).join('\n').matchAll(/\[([^\]]+)\]\((https:\/\/github\.com\/[^)\s]+)\)/g)].map(([, label, url]) => ({ label: label.replace(/`/g, ''), url }))
  // A guide that cites only a skill's reference files still names that skill: its SKILL.md at the same pinned commit.
  const pinned = links.filter((link) => /\/blob\/[a-f0-9]{40}\/(?:packages\/knowledge\/[^/]+\/)?skills\/[a-z0-9-]+\//.test(link.url))
  if (!pinned.length) return null
  const skill = pinned.find(({ url }) => /\/SKILL\.md(?:#|$)/.test(url)) ?? pinned[0]
  const [, base, name] = skill.url.match(/^(.*\/skills\/([a-z0-9-]+))\//)
  return { name, url: `${base}/SKILL.md`, files: pinned.filter((link) => !/\/SKILL\.md(?:#|$)/.test(link.url)) }
}
const dot = '<span class="sep" aria-hidden="true"> · </span>'
/** Head of a guide or article: the skill whose method it teaches and the first method files, linked at their pinned commit. */
const methodStrip = (item, filesAnchor = null) => {
  const method = methodOf(item)
  if (!method) return ''
  // A guide lists every method file in its own Sources section, so its head points there instead of repeating them.
  const files = filesAnchor
    ? (method.files.length ? `${dot}<a href="${filesAnchor}">${t(`${method.files.length} method files`, `${method.files.length} 个方法文件`)}<span class="arrow" aria-hidden="true">↓</span></a>` : '')
    : method.files.slice(0, 2).map((file) => `${dot}<a href="${esc(file.url)}">${esc(file.label)}</a>`).join('')
  return `<p class="method-strip"><span class="method-label">${t('Method from', '方法来自')}</span><a class="skill-chip" href="${esc(method.url)}"><code>${esc(method.name)}</code></a>${files}</p>`
}
/** The same provenance as a plain line inside a list row (the row itself is already a link). */
const methodFacts = (item) => {
  const method = methodOf(item)
  return method ? `<p class="method-facts"><code>${esc(method.name)}</code>${method.files.slice(0, 2).map((file) => `${dot}${esc(file.label)}`).join('')}</p>` : ''
}
/** Where a step page sits in a workflow: a line at the top, and the next step (or the way back) at the end. */
const workflowNav = (item, where) => WORKFLOWS.map((flow) => {
  const items = workflowItems(flow)
  const index = items.indexOf(item)
  if (index < 0) return ''
  const next = items[index + 1]
  const place = t(`${flow.title[0]}, step ${index + 1} of ${items.length}`, `${flow.title[1]} · 第 ${index + 1}/${items.length} 步`)
  if (where === 'top') return `<p class="path-step"><a href="/guides#path-${flow.id}">${t('Workflow', '创作路径')}${dot}${place}</a></p>`
  return `<nav class="path-next" aria-label="${t('Workflow', '创作路径')}"><p class="path-next-place">${place}</p>${next
    ? `<a href="${itemRoute(next)}"><span class="path-next-label">${t('Next step', '下一步')}</span><span class="guide-title">${esc(pick(next.title))}</span>${arrowGlyph}</a>`
    : `<a href="/guides#learning-paths"><span class="path-next-label">${t('Workflow complete', '这条路径走完了')}</span><span class="guide-title">${t('See the other workflows', '看看其他创作路径')}</span>${arrowGlyph}</a>`}</nav>`
}).join('')

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
  <p class="eyebrow"><a href="/guides/${topicOf(g)}">${esc(pick(topics.find((topic) => topic.slug === topicOf(g)).title))}</a></p>
  <h1>${esc(pick(g.title))}</h1>
  <p class="facts">${t(`${esc(owner.name.en)} · Updated ${esc(g.checked_on)}`, `${esc(owner.name.en)} · 更新于 ${esc(g.checked_on)}`)}</p>
  ${workflowNav(g, 'top')}
  ${pair(`<p>${rich(g.answer.en)}</p>`, `<p>${rich(g.answer.zh)}</p>`, 'answer')}
  ${methodStrip(g, '#sources')}
  <p class="actions guide-actions"><a class="crumb" href="/${owner.slug}">${t('Part of', '所属项目')} <b>${esc(owner.name.en)}</b>${arrowGlyph}</a><a class="crumb" href="${owner.github}">${t('Source on GitHub', '在 GitHub 查看源码')}${extGlyph}</a></p>
  </header>
  ${tocNav([
    ['before-you-start', t('Before you start', '开始之前')],
    ['steps', t('Steps', '操作步骤')],
    [`example-${LANG}`, t('Example', '示例')],
    ...(g.faq?.length ? [['faq', t('FAQ', '常见问题')]] : []),
    ['expected-files', t('Expected files', '预期文件')],
    ['verify-result', t('Check the result', '检查结果')],
    ['sources', t('Sources', '来源')],
  ])}
  <div class="guide-body">
  ${heading(2, 'Before you start', '开始之前', 'before-you-start')}
  ${pair(list(g.prerequisites.en), list(g.prerequisites.zh), 'cols')}
  ${heading(2, 'Steps', '操作步骤', 'steps')}
  ${pair(steps(g.steps.en), steps(g.steps.zh), 'cols')}
  ${heading(2, 'Example', '示例', 'examples')}
  ${pair(guideExample(g.example.en), guideExample(g.example.zh), 'cols')}
  ${g.faq?.length ? `${heading(2, 'FAQ', '常见问题', 'faq')}
  ${((faqHtml) => pair(faqHtml, faqHtml, 'cols'))(g.faq.map((item) => `<h3>${esc(pick(item.q))}</h3><p>${inline(pick(item.a))}</p>`).join(''))}` : ''}
  ${heading(2, 'Expected files', '预期文件', 'expected-files')}
  ${pair(list(g.outputs.en), list(g.outputs.zh), 'cols')}
  ${heading(2, 'Check the result', '检查结果', 'verify-result')}
  ${pair(list(g.verification.en), list(g.verification.zh), 'cols')}
  ${heading(2, 'Sources', '来源', 'sources')}
  ${pair(list(g.sources.en), list(g.sources.zh), 'cols')}
  ${workflowNav(g, 'end')}
  <p class="actions guide-end"><a class="btn ghost" href="/guides">${t('All guides', '全部指南')}${arrowGlyph}</a></p>
  </div>
</article>`
  write(route, page({ route, title: `${pick(g.title)} | ZenStory AI`, description: summary(pick(g.answer)), ld, body }))
}

// ---------- craft articles ----------

/** Articles of a project that exist on the current language's site. */
const articlesOf = (slug) => articles.filter((a) => a.owner === slug && a.langs.includes(LANG))
/** Guides and craft articles (of one project, or all) on the current language's site. */
const readingOf = (slug) => [...guides, ...articles.filter((a) => a.langs.includes(LANG))].filter((item) => !slug || item.owner === slug)

/** Title of an organization route on the current language's site, for related-reading lists. */
const routeTitle = (route) => {
  const guide = guides.find((g) => `/${g.owner}/${g.slug}` === route)
  if (guide) return pick(guide.title)
  const article = articles.find((a) => `/${a.owner}/${a.slug}` === route)
  if (article) return pick(article.title)
  const term = glossary.find((g) => `/glossary/${g.slug}` === route)
  if (term) return t(`${term.term} — ${term.bridge.split(/\s*[/(]/)[0].trim()}`, `${term.term}是什么意思`)
  const project = projects.find((p) => `/${p.slug}` === route)
  if (project) return t(project.name.en, project.name.zh)
  const comparison = comparisons.find((c) => `/compare/${c.slug}` === route)
  if (comparison) return pick(comparison.title)
  return { '/guides': t('All guides', '全部指南'), '/glossary': t('Glossary', '术语表'), '/projects': t('All projects', '全部项目') }[route]
}

/** Related-reading label: a Chinese glossary term keeps its zh-CN marker on English pages. */
const relatedLabel = (route) => {
  const term = glossary.find((g) => `/glossary/${g.slug}` === route)
  return term && LANG === 'en' ? esc(routeTitle(route)).replace(esc(term.term), () => `<span lang="zh-CN">${esc(term.term)}</span>`) : esc(routeTitle(route))
}

const articlePage = (a) => {
  const owner = projects.find((p) => p.slug === a.owner)
  const route = `/${a.owner}/${a.slug}`
  const url = U(route)
  const bilingual = a.langs.includes('en')
  const related = (a.related ?? []).filter((r) => routeExists(LANG, r))
  const faq = a.faq ?? []
  const contents = [
    ...a.sections.map((section) => [section.id, pick(section.heading)]),
    ...(faq.length ? [['faq', t('FAQ', '常见问题')]] : []),
    ['use-the-skill', t('Do it with the skill', '用 skill 来做')],
    ...(related.length ? [['related', t('Related reading', '相关阅读')]] : []),
  ]
  const ld = [
    orgNode,
    { '@type': 'Article', '@id': `${url}#article`, url,
      headline: pick(a.title), description: pick(a.description), inLanguage: LOCALE[LANG],
      datePublished: a.published_on, dateModified: a.updated_on, image: `${SITE}/brand/og-zenstory-ai.png`,
      author: { '@id': `${SITE}/#org` }, publisher: { '@id': `${SITE}/#org` } },
    breadcrumb([['ZenStory AI', U('/')], [owner.name.en, U(`/${owner.slug}`)], [pick(a.title), url]]),
  ]
  const zhMark = LANG === 'zh' ? ' lang="zh-CN"' : ''
  const body = `
<article class="guide article">
  <header class="guide-head">
  <p class="eyebrow"><a href="/guides/${topicOf(a)}">${esc(pick(topics.find((topic) => topic.slug === topicOf(a)).title))}</a></p>
  <h1>${esc(pick(a.title))}</h1>
  <p class="facts">${t(`${esc(owner.name.en)} · Updated ${esc(a.updated_on)}`, `${esc(owner.name.en)} · 更新于 ${esc(a.updated_on)}`)}</p>
  ${workflowNav(a, 'top')}
  <div class="pair answer"><div class="l-${LANG}"${zhMark}>${md(pick(a.answer))}</div></div>
  ${methodStrip(a)}
  </header>
  ${tocNav(contents)}
  <div class="guide-body article-body"${zhMark}>
  ${a.sections.map((section) => `<section aria-labelledby="${section.id}">
  <h2 id="${section.id}">${esc(pick(section.heading))}</h2>
  ${md(pick(section.body))}
  </section>`).join('\n  ')}
  ${faq.length ? `<section aria-labelledby="faq">
  <h2 id="faq">${t('FAQ', '常见问题')}</h2>
  ${faq.map((item) => `<h3>${esc(pick(item.q))}</h3>\n  ${md(pick(item.a))}`).join('\n  ')}
  </section>` : ''}
  <section class="use-skill" aria-labelledby="use-the-skill">
  <h2 id="use-the-skill">${t('Do it with the skill', '用 skill 来做')}</h2>
  ${md(pick(a.skill.text))}
  ${a.sources?.length > 2 ? `<p>${t('More of the method:', '更多方法依据：')} ${a.sources.slice(2).map((source) => `<a href="${esc(source.url)}">${esc(pick(source.label))}</a>`).join(' · ')}</p>` : ''}
  <p class="actions"><a class="btn ghost" href="/${owner.slug}">${t(`Install and use ${esc(owner.name.en)}`, `安装并使用 ${esc(owner.name.en)}`)}${arrowGlyph}</a></p>
  </section>
  ${workflowNav(a, 'end')}
  ${related.length ? `<section aria-labelledby="related">
  <h2 id="related">${t('Related reading', '相关阅读')}</h2>
  <ul class="guide-list">${related.map((r) => { const label = relatedLabel(r); return `<li>${listLink(r, label, label)}</li>` }).join('')}</ul>
  </section>` : ''}
  <p class="actions guide-end"><a class="btn ghost" href="/guides">${t('All guides', '全部指南')}${arrowGlyph}</a></p>
  </div>
</article>`
  const alternates = bilingual ? alternatesOf(route) : null
  write(route, page({ route, title: `${pick(a.seo_title)} | ZenStory AI`, description: pick(a.description), ld, body, alternates, switchLinks: alternates ?? { en: `/${owner.slug}`, zh: L(route) } }))
}

// ---------- creator task library ----------

const topicReading = (topic) => readingOf().filter((item) => topicOf(item) === topic.slug)
const topicFeatured = (topic) => {
  const own = topicReading(topic)
  const first = (topic.featured ?? []).map((slug) => own.find((item) => item.slug === slug)).filter(Boolean)
  return [...first, ...own.filter((item) => !first.includes(item))].slice(0, 3)
}
const libraryList = (items) => `<ul class="reading-list">${items.map((item) => `<li>${guideLink(item)}<p>${esc(item.description ? pick(item.description) : summary(pick(item.answer)))}</p>${methodFacts(item)}</li>`).join('')}</ul>`
/** The four workflows, each under the material it starts from; every step names the skill whose method it teaches. */
/** A guide row that also names the skill behind it (the row link and the skill tag, not nested). */
const skillRow = (item) => `<li>${guideLink(item)}${methodOf(item) ? `<code class="step-skill">${esc(methodOf(item).name)}</code>` : ''}</li>`
const skillList = (items) => `<ul class="guide-list skill-list">${items.map(skillRow).join('')}</ul>`
const learningPaths = () => `<div class="learning-paths">${WORKFLOWS.filter((flow) => workflowItems(flow).length > 1).map((flow) => `<div class="learning-path" id="path-${flow.id}"><p class="path-start">${t(...flow.start)}</p><h3>${t(...flow.title)}</h3><ol>${workflowItems(flow).map(skillRow).join('')}</ol></div>`).join('')}</div>`
/** All creative tasks as compact tiles: name and how many guides it holds. */
const taskIndex = () => `<ul class="task-index">${topics.filter((topic) => topicReading(topic).length).map((topic) => `<li><a href="/guides/${topic.slug}"><span class="task-name">${esc(pick(topic.title))}</span><span class="task-count">${t(`${topicReading(topic).length} guides`, `${topicReading(topic).length} 篇`)}</span></a></li>`).join('')}</ul>`
/** /guides reads in the homepage's four knowledge layers, in order: get started → whole workflows → techniques by task → revise and look up. */
const GUIDE_LAYERS = [
  ['get-started', 'Get started', '入门'],
  ['learning-paths', 'Complete workflows', '完整创作路径'],
  ['browse-topics', 'Go deeper by task', '按任务深入'],
  ['revise-and-look-up', 'Revise and look up', '改稿与查询'],
]
const layerHead = (index, lede) => {
  const [id, en, zh] = GUIDE_LAYERS[index]
  return `<p class="layer-step">${String(index + 1).padStart(2, '0')} / ${String(GUIDE_LAYERS.length).padStart(2, '0')}</p><h2 id="${id}">${t(en, zh)}</h2><p class="section-lede">${lede}</p>`
}
const topicBySlug = (slug) => topics.find((topic) => topic.slug === slug)
/** Three guides of a task for a /guides layer, skipping any the workflows on the same page already list. */
const layerPicks = (topic) => {
  const inWorkflows = new Set(WORKFLOWS.flatMap(workflowItems))
  const own = topicReading(topic)
  const first = (topic.featured ?? []).map((slug) => own.find((item) => item.slug === slug)).filter(Boolean)
  return [...first, ...own.filter((item) => !first.includes(item))].filter((item) => !inWorkflows.has(item)).slice(0, 3)
}
const topicMore = (topic) => `<a class="topic-more" href="/guides/${topic.slug}">${t(`All ${topicReading(topic).length} guides in ${esc(pick(topic.title))}`, `${esc(pick(topic.title))}：全部 ${topicReading(topic).length} 篇`)}${arrowGlyph}</a>`

const guidesIndex = () => {
  const route = '/guides'
  const title = t('Writing and Adaptation Guides | ZenStory AI', '创作指南：小说、短剧、AI 视频与游戏 | ZenStory AI')
  const description = t('Find practical guides by creative task: novel outlines, characters, revision, short drama, AI video, interactive games and video recaps.', '按创作任务查找实用指南：小说大纲、人物对白、去AI味、短剧剧本、AI 视频、互动游戏与视频解说。附模板、原创案例和 skill 方法来源。')
  const groups = topics.filter((topic) => topicReading(topic).length)
  const ld = [orgNode, { '@type':'ItemList', name:t('Writing and adaptation topics','创作与改编主题'), url:U(route), itemListElement:groups.map((topic,i)=>({'@type':'ListItem',position:i+1,name:pick(topic.title),url:U(`/guides/${topic.slug}`)})) }, breadcrumb([['ZenStory AI',U('/')],[t('Guides','指南'),U(route)]])]
  const starter = topicBySlug('getting-started')
  const revision = topicBySlug('revision')
  const body = `
<article class="guides-index">
  <header class="page-hero"><div class="wrap">
    <p class="eyebrow">${t('Writing and adaptation', '写作与改编')}</p>
    <h1>${t('What are you working on?', '你正在写什么、做什么？')}</h1>
    <p class="lede">${t('Go from a first draft to revision one layer at a time. Every method comes from a file in our open-source skills, with a template, an original example and the source link.', '从第一稿到改稿，一层层往下走。每篇方法都来自开源 skill 里的具体文件，附模板、原创示例和来源链接。')}</p>
    <form class="guide-search" data-guide-search hidden role="search">
      <label for="guide-query">${t('Search all guides', '搜索全部指南')}</label>
      <div class="guide-search-controls"><input type="search" id="guide-query" name="q" placeholder="${t('Try dialogue, outline, subtitles…','试试：大纲、对白、字幕……')}" autocomplete="off"><button type="reset">${t('Clear','清空')}</button></div>
      <p data-search-status role="status" aria-live="polite" data-count="${t('guides found','篇结果')}" data-related="${t('No exact match. {n} related guides:','没有完全匹配，下面是 {n} 篇相关文章：')}" data-empty="${t('No guides found. Try fewer words or browse a layer below.','没有匹配的文章。试试更短的关键词，或从下方按层浏览。')}"></p>
    </form>
    <noscript><p>${t('Browse the layers below to find a guide.', '从下方各层进入完整文章列表。')}</p></noscript>
    <nav class="layer-jump" data-library-browse aria-label="${t('Guide layers', '指南层次')}"><ol>${GUIDE_LAYERS.map(([id, en, zh], i) => `<li><a href="#${id}"><span class="layer-n">${String(i + 1).padStart(2, '0')}</span>${t(en, zh)}</a></li>`).join('')}</ol></nav>
  </div></header>
  <div class="wrap page-body wide">
    <section class="guide-layer" aria-labelledby="get-started" data-library-browse>
      ${layerHead(0, t('Choose where to write and make a first draft.', '选一个写作环境，写出第一稿。'))}
      ${skillList(layerPicks(starter))}
      <p class="layer-more">${topicMore(starter)}</p>
      <p class="path-direct"><a href="${appHref(LANG, '/register', 'org_guides_start')}">${t('No agent to install? Write in the browser workbench', '不装 Agent，在网页工作台里写')}${extGlyph}</a></p>
    </section>
    <section class="guide-layer" aria-labelledby="learning-paths" data-library-browse>
      ${layerHead(1, t('Pick by what you already have. Each step names the skill behind it, and each step page leads to the next.', '按你手上已有的材料选一条。每一步都标出背后的 skill，读完一步，页面会带你到下一步。'))}
      ${learningPaths()}
    </section>
    <section class="guide-layer" aria-labelledby="browse-topics" data-library-browse>
      ${layerHead(2, t('Stuck on something specific? Start from one of the creative tasks.', '遇到具体问题时，从下面的创作任务进入。'))}
      ${taskIndex()}
    </section>
    <section class="guide-layer" aria-labelledby="revise-and-look-up" data-library-browse>
      ${layerHead(3, t('Check the result, fix what drifted, and look up the terms.', '检查结果，修正跑偏的地方，查不懂的行话。'))}
      ${skillList(layerPicks(revision))}
      <p class="layer-more">${topicMore(revision)}</p>
      <div class="term-row"><p class="jump-label">${t('Look up a term', '查术语')}</p><ul>${glossary.map((g) => `<li><a href="/glossary/${g.slug}" lang="zh-CN">${esc(g.term)}</a></li>`).join('')}<li><a class="term-all" href="/glossary">${t('Glossary', '术语表')}${arrowGlyph}</a></li></ul></div>
    </section>
    <section data-search-results hidden aria-labelledby="search-results"><h2 id="search-results">${t('Search results','搜索结果')}</h2>
      <ul class="reading-list">${readingOf().map((item)=>{
        const topic=topics.find((candidate)=>candidate.slug===topicOf(item));const project=projects.find((candidate)=>candidate.slug===item.owner)
        const desc=item.description ? pick(item.description) : summary(pick(item.answer))
        const method=methodOf(item)
        return `<li data-search-title="${esc([pick(item.title),item.seo_title ? pick(item.seo_title) : ''].join(' '))}" data-search-body="${esc([desc,method ? [method.name,...method.files.map((file)=>file.label)].join(' ') : ''].join(' '))}" data-search-topic="${esc([pick(topic.title),project.name.en].join(' '))}">${guideLink(item)}<p>${esc(desc)}</p><span class="facts">${esc(pick(topic.title))} · ${esc(project.name.en)}</span></li>`
      }).join('')}${glossary.map((g)=>`<li data-search-title="${esc(`${g.term} ${g.bridge}`)}" data-search-body="${esc(pick(g.definition))}" data-search-topic="${esc(t('Glossary','术语表'))}"><a href="/glossary/${g.slug}"><span class="guide-title"${LANG === 'en' ? ' lang="zh-CN"' : ''}>${esc(g.term)}</span>${arrowGlyph}</a><p>${esc(summary(pick(g.definition)))}</p><span class="facts">${t('Glossary term','术语')} · ${esc(projects.find((p)=>p.slug===g.owner)?.name.en ?? '')}</span></li>`).join('')}</ul>
    </section>
  </div>
</article>`
  write(route,page({route,title,description,ogType:'website',ld,body}))
}

/**
 * A task's guides grouped by the skill behind them, when at least two skills hold three or more;
 * smaller ones share an "other methods" group. Shows which skill's knowhow a task draws on.
 */
const GUIDES_GROUP = '#guides'
const skillGroups = (items) => {
  // Step-by-step guides (guides.json) lead the task as their own group; craft articles group by skill.
  const crafts = items.filter((item) => item.skill)
  const counts = new Map()
  for (const item of crafts) counts.set(item.skill.name, (counts.get(item.skill.name) ?? 0) + 1)
  const major = [...counts].filter(([, count]) => count >= 3).sort((a, b) => b[1] - a[1]).map(([name]) => name)
  if (major.length < 2) return null
  const key = (item) => (!item.skill ? GUIDES_GROUP : major.includes(item.skill.name) ? item.skill.name : '')
  const order = [GUIDES_GROUP, ...major, '']
  return { order, key, total: new Map(order.map((name) => [name, items.filter((item) => key(item) === name).length])) }
}
const skillGroupHead = (name, count, sample) => `<h3 class="skill-group">${name === GUIDES_GROUP
  ? `<span class="skill-group-name">${t('Step-by-step guides', '操作指南')}</span>`
  : name
  ? `<span class="skill-group-name">${esc(skillLabels[name] ? pick(skillLabels[name]) : name)}</span><a class="skill-chip" href="${esc(methodOf(sample).url)}"><code>${esc(name)}</code></a>`
  : `<span class="skill-group-name">${t('Other methods', '其他方法')}</span>`}<span class="skill-group-count">${t(`${count} guides`, `${count} 篇`)}</span></h3>`
/** Compact rows: the title and the method behind it; the description lives on the guide itself. */
const compactList = (items, withSkill) => `<ul class="reading-list compact">${items.map((item) => `<li>${guideLink(item)}${withSkill ? methodFacts(item) : methodFiles(item)}</li>`).join('')}</ul>`
const methodFiles = (item) => {
  const method = methodOf(item)
  return method?.files.length ? `<p class="method-facts">${method.files.slice(0, 2).map((file) => esc(file.label)).join(dot)}</p>` : ''
}
const topicPage = (topic, number = 1) => {
  const route = topicRoute(topic, number)
  const items = topicReading(topic)
  const featured = topicFeatured(topic)
  const unfeatured = items.filter((item) => !featured.includes(item))
  const groups = skillGroups(unfeatured)
  // Grouped tasks page through one skill group after another (sort is stable within a group).
  const rest = groups ? [...unfeatured].sort((a, b) => groups.order.indexOf(groups.key(a)) - groups.order.indexOf(groups.key(b))) : unfeatured
  const ordered = [...featured, ...rest]
  const current = ordered.slice((number - 1) * TOPIC_PAGE_SIZE, number * TOPIC_PAGE_SIZE)
  const count = topicPageCount(topic, LANG)
  const pagination = count > 1 ? `<nav class="library-pagination" aria-label="${t('Guide pages','文章分页')}">${number > 1 ? `<a href="${topicRoute(topic, number - 1)}" rel="prev">${t('Previous','上一页')}</a>` : ''}<span>${t(`Page ${number} of ${count}`,`第 ${number} / ${count} 页`)}</span>${number < count ? `<a href="${topicRoute(topic, number + 1)}" rel="next">${t('Next','下一页')}${arrowGlyph}</a>` : ''}</nav>` : ''
  const title = `${pick(topic.title)}${number > 1 ? t(` — Page ${number}`, ` — 第 ${number} 页`) : ''} | ZenStory AI`
  const description = pick(topic.description)
  const ld = [orgNode, {'@type':'ItemList',name:pick(topic.title),url:U(route),itemListElement:current.map((item,i)=>({'@type':'ListItem',position:(number-1)*TOPIC_PAGE_SIZE+i+1,name:pick(item.title),url:U(`/${item.owner}/${esc(item.slug)}`)}))}, breadcrumb([['ZenStory AI',U('/')],[t('Guides','指南'),U('/guides')],[pick(topic.title),U(route)]])]
  const body = `<article class="guides-index"><header class="page-hero"><div class="wrap">
    <p class="crumbs"><a href="/guides">${t('All guides','全部指南')}</a></p>
    <h1>${esc(pick(topic.title))}${number > 1 ? t(`: page ${number}`, `：第 ${number} 页`) : ''}</h1><p class="lede">${esc(description)}</p>
    <nav class="terms jump" aria-label="${t('Other creative tasks','其他创作任务')}">${topics.filter((other)=>other.slug!==topic.slug && topicReading(other).length).map((other)=>`<a href="/guides/${other.slug}">${esc(pick(other.title))}</a>`).join(' ')}</nav>
  </div></header><div class="wrap page-body">
    ${number === 1 ? `<section aria-labelledby="topic-start"><h2 id="topic-start">${t('Start here','先看这几篇')}</h2>${libraryList(current.filter((item) => featured.includes(item)))}</section>` : ''}
    ${((more) => more.length ? `<section aria-labelledby="topic-more"><h2 id="topic-more">${t('More questions and methods','更多问题与方法')}</h2>${groups
      ? groups.order.filter((name) => more.some((item) => groups.key(item) === name)).map((name) => `${skillGroupHead(name, groups.total.get(name), more.find((item) => groups.key(item) === name))}${compactList(more.filter((item) => groups.key(item) === name), name === '' || name === GUIDES_GROUP)}`).join('')
      : compactList(more, true)}</section>` : '')(current.filter((item) => !featured.includes(item)))}
    ${pagination}
  </div></article>`
  const alternates = number <= Math.min(topicPageCount(topic, 'en'), topicPageCount(topic, 'zh')) ? alternatesOf(route) : null
  write(route,page({route,title,description,ogType:'website',ld,body,alternates,switchLinks:alternates ?? alternatesOf(topicRoute(topic))}))
}

// ---------- first-party comparisons ----------

const comparisonPage = (comparison) => {
  const route = `/compare/${comparison.slug}`
  const url = U(route)
  const optionProject = (option) => projects.find((project) => project.slug === option.project)
  const axisList = (field) => `<ul>${comparison.options.map((option) => {
    const project = optionProject(option)
    return `<li><b><a href="/${project.slug}">${toolName(project)}</a></b> ${rich(pick(option[field]))}</li>`
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
      <nav class="terms options" aria-label="${t('Compared projects', '比较的项目')}">${comparison.options.map((option) => { const project = optionProject(option); return `<a href="/${project.slug}">${toolName(project)}</a>` }).join(' ')}</nav>
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
    <p class="facts">${t(`Star counts as of ${esc(org.proof.as_of)}.`, `star 数统计于 ${esc(org.proof.as_of)}。`)}</p>
    </div>
  </section>
</article>`
  write(route, page({ route, title, description, ogType: 'website', ld, body }))
}

// ---------- glossary ----------

/** A term's main how-to article, when one is published in this language (the glossary defines; the article teaches). */
const termArticle = (g) => (g.how_to && routeExists(LANG, g.how_to) ? readingOf().find((item) => itemRoute(item) === g.how_to) : null)
const termHowTo = (g) => {
  const item = termArticle(g)
  return item ? `<p class="term-howto"><span class="method-label">${t('How to do it', '怎么做')}</span><a href="${g.how_to}"><span class="guide-title">${esc(pick(item.title))}</span>${arrowGlyph}</a></p>` : ''
}
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
  ${termHowTo(g)}

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
    <dd>${t(esc(g.definition.en.split('. ')[0]) + '.', esc(g.definition.zh.split(/(?<=。)/)[0]))}${termArticle(g) ? ` <a class="term-howto-link" href="${g.how_to}">${t('How to do it', '怎么做')}${arrowGlyph}</a>` : ''}${owner ? `<span class="owner">${esc(owner.name.en)}</span>` : ''}</dd>
    </div>`
  }).join('')}
  </dl>
  </div>
</article>
${roster()}`
  write(route, page({ route, title, description, ogType: 'website', ld, body }))
}

// ---------- run ----------

// Render every article body and guide FAQ answer once per language before writing anything, so bad
// markup or a link to a missing page fails the build with no partial output.
for (const lang of LANGS) {
  LANG = lang
  for (const g of guides) for (const item of g.faq ?? []) inline(item.a[lang])
  for (const a of articles.filter((candidate) => candidate.langs.includes(lang))) {
    for (const text of [a.answer, a.skill.text, ...a.sections.map((section) => section.body), ...(a.faq ?? []).map((item) => item.a)]) md(text[lang])
    for (const route of (a.related ?? []).filter((r) => routeExists(lang, r))) {
      assert.ok(route !== `/${a.owner}/${a.slug}` && routeTitle(route), `/${a.owner}/${a.slug} lists itself or an untitled page as related: ${route}`)
    }
  }
}

mkdirSync(join(outDir, 'org'), { recursive: true })
copyFileSync(join(here, 'org-pages.css'), join(outDir, 'org/org.css'))
for (const lang of LANGS) {
  LANG = lang
  homePage()
  projectsIndex()
  projects.forEach(projectPage)
  guides.forEach(guidePage)
  articles.filter((a) => a.langs.includes(LANG)).forEach(articlePage)
  guidesIndex()
  topics.forEach((topic) => { for (let number = 1; number <= topicPageCount(topic, LANG); number++) topicPage(topic, number) })
  comparisons.forEach(comparisonPage)
  glossaryIndex()
  glossary.forEach(termPage)
}

const routes = [...orgRoutes].map((route) => (route === '/' ? '/org-home' : route))
console.log(`org pages: wrote ${routes.length} routes × ${LANGS.length} languages + ${zhOnlyRoutes.size} Chinese-only to ${outDir}`)
