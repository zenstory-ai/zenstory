import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { finalizeSite, vercelConfig } from '../build-site-layout.mjs'

test('Vercel config is source-controlled and API bypasses the SPA', () => {
  assert.deepEqual(JSON.parse(readFileSync(new URL('../../vercel.json', import.meta.url))), vercelConfig)
  assert.equal(vercelConfig.rewrites[0].source, '/:path(api(?:/.*)?)')
  assert.equal(vercelConfig.rewrites[0].destination, 'https://api.zenstory.ai/:path')
  assert.ok(vercelConfig.headers.some(r => r.source === '/:path(api(?:/.*)?)' && r.headers.some(h=>h.key==='x-vercel-enable-rewrite-caching' && h.value==='0')))
  assert.ok(vercelConfig.redirects.findIndex(r=>r.source.endsWith('/index.html') && r.source.startsWith('/:path')) < vercelConfig.redirects.findIndex(r=>r.source.includes('projects|') && r.has), 'Clean aliases before host redirects')
  const scripts = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url))).scripts
  assert.equal(vercelConfig.buildCommand, 'npm run build:vercel')
  assert.ok(!scripts.build.includes('build-site-layout'), 'Default/Docker build retains a normal root app index')
  assert.match(scripts['build:vercel'], /build-site-layout/)
  const workflow = readFileSync(new URL('../../../../.github/workflows/ci.yml', import.meta.url), 'utf8')
  for (const [, script] of workflow.matchAll(/npm run ([\w:-]+)/g)) assert.ok(scripts[script], `CI script ${script} exists`)
  assert.match(readFileSync(new URL('../../playwright.config.ts', import.meta.url), 'utf8'), /testIgnore: \[.*geo-domain-smoke/)
  assert.ok(!vercelConfig.routes)
  assert.ok(vercelConfig.headers.some(r => r.source === '/:path(.*)' && r.has), 'Preview headers must include root slash')
  assert.ok(vercelConfig.redirects.some(r => r.source === '/:path(.*)' && r.destination === 'https://zenstory.ai/:path'), 'www canonical redirect must include root slash')
  assert.ok(vercelConfig.rewrites.at(-1).has, 'No unconditional apex SPA fallback')
})

test('post-build output cannot shadow host rewrites; sitemap uses canonical routes only', () => {
  const dir = mkdtempSync(join(tmpdir(), 'site-layout-'))
  try {
    writeFileSync(join(dir, 'index.html'), '<html><head><title>App</title><meta name="description" content="App" /></head><body><div id="root"></div></body></html>')
    writeFileSync(join(dir, 'robots.txt'), 'old robots')
    writeFileSync(join(dir, 'sitemap.xml'), 'old sitemap')
    for (const path of ['org-home','projects','docs/example','workbench']) {
      mkdirSync(join(dir,path), {recursive:true});writeFileSync(join(dir,path,'index.html'), '<html>Existing content</html>')
    }
    finalizeSite(dir)
    for (const f of ['index.html','robots.txt','sitemap.xml']) assert.equal(existsSync(join(dir,f)), false)
    assert.match(readFileSync(join(dir,'_app/home.html'),'utf8'), /href="https:\/\/app\.zenstory\.ai\/"/)
    assert.doesNotMatch(readFileSync(join(dir,'_app/index.html'),'utf8'), /rel="canonical"/)
    const siteMap=readFileSync(join(dir,'_site/sitemap.xml'),'utf8')
    assert.match(siteMap, /https:\/\/zenstory\.ai\/docs\/example/)
    assert.doesNotMatch(siteMap, /app\.zenstory|org-home|_app|_site|\/pricing|\/login/)
    const appMap=readFileSync(join(dir,'_app/sitemap.xml'),'utf8')
    assert.match(appMap, /https:\/\/app\.zenstory\.ai\/pricing/)
    assert.doesNotMatch(appMap, /\/docs|\/dashboard|\/login/)
    assert.match(readFileSync(join(dir,'_site/robots.txt'),'utf8'), /Sitemap: https:\/\/zenstory\.ai\/sitemap.xml/)
    assert.equal(readFileSync(join(dir,'docs/example/index.html'),'utf8'), '<html>Existing content</html>')
  } finally { rmSync(dir, {recursive:true,force:true}) }
})

test('all protected/public route roots are classified and aliases covered', () => {
  const contract=JSON.parse(readFileSync(new URL('../../content/site-routing.json', import.meta.url)))
  const projects=JSON.parse(readFileSync(new URL('../../content/projects.json', import.meta.url)))
  for (const p of projects) assert.ok(contract.sitePrefixes.includes(p.slug))
  const src=readFileSync(new URL('../../src/App.tsx', import.meta.url),'utf8')
  for (const [,path] of src.matchAll(/path="(\/[^"*]+)"/g)) {
    assert.ok([...contract.sitePrefixes,...contract.appPrefixes].includes(path.split('/')[1]), path)
  }
  for (const path of ['/_app/index.html','/_app/home.html','/org-home/index.html','/_site/sitemap.xml','/_app/robots.txt']) {
    assert.ok(vercelConfig.redirects.some(r=>r.source===path), `missing canonical alias ${path}`)
  }
})


test('domain finalizer rejects contaminated shell metadata before generating files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'site-layout-reject-'))
  try {
    mkdirSync(join(dir, 'org-home'))
    writeFileSync(join(dir, 'org-home/index.html'), '<html>Org</html>')
    for (const tag of [
      '<link rel="canonical" href="https://stale.example/">',
      '<meta property="og:url" content="https://stale.example/">',
      '<script type="application/ld+json">{}</script>',
    ]) {
      writeFileSync(join(dir, 'index.html'), `<html><head>${tag}</head><body><div id="root"></div></body></html>`)
      assert.throws(() => finalizeSite(dir), /metadata-free/i)
      assert.equal(existsSync(join(dir, '_app')), false)
    }
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
