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

test('docs generator removes inherited URL/schema metadata before adding apex-owned metadata', (t) => {
  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-doc-pages-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))

  writeFileSync(join(outDir, 'index.html'), `<!doctype html><html><head>
    <title>Workbench</title>
    <meta data-rh="true" name="description" content="Workbench description" />
    <meta data-rh="true" property="og:type" content="website" />
    <meta data-rh="true" property="og:title" content="Workbench" />
    <meta data-rh="true" property="og:description" content="Workbench description" />
    <link rel="canonical" href="https://app.zenstory.ai/" />
    <link href="https://stale.example/" rel="alternate canonical">
    <meta property="og:url" content="https://app.zenstory.ai/" />
    <meta content="https://stale.example/" property="og:url">
    <script type="application/ld+json">{"@type":"SoftwareApplication","url":"https://app.zenstory.ai"}</script>
    <script data-rh="true" type="application/ld+json">{"@type":"WebSite","url":"https://app.zenstory.ai"}</script>
  </head><body><div id="root"></div><script type="module" src="/assets/app.js"></script></body></html>`)

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
