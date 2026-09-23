import assert from 'node:assert/strict'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { finalizeSite } from '../build-site-layout.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const scriptsDir = resolve(here, '..')
const webRoot = resolve(scriptsDir, '..')
const GENERATOR_FILES = ['build-org-pages.mjs', 'site-shell.mjs', 'org-pages.css']
const LANGS = ['en', 'zh']
const routeIn = (lang, route) => (lang === 'zh' ? (route === '/' ? '/zh' : `/zh${route}`) : route)
const urlIn = (lang, route) => `https://zenstory.ai${routeIn(lang, route)}`
const matches = (html, pattern) => [...html.matchAll(pattern)]
const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const graphOf = (html) => JSON.parse(matches(html, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)[0][1])['@graph']
const readPage = (outDir, path) => readFileSync(join(outDir, path, 'index.html'), 'utf8')

/** A scratch copy of the generator and content, so fixtures never touch the real content JSON. */
const scratch = (t, articles) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-articles-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'scripts'))
  cpSync(join(webRoot, 'content'), join(root, 'content'), { recursive: true })
  for (const file of GENERATOR_FILES) cpSync(join(scriptsDir, file), join(root, 'scripts', file))
  if (articles) writeFileSync(join(root, 'content/articles.json'), JSON.stringify(articles))
  return root
}
const build = (root) => {
  const out = join(root, 'output')
  return { out, result: spawnSync(process.execPath, [join(root, 'scripts/build-org-pages.mjs'), out], { cwd: root, encoding: 'utf8' }) }
}

const section = (id, zh, en) => ({ id, heading: { zh: `小节 ${id}`, ...(en ? { en: `Section ${id}` } : {}) }, body: { zh, ...(en ? { en } : {}) } })
const fixtureBilingual = {
  owner: 'oh-story', slug: 'fixture-chapter-hooks', langs: ['zh', 'en'],
  published_on: '2026-09-20', updated_on: '2026-09-23',
  title: { zh: '网文断章技巧：章尾钩子怎么留', en: 'Chapter-ending hooks for web serials' },
  seo_title: { zh: '网文断章技巧：章尾钩子怎么留', en: 'Chapter-ending hooks for web serials' },
  description: { zh: '章尾钩子的几种留法与取舍。', en: 'How to end a serial chapter so readers open the next one.' },
  answer: { zh: '章尾留一个**具体**的未完成动作。', en: 'End on one **specific** unfinished action.' },
  sections: [
    section('types', '| 类型 | 做法 |\n|---|---|\n| 危机 | 危险刚到 |\n| 悬念 | 问题刚问出 |\n\n- 第一条\n- 第二条\n\n- [ ] 可复制的检查项\n- [ ] 第二项\n\n### 小标题\n\n> 原创示例第一段。\n> 原创示例第二段。', '| Type | Move |\n|---|---|\n| Threat | Danger arrives |\n\n1. First\n2. Second'),
    section('links', '见[小说开头指南](/oh-story/novel-opening)，以及[纯中文页](/oh-story/fixture-zh-only)。', 'See the [opening guide](/oh-story/novel-opening).'),
  ],
  faq: [{ q: { zh: '每章都要留钩子吗？', en: 'Does every chapter need a hook?' }, a: { zh: '要有推进。', en: 'It needs movement.' } }],
  skill: { name: 'story-long-write', text: { zh: '用 `/story-long-write` 写下一章。', en: 'Use `/story-long-write` for the next chapter.' } },
  related: ['/oh-story/novel-opening', '/glossary/gouzi', '/oh-story/fixture-zh-only'],
}
const fixtureZhOnly = {
  owner: 'oh-story', slug: 'fixture-zh-only', langs: ['zh'],
  published_on: '2026-09-23', updated_on: '2026-09-23',
  title: { zh: '纯中文技法页' }, seo_title: { zh: '纯中文技法页' }, description: { zh: '只有中文版的技法页。' },
  answer: { zh: '先回答问题。' },
  sections: [section('one', '正文一。'), section('two', '正文二，链接到[双语页](/oh-story/fixture-chapter-hooks)。')],
  skill: { name: 'story-long-write', text: { zh: '用 skill 来做。' } },
  related: ['/oh-story/fixture-chapter-hooks'],
}

