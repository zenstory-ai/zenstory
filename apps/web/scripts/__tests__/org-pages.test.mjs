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
const projects = JSON.parse(readFileSync(join(webRoot, 'content/projects.json'), 'utf8'))
const glossary = JSON.parse(readFileSync(join(webRoot, 'content/glossary.json'), 'utf8'))
const org = JSON.parse(readFileSync(join(webRoot, 'content/org.json'), 'utf8'))

const run = (script, outDir) => {
  const result = spawnSync(process.execPath, [join(scriptsDir, script), outDir], {
    cwd: webRoot,
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, `${script} failed:\n${result.stdout}\n${result.stderr}`)
}

const readOutput = (outDir, route) => readFileSync(join(outDir, route, 'index.html'), 'utf8')
const matches = (html, pattern) => [...html.matchAll(pattern)]

test('task guides provide bilingual instructions, primary sources and canonical sitemap entries', (t) => {
  const guides = JSON.parse(readFileSync(join(webRoot, 'content/guides.json'), 'utf8'))
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-guides-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  run('build-org-pages.mjs', outDir)
  const routes = guides.map((guide) => `/${guide.owner}/${guide.slug}`)
  assert.deepEqual(routes, ['/novel-to-game/quick-start', '/video-recap/capcut-draft', '/oh-story/agent-skills-for-writers', '/dsh/deepseek-novel-writing', '/oh-story/import-and-continue', '/drama-skills/novel-to-short-drama', '/oh-story/revise-ai-prose', '/video-recap/video-to-narration', '/novel-to-game/meaningful-choices', '/drama-skills/character-consistency', '/video-recap/original-audio-and-narration'])
  const directory = readFileSync(join(webRoot, 'public/llms.txt'), 'utf8')
  const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
  const richText = (s) => escape(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2">$1</a>')
  for (const [index, guide] of guides.entries()) {
    const route = routes[index]
    const url = `https://zenstory.ai${route}`
    const owner = projects.find((project) => project.slug === guide.owner)
    assert.ok(owner)
    const html = readOutput(outDir, route)
    const article = matches(html, /<article class="guide">([\s\S]*?)<\/article>/g)[0][1]
    assert.equal(matches(html, /<h1\b/g).length, 1)
    assert.ok(article.includes(`<h1>${escape(guide.title.en)}</h1>`))
    assert.ok(article.includes(`<p class="lede" lang="zh-CN">${escape(guide.title.zh)}</p>`))
    for (const field of ['prerequisites', 'steps', 'outputs', 'verification', 'sources']) {
      assert.equal(guide[field].en.length, guide[field].zh.length, `${route}: mismatched ${field} translations`)
    }
    for (const lang of ['en', 'zh']) {
      const attr = lang === 'zh' ? ' lang="zh-CN"' : ''
      assert.ok(article.includes(`<p${attr}>${richText(guide.answer[lang])}</p>`))
      for (const field of ['prerequisites', 'outputs', 'verification', 'sources']) {
        assert.ok(guide[field][lang].length > 1)
        for (const item of guide[field][lang]) assert.ok(article.includes(`<li${attr}>${richText(item)}</li>`))
      }
      for (const [heading, text] of guide.steps[lang]) assert.ok(article.includes(`<li${attr}><b>${richText(heading)}</b> ${richText(text)}</li>`))
      assert.ok(article.includes(`<pre${attr}><code>${escape(guide.example[lang])}</code></pre>`))
    }
    assert.ok(article.includes(guide.checked_on))
    assert.ok(article.includes(`href="/${owner.slug}"`))
    assert.ok(article.includes(`href="${owner.github}"`))
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
    assert.equal(matches(html, /<link\b[^>]*rel="canonical"[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<link rel="canonical" href="${url}"`))
    assert.equal(matches(html, /<meta\b[^>]*property="og:url"[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<meta property="og:url" content="${url}"`))
    assert.equal(matches(html, /<meta\b[^>]*property="og:type"[^>]*>/gi).length, 1)
    assert.ok(html.includes('<meta property="og:type" content="article">'))
    const graph = JSON.parse(matches(html, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)[0][1])['@graph']
    assert.deepEqual(graph.map(node => node['@type']), ['Organization', 'TechArticle', 'BreadcrumbList'])
    const doc = graph[1]
    assert.equal(doc.url, url)
    assert.equal(doc['@id'], `${url}#article`)
    assert.equal(doc.dateModified, guide.checked_on)
    assert.equal(doc.headline, guide.title.en)
    assert.equal(doc.description, guide.answer.en)
    assert.deepEqual(doc.inLanguage, ['en', 'zh-CN'])
    assert.equal(doc.publisher['@id'], 'https://zenstory.ai/#org')
    assert.deepEqual(graph[2].itemListElement.map(item => item.item), ['https://zenstory.ai', `https://zenstory.ai/${owner.slug}`, url])
    assert.doesNotMatch(html, /<script\b[^>]*type="module"/i)
    const projectArticle = matches(readOutput(outDir, owner.slug), /<article class="project">([\s\S]*?)<\/article>/g)[0][1]
    assert.ok(projectArticle.includes(`href="${route}"`))
    assert.ok(directory.includes(`](https://zenstory.ai${route})`))
    assert.ok(readOutput(outDir, 'org-home').includes(`href="${route}"`))
  }
  assert.match(readOutput(outDir, 'novel-to-game/quick-start'), /PRODUCT_BRIEF\.md/)
  assert.match(readOutput(outDir, 'novel-to-game/quick-start'), /qa\/verification\.json/)
  assert.match(readOutput(outDir, 'video-recap/capcut-draft'), /export_jianying\.py/)
  assert.match(readOutput(outDir, 'video-recap/capcut-draft'), /--out-dir/)
  assert.match(readOutput(outDir, 'video-recap/capcut-draft'), /draft_content\.json/)
  writeFileSync(join(outDir, 'index.html'), readFileSync(join(webRoot, 'index.html'), 'utf8'))
  finalizeSite(outDir)
  for (const route of routes) {
    assert.equal(readFileSync(join(outDir, '_site/sitemap.xml'), 'utf8').split(`<loc>https://zenstory.ai${route}</loc>`).length - 1, 1)
    assert.ok(!readFileSync(join(outDir, '_app/sitemap.xml'), 'utf8').includes(route))
  }
})

test('guide identities reject unknown owners, unsafe paths and duplicate routes before writing', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-invalid-guides-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'scripts'))
  cpSync(join(webRoot, 'content'), join(root, 'content'), { recursive: true })
  for (const file of ['build-org-pages.mjs', 'org-pages.css']) cpSync(join(scriptsDir, file), join(root, 'scripts', file))
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

test('AI-facing project directory links the source-backed canonical pages', () => {
  const directory = readFileSync(join(webRoot, 'public/llms.txt'), 'utf8')
  for (const project of projects) {
    assert.ok(directory.includes(`](https://zenstory.ai/${project.slug})`), `directory lacks /${project.slug}`)
    assert.ok(directory.includes(`](https://github.com/zenstory-ai/${project.repo})`))
  }
  assert.match(directory, /versioned source notes/)
  assert.match(directory, /five stage skills plus one orchestrator/)
  assert.doesNotMatch(directory, /fully playable game|Clip any video|six independent skills/i)
})

test('project pages render bilingual capabilities and dated, owned immutable sources', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-project-sources-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  run('build-org-pages.mjs', outDir)

  for (const project of projects) {
    assert.ok(project.sources, `${project.slug} lacks source evidence`)
    assert.match(project.sources.checked_on, /^\d{4}-\d{2}-\d{2}$/)
    const html = readOutput(outDir, project.slug)
    const article = matches(html, /<article class="project">([\s\S]*?)<\/article>/g)[0][1]
    assert.ok(article.includes(project.sources.checked_on), 'source-check date not rendered')
    assert.match(article, /Source notes/)
    assert.match(article, /来源与边界/)
    const sourceGroups = []
    for (const language of ['en', 'zh']) {
      const sources = project.sources[language]
      assert.ok(sources.length >= 2, `${project.slug} lacks ${language} source notes`)
      const urls = []
      for (const note of sources) {
        const noteHtml = note.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
          .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2">$1</a>')
        assert.ok(article.includes(`<li${language === 'zh' ? ' lang="zh-CN"' : ''}>${noteHtml}</li>`), `missing ${language} source note`)
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
      const definition = project.definition[language].replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/`([^`]+)`/g, '<code>$1</code>')
      assert.ok(article.includes(`<p${language === 'zh' ? ' lang="zh-CN"' : ''}>${definition}</p>`))
    }
    assert.deepEqual(sourceGroups[0], sourceGroups[1], `${project.slug} cites different EN/ZH sources`)
    const canonical = `https://zenstory.ai/${project.slug}`
    assert.equal(matches(html, /<link\b[^>]*rel="canonical"[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<link rel="canonical" href="${canonical}"`))
    assert.equal(matches(html, /<meta\b[^>]*property="og:url"[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<meta property="og:url" content="${canonical}"`))
    const graph = JSON.parse(matches(html, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)[0][1])['@graph']
    const software = graph.find((node) => node.codeRepository === project.github)
    assert.equal(software.description, project.definition.en)
    assert.equal(software.url, canonical)
    assert.doesNotMatch(html, /<script\b[^>]*type="module"/i)
  }
})

test('glossary terms have bilingual pages, valid relationships and traceable definitions', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-glossary-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  run('build-org-pages.mjs', outDir)

  const slugs = new Set(glossary.map((term) => term.slug))
  assert.equal(slugs.size, glossary.length, 'duplicate glossary slug')
  for (const slug of ['gouzi', 'taolu', 'fenji-ditu', 'daoyan-chanshu', 'dongjie-guanjianzhen']) {
    assert.ok(slugs.has(slug), `missing new term ${slug}`)
  }

  for (const term of glossary) {
    const html = readOutput(outDir, `glossary/${term.slug}`)
    const url = `https://zenstory.ai/glossary/${term.slug}`
    const termHtml = matches(html, /<article class="term">([\s\S]*?)<\/article>/g)[0][1]
    const owner = projects.find((project) => project.slug === term.owner)
    assert.ok(owner, `unknown owner of ${term.slug}`)
    assert.ok(termHtml.includes(`href="/${owner.slug}"`), `missing owner link for ${term.slug}`)
    for (const related of term.related) {
      assert.ok(slugs.has(related), `broken related term ${related}`)
      assert.ok(termHtml.includes(`href="/glossary/${related}"`), `missing related link to ${related}`)
    }
    for (const section of [term.definition, term.in_practice]) {
      for (const language of ['en', 'zh']) assert.ok(section[language].trim(), `${term.slug} lacks ${language}`)
    }
    assert.equal(matches(html, /<h1\b/g).length, 1)
    assert.ok(html.includes(`<h1 lang="zh-CN">${term.term}</h1>`))
    for (const language of ['en', 'zh']) {
      const definitionHtml = term.definition[language]
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      const languageAttribute = language === 'zh' ? ' lang="zh-CN"' : ''
      assert.ok(termHtml.includes(`<p${languageAttribute}>${definitionHtml}</p>`), `missing ${language} definition`)
    }
    assert.equal(matches(html, /<link\b[^>]*rel="canonical"[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<link rel="canonical" href="${url}"`))
    assert.equal(matches(html, /<meta\b[^>]*property="og:url"[^>]*>/gi).length, 1)
    assert.ok(html.includes(`<meta property="og:url" content="${url}"`))
    assert.doesNotMatch(html, /<script\b[^>]*type="module"/i)
    const graph = JSON.parse(matches(html, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)[0][1])['@graph']
    const definition = graph.find((node) => node['@type'] === 'DefinedTerm')
    assert.equal(definition.name, term.term)
    assert.equal(definition.url, url)
    assert.equal(definition.description, term.definition.en)

    const sourceGroups = []
    for (const language of ['en', 'zh']) {
      const sources = matches(term.in_practice[language], /\]\((https:\/\/[^)]+)\)/g).map((match) => match[1])
      assert.ok(sources.length > 0, `${term.slug} lacks ${language} source references`)
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

test('organization generator preserves existing routes and writes a complete apex homepage', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-org-pages-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))

  run('build-org-pages.mjs', outDir)

  for (const route of ['projects', ...projects.map((project) => project.slug), 'glossary', ...glossary.map((term) => `glossary/${term.slug}`)]) {
    assert.match(readOutput(outDir, route), /<!doctype html>/i, `missing complete HTML for /${route}`)
  }

  const workbench = readOutput(outDir, 'workbench')
  assert.match(workbench, /as a service at app\.zenstory\.ai/)
  assert.match(workbench, /也提供 app\.zenstory\.ai 的在线服务/)
  assert.doesNotMatch(workbench, /as a service at zenstory\.ai|也提供 zenstory\.ai 的在线服务/)

  const homepage = readOutput(outDir, 'org-home')
  assert.match(homepage, /<html lang="en">/)
  assert.match(homepage, /<h1>[^<]*ZenStory AI/i)
  assert.match(homepage, /lang="zh-CN"/)
  assert.match(homepage, /href="https:\/\/app\.zenstory\.ai"[^>]*>Open (?:the )?(?:web )?workbench/i)
  assert.match(homepage, /choose by (?:task|what you want to make)/i)
  assert.match(homepage, /Source on GitHub/i)
  assert.equal(matches(homepage, /<article class="project-card">/g).length, 6)
  assert.doesNotMatch(homepage, /<script\b[^>]*type="module"/i)

  for (const project of projects) {
    assert.match(homepage, new RegExp(`href="/${project.slug}"`), `homepage should link /${project.slug}`)
    assert.match(homepage, new RegExp(`href="${project.github.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`), `homepage should link ${project.github}`)
  }

  assert.equal(matches(homepage, /<link\b[^>]*rel="canonical"[^>]*>/gi).length, 1)
  assert.match(homepage, /<link\b[^>]*rel="canonical"[^>]*href="https:\/\/zenstory\.ai\/"/i)
  assert.equal(matches(homepage, /<meta\b[^>]*property="og:url"[^>]*>/gi).length, 1)
  assert.match(homepage, /<meta\b[^>]*property="og:url"[^>]*content="https:\/\/zenstory\.ai\/"/i)

  const graph = JSON.parse(matches(homepage, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)[0][1])['@graph']
  assert.ok(graph.some((node) => node['@type'] === 'Organization' && node.url === 'https://zenstory.ai'))
  assert.ok(graph.some((node) => node['@type'] === 'WebSite' && node.url === 'https://zenstory.ai'))
  assert.ok(!graph.some((node) => node['@type'] === 'SoftwareApplication'))

  for (const route of ['projects', ...projects.map((project) => project.slug)]) {
    const html = readOutput(outDir, route)
    if (/\bGitHub stars\b|\d[\d,]* ★/.test(html)) assert.match(html, new RegExp(org.proof.as_of))
  }
})

test('docs generator adds apex-owned metadata to the clean source shell', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-doc-pages-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))

  writeFileSync(join(outDir, 'index.html'), readFileSync(join(webRoot, 'index.html'), 'utf8'))

  run('build-docs-pages.mjs', outDir)

  for (const route of ['docs', 'docs/getting-started/quick-start']) {
    const html = readOutput(outDir, route)
    const canonical = `https://zenstory.ai/${route}`
    assert.equal(matches(html, /<link\b(?=[^>]*\brel=["']canonical["'])[^>]*>/gi).length, 1)
    assert.match(html, new RegExp(`<link\\b[^>]*href="${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"[^>]*data-rh="true"|<link\\b[^>]*data-rh="true"[^>]*href="${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`, 'i'))
    assert.equal(matches(html, /<meta\b(?=[^>]*\bproperty=["']og:url["'])[^>]*>/gi).length, 1)
    assert.match(html, new RegExp(`content="${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`))
    assert.equal(matches(html, /<script\b(?=[^>]*\btype=["']application\/ld\+json["'])[^>]*>[\s\S]*?<\/script>/gi).length, 1)
    const head = html.slice(0, html.indexOf('</head>'))
    assert.doesNotMatch(head, /SoftwareApplication|https:\/\/app\.zenstory\.ai|stale\.example/)
    assert.ok(html.includes('href="https://app.zenstory.ai/dashboard"'), 'Docs need a direct workbench entrypoint in their body')

    const json = JSON.parse(matches(html, /<script\b[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi)[0][1])
    const types = json['@graph'].map((node) => node['@type'])
    assert.deepEqual(types, ['Organization', 'WebSite', 'TechArticle'])
    assert.equal(json['@graph'][2].url, canonical)
  }
})


test('account documentation keeps app entrypoints and source limits in the apex React shell', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-account-doc-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  writeFileSync(join(outDir, 'index.html'), readFileSync(join(webRoot, 'index.html'), 'utf8'))
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
  assert.equal(matches(html, /<script\b[^>]*type="module"[^>]*src="\/src\/main\.tsx"/g).length, 1)
  assert.ok(html.includes('href="https://zenstory.ai/docs/getting-started/installation"'))
  const sources = matches(html, /href="(https:\/\/github\.com\/zenstory-ai\/zenstory\/blob\/[a-f0-9]{40}\/[^"#]+#L\d+(?:-L\d+)?)"/g).map(match => match[1])
  assert.equal(sources.length, 24, 'Both languages need all twelve checked-source references')
  const midpoint = sources.length / 2
  assert.deepEqual(sources.slice(0, midpoint), sources.slice(midpoint))
  for (const source of sources) assert.ok(source.includes('/blob/76b8ab84ce73ca309083f0c1a90f3c9d7ce8d72b/'))
})

test('docs generator rejects inherited metadata rather than sanitizing arbitrary HTML', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-reject-shell-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  const clean = readFileSync(join(webRoot, 'index.html'), 'utf8')
  for (const metadata of [
    '<link href="https://app.zenstory.ai/" rel="alternate canonical">',
    '<meta content="https://app.zenstory.ai/" property="og:url">',
    '<script type="application/ld+json">{"@type":"SoftwareApplication"}</script>',
  ]) {
    writeFileSync(join(outDir, 'index.html'), clean.replace('</head>', `${metadata}</head>`))
    const result = spawnSync(process.execPath, [join(scriptsDir, 'build-docs-pages.mjs'), outDir], { encoding: 'utf8' })
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /metadata-free/i)
  }
})
