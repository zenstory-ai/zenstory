import assert from 'node:assert/strict'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { finalizeSite } from '../build-site-layout.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const scriptsDir = resolve(here, '..')
const webRoot = resolve(scriptsDir, '..')
const projects = JSON.parse(readFileSync(join(webRoot, 'content/projects.json'), 'utf8'))
const glossary = JSON.parse(readFileSync(join(webRoot, 'content/glossary.json'), 'utf8'))
const org = JSON.parse(readFileSync(join(webRoot, 'content/org.json'), 'utf8'))

/** Files a copy of the organization generator needs. */
const GENERATOR_FILES = ['build-org-pages.mjs', 'site-shell.mjs', 'org-pages.css']
const LANGS = ['en', 'zh']
const LOCALE = { en: 'en', zh: 'zh-CN' }
const OG_LOCALE = { en: 'en_US', zh: 'zh_CN' }
/** Path of an English route on the `lang` site. */
const routeIn = (lang, route) => (lang === 'zh' ? (route === '/' ? '/zh' : `/zh${route}`) : route)
/** Output directory (relative to outDir) of an English route in `lang`. */
const outPath = (lang, route) => (lang === 'en' && route === '/' ? 'org-home' : routeIn(lang, route).slice(1))
const urlIn = (lang, route) => `https://zenstory.ai${routeIn(lang, route)}`
const other = (lang) => (lang === 'zh' ? 'en' : 'zh')

