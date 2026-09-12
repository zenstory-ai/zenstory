import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

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
    assert.doesNotMatch(html, /SoftwareApplication|https:\/\/app\.zenstory\.ai|stale\.example/)

    const json = JSON.parse(matches(html, /<script\b[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi)[0][1])
    const types = json['@graph'].map((node) => node['@type'])
    assert.deepEqual(types, ['Organization', 'WebSite', 'TechArticle'])
    assert.equal(json['@graph'][2].url, canonical)
  }
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
