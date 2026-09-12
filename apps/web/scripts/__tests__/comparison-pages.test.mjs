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
const comparisons = JSON.parse(readFileSync(join(webRoot, 'content/comparisons.json'), 'utf8'))
const AXES = ['fit', 'environment', 'configuration', 'files', 'version', 'review']
const EXPECTED_PROJECTS = ['oh-story', 'dsh', 'workbench']
const EXPECTED_SOURCE_SHAS = {
  'oh-story': '070d744b075024b530d1befbaa2474531f4700a1',
  dsh: '493cd7793f69d4c02e19a4431d161b5284e7ad38',
  workbench: '9bad4fd5f5f14f2ac77372caf177c598aa238a9d',
}
const EXPECTED_DSH_UPSTREAM_COMMIT = 'abe96630d115afbd528f2329e2d8d604d5d5673c'
const EXPECTED_DSH_MANIFEST_SOURCE = 'https://github.com/zenstory-ai/oh-story-dsh/blob/493cd7793f69d4c02e19a4431d161b5284e7ad38/packages/knowledge/oh-story/manifest.json#L1-L12'

const escape = (s) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const richText = (s) => escape(s)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2">$1</a>')
const matches = (html, pattern) => [...html.matchAll(pattern)]
const readOutput = (outDir, route) => readFileSync(join(outDir, route, 'index.html'), 'utf8')
const run = (script, outDir, cwd = webRoot) => {
  const result = spawnSync(process.execPath, [join(cwd, 'scripts', script), outDir], { cwd, encoding: 'utf8' })
  return result
}