const run = (script, outDir) => {
  const result = spawnSync(process.execPath, [join(scriptsDir, script), outDir], {
    cwd: webRoot,
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, `${script} failed:\n${result.stdout}\n${result.stderr}`)
}

const readOutput = (outDir, route) => readFileSync(join(outDir, route, 'index.html'), 'utf8')
const matches = (html, pattern) => [...html.matchAll(pattern)]
const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const richText = (s) => escape(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2">$1</a>')
const graphOf = (html) => JSON.parse(matches(html, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)[0][1])['@graph']

/** Head invariants of one generated page: canonical, hreflang pair, Open Graph locale, language attributes. */
function assertHead(html, lang, route) {
  const url = urlIn(lang, route)
  assert.match(html, new RegExp(`^<!doctype html>\\n<html lang="${LOCALE[lang]}" data-lang="${lang}">`))
  assert.equal(matches(html, /<link\b[^>]*rel="canonical"[^>]*>/gi).length, 1)
  assert.ok(html.includes(`<link rel="canonical" href="${url}">`))
  assert.ok(html.includes(`<link rel="alternate" hreflang="en" href="${urlIn('en', route)}">`))
  assert.ok(html.includes(`<link rel="alternate" hreflang="zh-CN" href="${urlIn('zh', route)}">`))
  assert.ok(html.includes(`<link rel="alternate" hreflang="x-default" href="${urlIn('en', route)}">`))
  assert.equal(matches(html, /<meta\b[^>]*property="og:url"[^>]*>/gi).length, 1)
  assert.ok(html.includes(`<meta property="og:url" content="${url}">`))
  assert.ok(html.includes(`<meta property="og:locale" content="${OG_LOCALE[lang]}">`))
  assert.ok(html.includes(`<meta property="og:locale:alternate" content="${OG_LOCALE[other(lang)]}">`))
  assert.doesNotMatch(html, /<script\b[^>]*type="module"/i)
  assert.doesNotMatch(html, /localStorage|navigator\.language/)
  // The language switch links to the counterpart page.
  assert.ok(html.includes(`<a href="${routeIn('en', route)}" lang="en" hreflang="en"${lang === 'en' ? ' aria-current="true"' : ''}>EN</a>`))
  assert.ok(html.includes(`<a href="${routeIn('zh', route)}" lang="zh-CN" hreflang="zh-CN"${lang === 'zh' ? ' aria-current="true"' : ''}>中文</a>`))
  if (lang === 'zh') {
    // Every internal link on a Chinese page stays on the Chinese site, except single-URL pages
    // and the language switch, which is the one link to the English counterpart.
    const withoutSwitch = html.replace(/<div class="lang-switch"[\s\S]*?<\/div>/, '')
    for (const [, href] of matches(withoutSwitch, /href="(\/[^"]*)"/g)) {
      assert.ok(/^\/(?:zh(?:\/|$)|docs(?:\/|$)|privacy-policy|terms-of-service|llms\.txt|brand\/|org\/|favicon\.svg)/.test(href), `${route} (zh) links off the Chinese site: ${href}`)
    }
    assert.equal(matches(withoutSwitch.slice(withoutSwitch.indexOf('<body>')), / lang="zh-CN"/g).length, 0, `${route} (zh) marks fragments zh-CN inside a zh-CN document`)
  }
}

test('task guides render one language per URL with hreflang pairs, primary sources and canonical sitemap entries', (t) => {
  const guides = JSON.parse(readFileSync(join(webRoot, 'content/guides.json'), 'utf8'))
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-guides-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  run('build-org-pages.mjs', outDir)
  const routes = guides.map((guide) => `/${guide.owner}/${guide.slug}`)
  assert.deepEqual(routes, ['/novel-to-game/quick-start', '/video-recap/capcut-draft', '/oh-story/agent-skills-for-writers', '/dsh/deepseek-novel-writing', '/oh-story/import-and-continue', '/drama-skills/novel-to-short-drama', '/oh-story/revise-ai-prose', '/video-recap/video-to-narration', '/novel-to-game/meaningful-choices', '/drama-skills/character-consistency', '/video-recap/original-audio-and-narration', '/oh-story/long-novel-continuity', '/oh-story/outline-to-chapter', '/oh-story/preserve-author-voice', '/oh-story/review-and-revise', '/oh-story/short-story-from-idea', '/oh-story/character-dialogue', '/oh-story/learn-from-fiction', '/oh-story/character-motivation', '/drama-skills/script-to-storyboard', '/oh-story/novel-opening'])
  const directory = readFileSync(join(webRoot, 'public/llms.txt'), 'utf8')
  for (const [index, guide] of guides.entries()) {
    const route = routes[index]
    const owner = projects.find((project) => project.slug === guide.owner)
    assert.ok(owner)
    for (const field of ['prerequisites', 'steps', 'outputs', 'verification', 'sources']) {
      assert.equal(guide[field].en.length, guide[field].zh.length, `${route}: mismatched ${field} translations`)
    }
    const sourceGroups = ['en', 'zh'].map((lang) => matches(guide.sources[lang].join(' '), /\]\((https:\/\/[^)]+)\)/g).map(m => m[1]).sort())
    assert.deepEqual(sourceGroups[0], sourceGroups[1])
    assert.ok(sourceGroups[0].length >= 4)
    const officialSources = new Set([
      'https://agentskills.io/home',
      'https://code.claude.com/docs/en/skills',
      'https://code.claude.com/docs/en/plugins',
      'https://modelcontextprotocol.io/docs/getting-started/intro',
    ])
    assert.ok(sourceGroups[0].filter(source => source.startsWith('https://github.com/')).length >= 4)
    for (const source of sourceGroups[0]) {
      if (guide.slug === 'agent-skills-for-writers' && officialSources.has(source)) continue
      const parsed = new URL(source)
      assert.equal(parsed.origin, 'https://github.com')
      assert.match(parsed.pathname, /^\/zenstory-ai\/[^/]+\/blob\/[a-f0-9]{40}\/.+/)
      assert.equal(parsed.pathname.split('/').slice(0, 3).join('/'), new URL(owner.github).pathname)
      assert.match(parsed.hash, /^#L\d+(?:-L\d+)?$/)
    }
    assert.ok(directory.includes(`](https://zenstory.ai${route})`))

    for (const lang of LANGS) {
      const url = urlIn(lang, route)
      const html = readOutput(outDir, outPath(lang, route))
      assertHead(html, lang, route)
      const article = matches(html, /<article class="guide">([\s\S]*?)<\/article>/g)[0][1]
      assert.equal(matches(html, /<h1\b/g).length, 1)
      assert.ok(article.includes(`<h1>${escape(guide.title[lang])}</h1>`))
      assert.ok(!article.includes(escape(guide.title[other(lang)])), `${route} (${lang}) still carries the other language's title`)
      const contents = matches(article, /<nav class="guide-contents" aria-label="[^"]+">([\s\S]*?)<\/nav>/g)
      assert.equal(contents.length, 1, `${route}: missing guide navigation`)
      const targets = matches(contents[0][1], /href="#([^"]+)"/g).map(match => match[1])
      assert.deepEqual(targets, ['before-you-start', 'steps', `example-${lang}`, 'expected-files', 'verify-result', 'sources'])
      const ids = matches(article, /\sid="([^"]+)"/g).map(match => match[1])
      assert.equal(ids.length, new Set(ids).size, `${route}: duplicate fragment target`)
      for (const target of targets) {
        assert.ok(ids.includes(target), `${route}: missing #${target}`)
        assert.match(article, new RegExp(`<h[23] id="${target}">`))
      }
      assert.ok(article.includes(`<p>${richText(guide.answer[lang])}</p>`))
      assert.ok(!article.includes(richText(guide.answer[other(lang)])))
      for (const field of ['prerequisites', 'outputs', 'verification', 'sources']) {
        assert.ok(guide[field][lang].length > 1)
        for (const item of guide[field][lang]) assert.ok(article.includes(`<li>${richText(item)}</li>`))
        for (const item of guide[field][other(lang)]) assert.ok(!article.includes(`<li>${richText(item)}</li>`), `${route} (${lang}) renders ${other(lang)} ${field}`)
      }
      for (const [heading, text] of guide.steps[lang]) assert.ok(article.includes(`<li><b>${richText(heading)}</b> ${richText(text)}</li>`))
      const example = matches(article, /<section class="guide-example" aria-labelledby="example-(\w+)">([\s\S]*?)<\/section>/g)
      assert.equal(example.length, 1)
      assert.equal(example[0][1], lang)
      assert.deepEqual(matches(example[0][2], /<p>([\s\S]*?)<\/p>/g).map(match => match[1]), guide.example[lang].split(/\n{2,}/).map(escape))
      assert.doesNotMatch(example[0][2], /<(?:pre|code)\b/)
      assert.ok(article.includes(guide.checked_on))
      assert.ok(article.includes(`href="${routeIn(lang, `/${owner.slug}`)}"`))
      assert.ok(article.includes(`href="${owner.github}"`))
      assert.ok(html.includes('<meta property="og:type" content="article">'))
      const graph = graphOf(html)
      assert.deepEqual(graph.map(node => node['@type']), ['Organization', 'TechArticle', 'BreadcrumbList'])
      const doc = graph[1]
      assert.equal(doc.url, url)
      assert.equal(doc['@id'], `${url}#article`)
      assert.equal(doc.dateModified, guide.checked_on)
      assert.equal(doc.headline, guide.title[lang])
      assert.equal(doc.description, guide.answer[lang])
      assert.equal(doc.inLanguage, LOCALE[lang])
      assert.equal(doc.publisher['@id'], 'https://zenstory.ai/#org')
      assert.deepEqual(graph[2].itemListElement.map(item => item.item), [urlIn(lang, '/'), urlIn(lang, `/${owner.slug}`), url])
      const projectArticle = matches(readOutput(outDir, outPath(lang, `/${owner.slug}`)), /<article class="project">([\s\S]*?)<\/article>/g)[0][1]
      assert.ok(projectArticle.includes(`href="${routeIn(lang, route)}"`))
      assert.ok(readOutput(outDir, outPath(lang, '/')).includes(`href="${routeIn(lang, route)}"`))
    }
  }
  assert.match(readOutput(outDir, 'oh-story/novel-opening'), /three complete chapters/)
  assert.match(readOutput(outDir, 'oh-story/novel-opening'), /not a fixed retention law/)
  assert.match(readOutput(outDir, 'oh-story/novel-opening'), /six intact bowls/)
  assert.match(readOutput(outDir, 'zh/oh-story/novel-opening'), /失去最佳摊位|失去最好的摊位|失去前排摊位/)
  assert.ok(readOutput(outDir, 'glossary/huangjinsanzhang').includes('href="https://zenstory.ai/oh-story/novel-opening"'))
  assert.match(readOutput(outDir, 'novel-to-game/quick-start'), /PRODUCT_BRIEF\.md/)
  assert.match(readOutput(outDir, 'novel-to-game/quick-start'), /qa\/verification\.json/)
  assert.match(readOutput(outDir, 'video-recap/capcut-draft'), /export_jianying\.py/)
  assert.match(readOutput(outDir, 'video-recap/capcut-draft'), /--out-dir/)
  assert.match(readOutput(outDir, 'video-recap/capcut-draft'), /draft_content\.json/)
  writeFileSync(join(outDir, 'index.html'), readFileSync(join(webRoot, 'index.html'), 'utf8'))
  finalizeSite(outDir)
  const siteMap = readFileSync(join(outDir, '_site/sitemap.xml'), 'utf8')
  for (const route of routes) {
    for (const lang of LANGS) {
      const entry = matches(siteMap, new RegExp(`<url><loc>${urlIn(lang, route).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}</loc>(.*?)</url>`, 'g'))
      assert.equal(entry.length, 1, `${route} (${lang}) must appear once in the sitemap`)
      assert.ok(entry[0][1].includes(`<xhtml:link rel="alternate" hreflang="en" href="${urlIn('en', route)}"/>`))
      assert.ok(entry[0][1].includes(`<xhtml:link rel="alternate" hreflang="zh-CN" href="${urlIn('zh', route)}"/>`))
      assert.ok(entry[0][1].includes(`<xhtml:link rel="alternate" hreflang="x-default" href="${urlIn('en', route)}"/>`))
    }
    assert.ok(!readFileSync(join(outDir, '_app/sitemap.xml'), 'utf8').includes(route))
  }
})