test('craft articles render free-form sections, one language per URL, and Chinese-only pages without a hreflang pair', (t) => {
  const root = scratch(t, [fixtureBilingual, fixtureZhOnly])
  const { out, result } = build(root)
  assert.equal(result.status, 0, result.stderr)
  const bilingualRoute = '/oh-story/fixture-chapter-hooks'
  const zhOnlyRoute = '/oh-story/fixture-zh-only'

  for (const lang of LANGS) {
    const html = readPage(out, routeIn(lang, bilingualRoute).slice(1))
    const a = fixtureBilingual
    assert.match(html, new RegExp(`<html lang="${lang === 'zh' ? 'zh-CN' : 'en'}" data-lang="${lang}">`))
    assert.ok(html.includes(`<title>${escape(a.seo_title[lang])} | ZenStory AI</title>`))
    assert.ok(html.includes(`<meta name="description" content="${escape(a.description[lang])}">`))
    assert.ok(html.includes(`<link rel="canonical" href="${urlIn(lang, bilingualRoute)}">`))
    for (const [hreflang, target] of [['en', 'en'], ['zh-CN', 'zh'], ['zh', 'zh'], ['x-default', 'en']]) {
      assert.ok(html.includes(`<link rel="alternate" hreflang="${hreflang}" href="${urlIn(target, bilingualRoute)}">`), `${lang}: missing hreflang ${hreflang}`)
    }
    assert.equal(matches(html, /<h1\b/g).length, 1)
    assert.ok(html.includes(`<h1>${escape(a.title[lang])}</h1>`))
    assert.ok(html.includes(lang === 'zh' ? '<strong>具体</strong>' : '<strong>specific</strong>'))
    for (const s of a.sections) assert.ok(html.includes(`<h2 id="${s.id}">${escape(s.heading[lang])}</h2>`))
    for (const id of ['types', 'links', 'faq', 'use-the-skill', 'related']) assert.ok(html.includes(`<li><a href="#${id}">`), `${lang}: contents lack #${id}`)
    assert.match(html, /<div class="table-wrap" role="region" tabindex="0" aria-label="[^"]+"><table><thead><tr><th scope="col">/)
    assert.ok(html.includes('href="https://github.com/zenstory-ai/oh-story-claudecode/tree/main/skills/story-long-write"'))
    const graph = graphOf(html)
    assert.deepEqual(graph.map((node) => node['@type']), ['Organization', 'Article', 'BreadcrumbList'])
    assert.equal(graph[1].url, urlIn(lang, bilingualRoute))
    assert.equal(graph[1].datePublished, a.published_on)
    assert.equal(graph[1].dateModified, a.updated_on)
    assert.equal(graph[1].headline, a.title[lang])
    // One date per page.
    assert.equal(html.split(a.updated_on).length - 1, 2, 'visible date appears once besides JSON-LD')
  }

  const zh = readPage(out, routeIn('zh', bilingualRoute).slice(1))
  assert.ok(zh.includes('<ul><li>第一条</li><li>第二条</li></ul>'))
  assert.ok(zh.includes('<h3>小标题</h3>'))
  assert.ok(zh.includes('<ul class="checklist"><li>可复制的检查项</li><li>第二项</li></ul>'))
  assert.ok(zh.includes('<blockquote><p>原创示例第一段。</p><p>原创示例第二段。</p></blockquote>'))
  assert.ok(zh.includes('href="/zh/oh-story/novel-opening"'), 'zh body links stay on /zh')
  assert.ok(zh.includes(`href="/zh${zhOnlyRoute}"`), 'zh page links the Chinese-only article')
  const en = readPage(out, bilingualRoute.slice(1))
  assert.ok(en.includes('<ol><li>First</li><li>Second</li></ol>'))
  assert.ok(!en.includes(zhOnlyRoute), 'English pages never link a Chinese-only article')

  // Chinese-only article: exists only under /zh, no hreflang pair, the language switch leads to the English project page.
  assert.equal(existsSync(join(out, zhOnlyRoute.slice(1), 'index.html')), false)
  const only = readPage(out, routeIn('zh', zhOnlyRoute).slice(1))
  assert.ok(only.includes(`<link rel="canonical" href="${urlIn('zh', zhOnlyRoute)}">`))
  assert.doesNotMatch(only, /<link rel="alternate" hreflang=/)
  assert.doesNotMatch(only, /og:locale:alternate/)
  assert.ok(only.includes('<a href="/oh-story" lang="en" hreflang="en">EN</a>'))
  assert.ok(only.includes(`href="/zh${bilingualRoute}"`))

  // Listed on the project page and the guides index of each language it exists in.
  for (const lang of LANGS) {
    for (const hub of ['/oh-story', '/guides']) {
      const html = readPage(out, routeIn(lang, hub).slice(1))
      assert.ok(html.includes(`href="${routeIn(lang, bilingualRoute)}"`), `${hub} (${lang}) lacks the bilingual article`)
      assert.equal(html.includes(`href="${routeIn(lang, zhOnlyRoute)}"`), lang === 'zh', `${hub} (${lang}) Chinese-only listing`)
    }
  }

  // Sitemap: the bilingual pair carries alternates; the Chinese-only entry stands alone.
  writeFileSync(join(out, 'index.html'), readFileSync(join(webRoot, 'index.html'), 'utf8'))
  finalizeSite(out)
  const siteMap = readFileSync(join(out, '_site/sitemap.xml'), 'utf8')
  const entry = (url) => matches(siteMap, new RegExp(`<url><loc>${url.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}</loc>(.*?)</url>`, 'g'))
  for (const lang of LANGS) {
    const [found] = entry(urlIn(lang, bilingualRoute))
    assert.ok(found?.[1].includes(`hreflang="zh" href="${urlIn('zh', bilingualRoute)}"`))
  }
  const [zhOnlyEntry] = entry(urlIn('zh', zhOnlyRoute))
  assert.equal(zhOnlyEntry?.[1], '')
  assert.equal(entry(urlIn('en', zhOnlyRoute)).length, 0)
})