test('writing workflow comparison renders three source-backed choices across the same six axes', (t) => {
  assert.equal(comparisons.length, 1)
  const comparison = comparisons[0]
  assert.equal(comparison.slug, 'writing-workflows')
  assert.match(comparison.checked_on, /^\d{4}-\d{2}-\d{2}$/)
  assert.deepEqual(comparison.options.map((option) => option.project), EXPECTED_PROJECTS)
  assert.match(comparison.disclosure.en, /first-party/i)
  assert.match(comparison.disclosure.zh, /第一方|自有项目选择指南/)
  assert.match([comparison.disclosure.en, ...comparison.boundaries.en].join(' '), /not (?:an )?independent|not a benchmark/i)
  assert.match([comparison.disclosure.zh, ...comparison.boundaries.zh].join(' '), /不是.*独立|不是.*基准|不构成.*独立/)
  assert.match(comparison.answer.en, /both DSH Web and ZenStory.*browser.*host, files and account boundary/i)
  assert.match(comparison.answer.zh, /DSH Web 和 ZenStory.*浏览器.*宿主、文件与账户边界/)

  const outDir = mkdtempSync(join(tmpdir(), 'zenstory-comparison-'))
  t.after(() => rmSync(outDir, { recursive: true, force: true }))
  const result = run('build-org-pages.mjs', outDir)
  assert.equal(result.status, 0, result.stderr)

  const route = `/compare/${comparison.slug}`
  const url = `https://zenstory.ai${route}`
  const html = readOutput(outDir, `compare/${comparison.slug}`)
  const article = matches(html, /<article class="comparison">([\s\S]*?)<\/article>/g)[0][1]
  assert.equal(matches(html, /<h1\b/g).length, 1)
  assert.ok(article.includes(`<h1>${escape(comparison.title.en)}</h1>`))
  assert.ok(article.includes(`<p class="lede" lang="zh-CN">${escape(comparison.title.zh)}</p>`))
  assert.ok(article.includes(richText(comparison.answer.en)))
  assert.ok(article.includes(richText(comparison.answer.zh)))
  assert.ok(article.includes(richText(comparison.disclosure.en)))
  assert.ok(article.includes(richText(comparison.disclosure.zh)))

  for (const option of comparison.options) {
    const project = projects.find((candidate) => candidate.slug === option.project)
    assert.ok(project)
    for (const axis of AXES) {
      for (const language of ['en', 'zh']) {
        assert.ok(option[axis][language].trim(), `${option.project}.${axis}.${language}`)
        assert.ok(article.includes(richText(option[axis][language])))
      }
    }

    const sourceGroups = []
    for (const language of ['en', 'zh']) {
      const sources = option.sources[language]
      assert.ok(sources.length > 0, `${option.project} lacks ${language} sources`)
      const urls = sources.flatMap((source) => matches(source, /\]\((https:\/\/[^)]+)\)/g).map((match) => match[1]))
      assert.equal(urls.length, sources.length, `${option.project} ${language} source labels must each contain one link`)
      for (const source of sources) {
        assert.ok(article.includes(richText(source)), `missing rendered ${language} source`)
      }
      for (const source of urls) {
        const parsed = new URL(source)
        assert.equal(parsed.origin, 'https://github.com')
        assert.equal(parsed.pathname.split('/').slice(0, 3).join('/'), new URL(project.github).pathname)
        assert.ok(parsed.pathname.includes(`/blob/${EXPECTED_SOURCE_SHAS[option.project]}/`))
        assert.match(parsed.hash, /^#L\d+(?:-L\d+)?$/)
      }
      sourceGroups.push(urls.sort())
    }
    assert.deepEqual(sourceGroups[0], sourceGroups[1], `${option.project} cites different EN/ZH sources`)
  }

  const dsh = comparison.options.find((option) => option.project === 'dsh')
  const direct = comparison.options.find((option) => option.project === 'oh-story')
  const workbench = comparison.options.find((option) => option.project === 'workbench')
  assert.match(dsh.version.en, /pinned|bundled/i)
  assert.match(dsh.version.zh, /固定|内置|随附|捆绑/)
  assert.match(dsh.version.en, /0\.7\.10/)
  assert.ok(dsh.version.en.includes(EXPECTED_DSH_UPSTREAM_COMMIT.slice(0, 7)))
  assert.ok(!dsh.version.en.includes(EXPECTED_DSH_UPSTREAM_COMMIT))
  assert.ok(dsh.sources.en.includes(`[Pinned upstream release and commit](${EXPECTED_DSH_MANIFEST_SOURCE})`))
  assert.ok(dsh.sources.zh.some((source) => source.endsWith(`](${EXPECTED_DSH_MANIFEST_SOURCE})`)))
  assert.match(direct.version.en, /070d744/)
  assert.match(`${direct.version.en} ${dsh.version.en}`, /not (?:assume|your standalone)|separately bundled DSH snapshot/i)
  assert.match(`${direct.version.zh} ${dsh.version.zh}`, /不要默认.*DSH.*快照|不是你另装的 Oh Story/)
  assert.notEqual(dsh.version.en, direct.version.en)
  assert.notEqual(dsh.version.en, workbench.version.en)
  assert.match(`${comparison.disclosure.en} ${workbench.fit.en} ${workbench.environment.en}`, /hosted service.*self-hosting is (?:a )?separate|hosted ZenStory.*account-based/i)
  assert.match(`${comparison.disclosure.zh} ${workbench.fit.zh} ${workbench.environment.zh}`, /托管在线服务.*自托管是另一种|账户化的托管项目工作台/)

  for (const field of ['checklist', 'boundaries']) {
    assert.equal(comparison[field].en.length, comparison[field].zh.length)
    for (const language of ['en', 'zh']) {
      const attr = language === 'zh' ? ' lang="zh-CN"' : ''
      for (const item of comparison[field][language]) {
        assert.ok(article.includes(`<li${attr}>${richText(item)}</li>`), `missing ${field}.${language} item`)
      }
    }
  }

  for (const axis of AXES) {
    const section = matches(article, new RegExp(`<section aria-labelledby="axis-${axis}">([\\s\\S]*?)<\\/section>`, 'g'))[0][1]
    assert.equal(matches(section, /<li(?: lang="zh-CN")?>/g).length, 6)
    for (const project of EXPECTED_PROJECTS) {
      assert.equal(matches(section, new RegExp(`href="\/${project}"`, 'g')).length, 2)
    }
  }

  assert.equal(matches(html, /<link\b[^>]*rel="canonical"[^>]*>/gi).length, 1)
  assert.ok(html.includes(`<link rel="canonical" href="${url}">`))
  assert.equal(matches(html, /<meta\b[^>]*property="og:url"[^>]*>/gi).length, 1)
  assert.ok(html.includes(`<meta property="og:url" content="${url}">`))
  assert.ok(html.includes('<meta property="og:type" content="article">'))
  const graph = JSON.parse(matches(html, /<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)[0][1])['@graph']
  assert.deepEqual(graph.map((node) => node['@type']), ['Organization', 'TechArticle', 'BreadcrumbList'])
  assert.equal(graph[1].url, url)
  assert.equal(graph[1]['@id'], `${url}#article`)
  assert.equal(graph[1].dateModified, comparison.checked_on)
  assert.equal(graph[1].publisher['@id'], 'https://zenstory.ai/#org')
  assert.deepEqual(graph[2].itemListElement.map((item) => item.item), ['https://zenstory.ai', url])

  for (const entry of ['org-home', 'projects', ...EXPECTED_PROJECTS]) {
    assert.ok(readOutput(outDir, entry).includes(`href="${route}"`), `/${entry} lacks comparison link`)
  }
  for (const project of projects.filter((candidate) => !EXPECTED_PROJECTS.includes(candidate.slug))) {
    assert.ok(!readOutput(outDir, project.slug).includes(`href="${route}"`), `/${project.slug} must not link comparison`)
  }

  writeFileSync(join(outDir, 'index.html'), readFileSync(join(webRoot, 'index.html'), 'utf8'))
  const docsResult = run('build-docs-pages.mjs', outDir)
  assert.equal(docsResult.status, 0, docsResult.stderr)
  finalizeSite(outDir)
  const siteMap = readFileSync(join(outDir, '_site/sitemap.xml'), 'utf8')
  const appMap = readFileSync(join(outDir, '_app/sitemap.xml'), 'utf8')
  assert.equal(siteMap.split(`<loc>${url}</loc>`).length - 1, 1)
  assert.ok(!appMap.includes('/compare/'))
  assert.equal(matches(siteMap, /<loc>/g).length + matches(appMap, /<loc>/g).length, 52)
})