test('guide examples keep literal markup, line breaks and indentation as escaped prose', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-guide-prose-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'scripts'))
  cpSync(join(webRoot, 'content'), join(root, 'content'), { recursive: true })
  for (const file of GENERATOR_FILES) cpSync(join(scriptsDir, file), join(root, 'scripts', file))
  const [guide] = JSON.parse(readFileSync(join(root, 'content/guides.json'), 'utf8'))
  guide.example = {
    en: 'A literal <script>alert("x")</script> & [link](https://example.com).\n\n/skill --flag "quoted"\n  keep indentation\n`code stays literal`',
    zh: '原文 <img src=x onerror="alert(1)"> 与 & 符号。\n\n第二段\n  保留缩进',
  }
  writeFileSync(join(root, 'content/guides.json'), JSON.stringify([guide]))
  const out = join(root, 'output')
  const result = spawnSync(process.execPath, [join(root, 'scripts/build-org-pages.mjs'), out], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  const examples = LANGS.map((lang) => {
    const html = readOutput(out, outPath(lang, `/${guide.owner}/${guide.slug}`))
    const found = matches(html, /<section class="guide-example"[^>]*>([\s\S]*?)<\/section>/g)
    assert.equal(found.length, 1)
    return found[0]
  })
  assert.ok(examples[0][1].includes('<p>A literal &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; [link](https://example.com).</p>'))
  assert.ok(examples[0][1].includes('<p>/skill --flag &quot;quoted&quot;\n  keep indentation\n`code stays literal`</p>'))
  assert.ok(examples[1][1].includes('<p>原文 &lt;img src=x onerror=&quot;alert(1)&quot;&gt; 与 &amp; 符号。</p>'))
  assert.ok(examples[1][1].includes('<p>第二段\n  保留缩进</p>'))
  for (const example of examples) assert.doesNotMatch(example[1], /<(?:script|img|a|code)\b/)
})