test('published craft articles are listed in llms.txt and render in every language they declare', (t) => {
  const articles = JSON.parse(readFileSync(join(webRoot, 'content/articles.json'), 'utf8'))
  const directory = readFileSync(join(webRoot, 'public/llms.txt'), 'utf8')
  const root = scratch(t)
  const { out, result } = build(root)
  assert.equal(result.status, 0, result.stderr)
  for (const a of articles) {
    const route = `/${a.owner}/${a.slug}`
    assert.ok(directory.includes(`](${urlIn(a.langs.includes('en') ? 'en' : 'zh', route)})`), `llms.txt lacks ${route}`)
    for (const lang of LANGS) {
      assert.equal(existsSync(join(out, routeIn(lang, route).slice(1), 'index.html')), a.langs.includes(lang), `${route} (${lang})`)
    }
  }
})

test('invalid craft articles fail the build before any page is written', (t) => {
  const valid = { ...fixtureZhOnly, related: [] }
  const cases = [
    [{ ...valid, slug: '../escape' }, /Invalid article identity/],
    [{ ...valid, owner: 'unknown' }, /Invalid article identity/],
    [{ ...valid, slug: 'novel-opening' }, /Duplicate article route/],
    [{ ...valid, langs: ['en'] }, /Invalid article languages/],
    [{ ...valid, updated_on: '2026-09-01' }, /Invalid article dates/],
    [{ ...valid, seo_title: { zh: '这是一个明显超过三十个字符预算的非常非常长的页面标题会被搜索结果截断' } }, /seo_title\.zh exceeds 30/],
    [{ ...valid, sections: [section('one', '正文。'), section('faq', '保留 id。')] }, /Invalid article section ids/],
    [{ ...valid, related: ['/oh-story/does-not-exist'] }, /unknown related page/],
    [{ ...valid, sections: [section('one', '- 列表\n不是列表'), section('two', '正文。')] }, /Mixed list block/],
    [{ ...valid, sections: [section('one', '链接[不存在](/oh-story/missing)。'), section('two', '正文。')] }, /does not exist in zh/],
    [{ ...valid, sections: [section('one', '| a | b |\n|---|---|\n| 1 |'), section('two', '正文。')] }, /Malformed table|Ragged table/],
  ]
  for (const [article, pattern] of cases) {
    const root = scratch(t, [article])
    const { out, result } = build(root)
    assert.notEqual(result.status, 0, `accepted ${JSON.stringify(article).slice(0, 80)}`)
    assert.match(result.stderr, pattern)
    assert.equal(existsSync(out), false, 'no partial output')
  }
})