test('comparison identities reject traversal, unknown or duplicate options and duplicate routes before writing', (t) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-invalid-comparisons-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'scripts'))
  cpSync(join(webRoot, 'content'), join(root, 'content'), { recursive: true })
  for (const file of ['build-org-pages.mjs', 'org-pages.css']) cpSync(join(scriptsDir, file), join(root, 'scripts', file))
  const valid = comparisons[0]
  const replaceOption = (options, index, project) => options.map((option, i) => i === index ? { ...option, project } : option)
  const cases = [
    [{ ...valid, slug: '../escape' }],
    [{ ...valid, slug: undefined }],
    [{ ...valid, slug: 123 }],
    [{ ...valid, options: replaceOption(valid.options, 0, 'unknown') }],
    [{ ...valid, options: replaceOption(valid.options, 1, valid.options[0].project) }],
    [valid, valid],
  ]
  for (const invalid of cases) {
    writeFileSync(join(root, 'content/comparisons.json'), JSON.stringify(invalid))
    const out = join(root, 'output')
    const result = run('build-org-pages.mjs', out, root)
    assert.notEqual(result.status, 0)
    assert.match(result.stderr, /Invalid comparison identity|Invalid comparison options|Duplicate comparison route/)
    assert.equal(existsSync(out), false)
  }
})