test('guide identities reject unknown owners, unsafe paths and duplicate routes before writing', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-invalid-guides-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'scripts'))
  cpSync(join(webRoot, 'content'), join(root, 'content'), { recursive: true })
  for (const file of GENERATOR_FILES) cpSync(join(scriptsDir, file), join(root, 'scripts', file))
  const valid = JSON.parse(readFileSync(join(root, 'content/guides.json'), 'utf8'))
  for (const invalid of [[{ ...valid[0], owner: 'unknown' }], [{ ...valid[0], slug: '../escape' }], [valid[0], valid[0]]]) {
    writeFileSync(join(root, 'content/guides.json'), JSON.stringify(invalid))
    const out = join(root, 'output')
    const result = spawnSync(process.execPath, [join(root, 'scripts/build-org-pages.mjs'), out], { encoding: 'utf8' })
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /Invalid guide identity|Duplicate guide route/)
    assert.equal(existsSync(out), false)
  }
})

test('AI-facing project directory links the source-backed canonical pages and separates guides from workbench docs', () => {
  const directory = readFileSync(join(webRoot, 'public/llms.txt'), 'utf8')
  for (const project of projects) {
    assert.ok(directory.includes(`](https://zenstory.ai/${project.slug})`), `directory lacks /${project.slug}`)
    assert.ok(directory.includes(`](https://github.com/zenstory-ai/${project.repo})`))
  }
  assert.match(directory, /versioned source notes/)
  assert.match(directory, /five stage skills plus one orchestrator/)
  assert.doesNotMatch(directory, /fully playable game|Clip any video|six independent skills/i)
  const sections = matches(directory, /^## (.+)$/gm).map((m) => m[1])
  assert.deepEqual(sections, ['Projects', 'Guides', 'Workbench docs', 'Optional'])
  const between = (a, b) => directory.slice(directory.indexOf(`## ${a}`), b ? directory.indexOf(`## ${b}`) : undefined)
  assert.doesNotMatch(between('Guides', 'Workbench docs'), /zenstory\.ai\/docs\//)
  assert.equal(matches(between('Workbench docs', 'Optional'), /\]\(https:\/\/zenstory\.ai\/docs\//g).length, 10)
  assert.match(between('Guides', 'Workbench docs'), /https:\/\/zenstory\.ai\/zh\//)
})

test('project pages render each language with dated, owned immutable sources', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-project-sources-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  run('build-org-pages.mjs', outDir)

  for (const project of projects) {
    assert.ok(project.sources, `${project.slug} lacks source evidence`)
    assert.match(project.sources.checked_on, /^\d{4}-\d{2}-\d{2}$/)
    const sourceGroups = []
    for (const lang of LANGS) {
      const route = `/${project.slug}`
      const html = readOutput(outDir, outPath(lang, route))
      assertHead(html, lang, route)
      const article = matches(html, /<article class="project">([\s\S]*?)<\/article>/g)[0][1]
      assert.ok(article.includes(project.sources.checked_on), 'source-check date not rendered')
      assert.match(article, lang === 'en' ? /Source notes/ : /来源与边界/)
      const sources = project.sources[lang]
      assert.ok(sources.length >= 2, `${project.slug} lacks ${lang} source notes`)
      const urls = []
      for (const note of sources) {
        assert.ok(article.includes(`<li>${richText(note)}</li>`), `missing ${lang} source note`)
        const links = matches(note, /\]\((https:\/\/[^)]+)\)/g).map((match) => match[1])
        assert.ok(links.length > 0, 'source note lacks a link')
        for (const source of links) {
          const parsed = new URL(source)
          assert.equal(parsed.origin, 'https://github.com')
          assert.match(parsed.pathname, /^\/zenstory-ai\/[^/]+\/blob\/[a-f0-9]{40}\/.+/)
          assert.equal(parsed.pathname.split('/').slice(0, 3).join('/'), new URL(project.github).pathname)
          assert.match(parsed.hash, /^#L\d+(?:-L\d+)?$/)
          assert.ok(article.includes(`href="${source}"`), `source not rendered: ${source}`)
          urls.push(source)
        }
      }
      sourceGroups.push(urls.sort())
      assert.ok(article.includes(`<p>${richText(project.definition[lang])}</p>`))
      assert.ok(!article.includes(`<p>${richText(project.definition[other(lang)])}</p>`))
      const software = graphOf(html).find((node) => node.codeRepository === project.github)
      assert.equal(software.description, project.definition[lang])
      assert.equal(software.url, urlIn(lang, route))
      assert.equal(software.inLanguage, LOCALE[lang])
    }
    assert.deepEqual(sourceGroups[0], sourceGroups[1], `${project.slug} cites different EN/ZH sources`)
  }
})

test('glossary terms have pages in each language, valid relationships and traceable definitions', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-glossary-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  run('build-org-pages.mjs', outDir)

  const slugs = new Set(glossary.map((term) => term.slug))
  assert.equal(slugs.size, glossary.length, 'duplicate glossary slug')
  for (const slug of ['gouzi', 'taolu', 'fenji-ditu', 'daoyan-chanshu', 'dongjie-guanjianzhen']) {
    assert.ok(slugs.has(slug), `missing new term ${slug}`)
  }

  for (const term of glossary) {
    const owner = projects.find((project) => project.slug === term.owner)
    assert.ok(owner, `unknown owner of ${term.slug}`)
    for (const related of term.related) assert.ok(slugs.has(related), `broken related term ${related}`)
    for (const section of [term.definition, term.in_practice]) {
      for (const language of ['en', 'zh']) assert.ok(section[language].trim(), `${term.slug} lacks ${language}`)
    }
    const sourceGroups = []
    for (const lang of LANGS) {
      const route = `/glossary/${term.slug}`
      const html = readOutput(outDir, outPath(lang, route))
      assertHead(html, lang, route)
      const termHtml = matches(html, /<article class="term">([\s\S]*?)<\/article>/g)[0][1]
      assert.ok(termHtml.includes(`href="${routeIn(lang, `/${owner.slug}`)}"`), `missing owner link for ${term.slug}`)
      for (const related of term.related) assert.ok(termHtml.includes(`href="${routeIn(lang, `/glossary/${related}`)}"`), `missing related link to ${related}`)
      assert.equal(matches(html, /<h1\b/g).length, 1)
      // The term itself is Chinese: marked zh-CN on the English page, plain inside the zh-CN document.
      assert.ok(html.includes(lang === 'en' ? `<h1 lang="zh-CN">${term.term}</h1>` : `<h1>${term.term}</h1>`))
      assert.ok(termHtml.includes(`<p>${richText(term.definition[lang])}</p>`), `missing ${lang} definition`)
      assert.ok(!termHtml.includes(`<p>${richText(term.definition[other(lang)])}</p>`))
      const definition = graphOf(html).find((node) => node['@type'] === 'DefinedTerm')
      assert.equal(definition.name, term.term)
      assert.equal(definition.url, urlIn(lang, route))
      assert.equal(definition.description, term.definition[lang])
      const links = matches(term.in_practice[lang], /\]\((https:\/\/[^)]+)\)/g).map((match) => match[1])
      const guideLinks = links.filter((link) => link.startsWith('https://zenstory.ai/'))
      assert.deepEqual(guideLinks, term.slug === 'huangjinsanzhang' ? ['https://zenstory.ai/oh-story/novel-opening'] : [])
      const sources = links.filter((link) => !guideLinks.includes(link))
      assert.ok(sources.length > 0, `${term.slug} lacks ${lang} source references`)
      sourceGroups.push(sources.sort())
      for (const source of sources) {
        const parsed = new URL(source)
        assert.equal(parsed.origin, 'https://github.com')
        assert.match(parsed.pathname, /^\/zenstory-ai\/[^/]+\/blob\/[a-f0-9]{40}\/.+/)
        assert.equal(parsed.pathname.split('/').slice(0, 3).join('/'), new URL(owner.github).pathname)
        assert.match(parsed.hash, /^#L\d+(?:-L\d+)?$/)
        assert.ok(termHtml.includes(`href="${source}"`), `source not rendered: ${source}`)
      }
    }
    assert.deepEqual(sourceGroups[0], sourceGroups[1], `${term.slug} cites different EN/ZH sources`)
  }
})

test('organization generator writes every route in both languages, an apex homepage and a Chinese homepage', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-org-pages-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))

  run('build-org-pages.mjs', outDir)

  for (const route of ['projects', ...projects.map((project) => project.slug), 'guides', 'glossary', ...glossary.map((term) => `glossary/${term.slug}`)]) {
    assert.match(readOutput(outDir, route), /<!doctype html>/i, `missing complete HTML for /${route}`)
    assert.match(readOutput(outDir, `zh/${route}`), /<!doctype html>/i, `missing complete HTML for /zh/${route}`)
  }

  const workbench = readOutput(outDir, 'workbench')
  assert.match(workbench, /as a service at app\.zenstory\.ai/)
  assert.doesNotMatch(workbench, /也提供 app\.zenstory\.ai 的在线服务/)
  assert.match(readOutput(outDir, 'zh/workbench'), /也提供 app\.zenstory\.ai 的在线服务/)
  assert.doesNotMatch(workbench, /as a service at zenstory\.ai|也提供 zenstory\.ai 的在线服务/)
  // The workbench docs are reachable from the workbench project page and the footer, not the site navigation.
  assert.match(workbench, /<a class="btn ghost" href="\/docs">Workbench docs/)
  assert.match(workbench, /<li><a href="\/docs">Workbench docs<\/a><\/li>/)
  assert.match(readOutput(outDir, 'zh/workbench'), /<a class="btn ghost" href="\/docs">工作台文档/)

  for (const [lang, file] of [['en', 'org-home'], ['zh', 'zh']]) {
    const homepage = readOutput(outDir, file)
    assertHead(homepage, lang, '/')
    assert.match(homepage, /<h1>ZenStory AI <span class="headline">/)
    assert.match(homepage, lang === 'en' ? /turns stories into many forms/ : /让故事走向更多形态/)
    assert.match(homepage, /<p class="lede">[^<]*(?:story|open-source|故事|创作)/i)
    assert.match(homepage, /href="https:\/\/app\.zenstory\.ai">(?:Open the web workbench|打开网页工作台)/)
    assert.match(homepage, lang === 'en' ? /choose by task/i : /按任务选择/)
    assert.match(homepage, lang === 'en' ? /Source on GitHub/ : /GitHub 源码/)
    assert.equal(matches(homepage, /<article class="project-card">/g).length, 6)
    const siteNav = matches(homepage, /<nav aria-label="(?:Site|站点)">([\s\S]*?)<\/nav>/g)[0][1]
    assert.deepEqual(matches(siteNav, /href="([^"]+)"/g).map((m) => m[1]), [routeIn(lang, '/projects'), routeIn(lang, '/guides'), routeIn(lang, '/glossary'), org.github])
    for (const project of projects) {
      assert.match(homepage, new RegExp(`href="${routeIn(lang, `/${project.slug}`)}"`), `homepage should link /${project.slug}`)
      assert.match(homepage, new RegExp(`href="${project.github.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`), `homepage should link ${project.github}`)
    }
    const graph = graphOf(homepage)
    assert.ok(graph.some((node) => node['@type'] === 'Organization' && node.url === 'https://zenstory.ai'))
    assert.ok(graph.some((node) => node['@type'] === 'WebSite' && node.url === 'https://zenstory.ai' && node.inLanguage === LOCALE[lang]))
    assert.ok(!graph.some((node) => node['@type'] === 'SoftwareApplication'))
  }

  for (const route of ['projects', ...projects.map((project) => project.slug)]) {
    const html = readOutput(outDir, route)
    if (/\bGitHub stars\b|\d[\d,]* ★/.test(html)) assert.match(html, new RegExp(org.proof.as_of))
  }
})

test('docs pages sit on the organization shell with resolved links and their own metadata', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-doc-pages-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))

  run('build-docs-pages.mjs', outDir)

  for (const route of ['docs', 'docs/getting-started/quick-start']) {
    const html = readOutput(outDir, route)
    const canonical = `https://zenstory.ai/${route}`
    assert.match(html, /^<!doctype html>\n<html lang="zh-CN" data-lang="zh">/)
    assert.equal(matches(html, /<link\b(?=[^>]*\brel=["']canonical["'])[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<link rel="canonical" href="${canonical}">`))
    assert.doesNotMatch(html, /hreflang="x-default"/, 'single-URL docs must not claim a language counterpart')
    assert.equal(matches(html, /<meta\b(?=[^>]*\bproperty=["']og:url["'])[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<meta property="og:url" content="${canonical}">`))
    assert.equal(matches(html, /<script\b(?=[^>]*\btype=["']application\/ld\+json["'])[^>]*>[\s\S]*?<\/script>/gi).length, 1)
    assert.doesNotMatch(html, /<script\b[^>]*type="module"/i, 'docs pages must not load the app')
    assert.match(html, /<header class="top">/)
    assert.match(html, /<footer class="bottom">/)
    assert.match(html, /<nav class="docs-side"/)
    assert.doesNotMatch(html, /href="[^"]*\.md[^"]*"/, 'markdown links must resolve to site routes')
    assert.match(html, /<section class="prose" id="zh" lang="zh-CN">/)
    assert.match(html, /<section class="prose" id="en" lang="en">/)
    assert.match(html, /<a href="#en" lang="en" hreflang="en">EN<\/a>/)
    assert.match(html, /<a href="#zh" lang="zh-CN" hreflang="zh-CN" aria-current="true">中文<\/a>/)
    assert.ok(html.includes('href="https://app.zenstory.ai/dashboard"'), 'Docs need a direct workbench entrypoint in their body')
    assert.ok(html.includes('href="/zh/workbench"'), 'Docs link back to the workbench project page')
    const head = html.slice(0, html.indexOf('</head>'))
    assert.doesNotMatch(head, /SoftwareApplication|https:\/\/app\.zenstory\.ai|stale\.example/)
    assert.match(head, /<title>[^<]*\| ZenStory Workbench<\/title>/)
    assert.doesNotMatch(head, /zenstory 文档|zenstory帮助文档/)
    const graph = graphOf(html)
    assert.deepEqual(graph.map((node) => node['@type']), ['Organization', 'WebSite', 'TechArticle'])
    assert.equal(graph[2].url, canonical)
  }
  const index = readOutput(outDir, 'docs')
  assert.match(index, /<h1>ZenStory 工作台帮助文档<\/h1>/)
  assert.ok(index.includes('href="/docs/reference/faq"'))
  assert.ok(index.includes('href="/docs/getting-started/installation"'))
  const quickStart = readOutput(outDir, 'docs/getting-started/quick-start')
  assert.ok(quickStart.includes('<li><a href="/docs/getting-started/quick-start" aria-current="page">'))
  // Section links open the section's first page, as the app does.
  assert.ok(readOutput(outDir, 'docs/reference/faq').includes('href="/docs/user-guide/interface-overview"'))
})

test('account documentation keeps app entrypoints and source limits', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-account-doc-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  run('build-docs-pages.mjs', outDir)
  const html = readOutput(outDir, 'docs/getting-started/installation')
  for (const route of ['register', 'login', 'forgot-password']) {
    assert.ok(html.includes(`href="https://app.zenstory.ai/${route}"`), `Missing direct app ${route} entry`)
  }
  assert.equal(html.split('https://app.zenstory.ai/register?invite=ABCD-1234').length - 1, 2)
  assert.doesNotMatch(html, /https:\/\/zenstory\.ai\/(?:register|login|forgot-password)/)
  assert.equal(html.split('2026-09-12').length - 1, 2)
  assert.match(html, /源码核对/)
  assert.match(html, /Source review/)
  assert.match(html, /不是线上账号验收/)
  assert.match(html, /not a live-account acceptance test/)
  assert.match(html, /没有网页自助发送密码重置链接的流程/)
  assert.match(html, /no web self-service reset-link flow/)
  assert.ok(html.includes('href="https://zenstory.ai/docs/getting-started/installation"'))
  const sources = matches(html, /href="(https:\/\/github\.com\/zenstory-ai\/zenstory\/blob\/[a-f0-9]{40}\/[^"#]+#L\d+(?:-L\d+)?)"/g).map(match => match[1])
  assert.equal(sources.length, 24, 'Both languages need all twelve checked-source references')
  const midpoint = sources.length / 2
  assert.deepEqual(sources.slice(0, midpoint), sources.slice(midpoint))
  for (const source of sources) assert.ok(source.includes('/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/'))
})

test('docs generator fails closed on a link to a page that does not exist', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-broken-doc-link-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'scripts'))
  mkdirSync(join(root, 'src/data'), { recursive: true })
  cpSync(join(webRoot, 'content'), join(root, 'content'), { recursive: true })
  cpSync(join(webRoot, 'docs'), join(root, 'docs'), { recursive: true })
  cpSync(join(webRoot, 'src/data/docsNavigation.ts'), join(root, 'src/data/docsNavigation.ts'))
  symlinkSync(join(webRoot, 'node_modules'), join(root, 'node_modules'), 'dir')
  for (const file of ['build-docs-pages.mjs', 'site-shell.mjs']) cpSync(join(scriptsDir, file), join(root, 'scripts', file))
  writeFileSync(join(root, 'docs/reference/faq.md'), readFileSync(join(root, 'docs/reference/faq.md'), 'utf8') + '\n\n[missing](../user-guide/does-not-exist.md)\n')
  const out = join(root, 'output')
  const result = spawnSync(process.execPath, [join(root, 'scripts/build-docs-pages.mjs'), out], { encoding: 'utf8' })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /does-not-exist, which is not a docs page/)
})
