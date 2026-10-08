#!/usr/bin/env node
/** Domain boundary for the existing Vite build. No new runtime/framework.
 * Vercel checks files before rewrites: relocate the three root files LAST,
 * after docs prerender has read index.html. Keep vercel.json checked in and
 * regenerate it with --write-config; build refuses configuration drift.
 */
import { readFileSync, writeFileSync, mkdirSync, renameSync, rmSync, readdirSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const contract = JSON.parse(readFileSync(join(web, 'content/site-routing.json'), 'utf8'))
const guideRedirects = JSON.parse(readFileSync(join(web, 'content/guide-redirects.json'), 'utf8'))
const {siteOrigin:SITE, appOrigin:APP, apiOrigin:API, previewApiOrigin:PREVIEW_API, previewSiteOrigin:PREVIEW_SITE, previewAppOrigin:PREVIEW_APP} = contract
const escapeRegex = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const host = value => [{type:'host',value}]
const hosts = origins => origins.map(s => escapeRegex(new URL(s).hostname)).join('|')
const siteHosts = hosts([SITE,PREVIEW_SITE])
const appHosts = hosts([APP,PREVIEW_APP])
const previews = `${hosts([PREVIEW_SITE,PREVIEW_APP])}|.*\\.vercel\\.app`
const prefixPattern = prefixes => `/:path((?:${prefixes.map(escapeRegex).join('|')})(?:/.*)?)`
const appPaths = prefixPattern(contract.appPrefixes)
const sitePaths = prefixPattern(contract.sitePrefixes)
const redirect = (source,destination,has) => ({source,destination,permanent:true,...(has ? {has} : {})})
const rewrite = (source,destination,value) => ({source,destination,...(value ? {has:host(value)} : {})})
const internalAliases = {
  '/org-home': `${SITE}/`, '/org-home/':`${SITE}/`, '/org-home/index.html':`${SITE}/`,
  '/_app':`${APP}/`, '/_app/':`${APP}/`, '/_app/index.html':`${APP}/`, '/_app/home.html':`${APP}/`,
  '/_app/pricing.html':`${APP}/pricing`,
  '/_app/robots.txt':`${APP}/robots.txt`, '/_app/sitemap.xml':`${APP}/sitemap.xml`,
  '/_site/robots.txt':`${SITE}/robots.txt`, '/_site/sitemap.xml':`${SITE}/sitemap.xml`,
}
export const vercelConfig = {
  '$schema':'https://openapi.vercel.sh/vercel.json',
  buildCommand:'npm run build:vercel',outputDirectory:'dist',installCommand:'npm install --legacy-peer-deps',devCommand:'npm run dev',framework:'vite',
  redirects:[
    ...Object.entries(guideRedirects).flatMap(([source, destination]) => [redirect(source, `${SITE}${destination}`), redirect(`/zh${source}`, `${SITE}/zh${destination}`)]),
    ...Object.entries(internalAliases).map(([source,destination])=>redirect(source,destination)),
    // Explicit document aliases must not leave duplicate HTML URLs indexed.
    redirect('/index.html',`${APP}/`,host(appHosts)),
    redirect('/index.html',`${SITE}/`,host(`${siteHosts}|www\\.zenstory\\.ai`)),
    redirect(`${prefixPattern(contract.sitePrefixes.filter(p=>p!=='llms.txt'))}/index.html`,`${SITE}/:path`),
    redirect(appPaths,`${APP}/:path`,host('zenstory\\.ai|www\\.zenstory\\.ai')),
    redirect(appPaths,`${PREVIEW_APP}/:path`,host('geo-preview\\.zenstory\\.ai')),
    redirect(sitePaths,`${SITE}/:path`,host('app\\.zenstory\\.ai')),
    redirect(sitePaths,`${PREVIEW_SITE}/:path`,host('app-preview\\.zenstory\\.ai')),
    redirect('/:path(.*)',`${SITE}/:path`,host('www\\.zenstory\\.ai')),

  ],
  headers:[
    {source:'/:path(.*)',has:host(previews),headers:[{key:'X-Robots-Tag',value:'noindex'}]},
    {source:prefixPattern(contract.appPrefixes.filter(p=>p!=='pricing')),headers:[{key:'X-Robots-Tag',value:'noindex, nofollow'}]},
    {source:'/:path(api(?:/.*)?)',headers:[{key:'Cache-Control',value:'private, no-store'},{key:'x-vercel-enable-rewrite-caching',value:'0'}]},
  ],
  rewrites:[
    rewrite('/1e4acbc11fe3407a8a641d69a13af696.txt','/api/indexnow-key'),
    rewrite('/:path(api(?:/.*)?)',`${PREVIEW_API}/:path`,previews),
    rewrite('/:path(api(?:/.*)?)',`${API}/:path`),
    rewrite('/', '/org-home/index.html', `${siteHosts}|.*\\.vercel\\.app`),
    rewrite('/', '/_app/home.html', appHosts),
    rewrite('/pricing','/_app/pricing.html',`${appHosts}|.*\\.vercel\\.app`),
    ...['robots.txt','sitemap.xml'].flatMap(name=>[
      rewrite(`/${name}`,`/_app/${name}`,appHosts),
      rewrite(`/${name}`,`/_site/${name}`,`${siteHosts}|.*\\.vercel\\.app`),
    ]),
    // Existing static docs/project/legal pages are found before these fallbacks.
    // Unknown apex/reserved-namespace URLs must remain genuine platform 404s.
    rewrite('/:path((?!api(?:/|$)|_app(?:/|$)|_site(?:/|$)|org-home(?:/|$)).*)','/_app/index.html',`${appHosts}|.*\\.vercel\\.app`),
  ],
}

const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')
const legalContent = JSON.parse(readFileSync(join(web, 'public/locales/en/privacy.json'), 'utf8'))
function pageHead(shell,route,origin,title) {
  return shell
    .replace(/<title>[^<]*<\/title>/,`<title>${esc(title)}</title>`)
    .replace(/<meta\b[^>]*name=["']description["'][^>]*>/gi,`<meta data-rh="true" name="description" content="${esc(title)}" />`)
    .replace(/<meta\b[^>]*property=["']og:title["'][^>]*>/gi,`<meta data-rh="true" property="og:title" content="${esc(title)}" />`)
    .replace(/<meta\b[^>]*property=["']og:description["'][^>]*>/gi,`<meta data-rh="true" property="og:description" content="${esc(title)}" />`)
    .replace('</head>',`<link data-rh="true" rel="canonical" href="${origin}${route}" /><meta data-rh="true" property="og:url" content="${origin}${route}" /></head>`)
}
function renderLegalValue(value, depth = 2) {
  if (Array.isArray(value)) return `<ul>${value.map(item => `<li>${esc(String(item))}</li>`).join('')}</ul>`
  if (!value || typeof value !== 'object') return ''
  const level = Math.min(depth, 6)
  const heading = value.title ? `<h${level}>${esc(String(value.title))}</h${level}>` : ''
  const content = value.content ? `<p>${esc(String(value.content))}</p>` : ''
  const children = Object.entries(value)
    .filter(([key]) => !['title', 'content', 'lastUpdated'].includes(key))
    .map(([key, child]) => typeof child === 'string' && ['note', 'email'].includes(key)
      ? `<p>${esc(child)}</p>`
      : renderLegalValue(child, depth + 1))
    .join('')
  return `${heading}${content}${children}`
}
function legalPage(shell, route, title, document) {
  const body = `<main><article><h1>${esc(String(document.title))}</h1>` +
    (document.lastUpdated ? `<p>${esc(String(document.lastUpdated))}</p>` : '') +
    `${renderLegalValue(document.sections)}</article></main>`
  return pageHead(shell, route, SITE, title).replace('<div id="root"></div>', `<div id="root">${body}</div>`)
}
/** English route of a site route (`/zh/x` → `/x`, `/zh` → `/`), or null when it is not a Chinese page. */
const englishRoute=route=>route==='/zh' ? '/' : route.startsWith('/zh/') ? route.slice(3) : null
const chineseRoute=route=>route==='/' ? '/zh' : `/zh${route}`
/** Read the page's own editorial date, never the build clock or filesystem mtime. */
function pageModifiedOn(html, url) {
  const dates = []
  for (const [, source] of html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/gi)) {
    const document = JSON.parse(source)
    for (const node of document['@graph'] ?? [document]) {
      if (node.url !== url || !['Article', 'TechArticle'].includes(node['@type']) || node.dateModified === undefined) continue
      const date = node.dateModified
      const parsed = new Date(`${date}T00:00:00Z`)
      assert.ok(typeof date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(date) &&
        Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date,
      `Invalid page dateModified: ${url}`)
      dates.push(date)
    }
  }
  assert.ok(new Set(dates).size <= 1, `Conflicting page dateModified: ${url}`)
  return dates[0]
}
/**
 * Sitemap with hreflang pairs: a route whose Chinese counterpart exists lists
 * both languages (zh-CN plus language-only zh, so Chinese searchers outside
 * mainland China also get the Chinese page, and x-default = English) on each
 * of the two entries.
 */
const sitemap=(origin,routes,modifiedOn=new Map())=>{
  const set=new Set(routes)
  const entry=route=>{
    const en=englishRoute(route) ?? route
    const zh=chineseRoute(en)
    const paired=set.has(en) && set.has(zh)
    const alternates=paired ? [['en',en],['zh-CN',zh],['zh',zh],['x-default',en]].map(([lang,r])=>`<xhtml:link rel="alternate" hreflang="${lang}" href="${esc(origin+r)}"/>`).join('') : ''
    const date = modifiedOn.get(route)
    return `  <url><loc>${esc(origin+route)}</loc>${date ? `<lastmod>${date}</lastmod>` : ''}${alternates}</url>`
  }
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n${routes.map(entry).join('\n')}\n</urlset>\n`
}

export function finalizeSite(outDir) {
  const shell=readFileSync(join(outDir,'index.html'),'utf8')
  assert.equal(shell.split('</head>').length, 2, 'Expected one deterministic head insertion point')
  assert.doesNotMatch(shell.slice(0, shell.indexOf('</head>')), /canonical|og:url|application\/ld\+json/i, 'Expected a metadata-free source shell (no canonical, og:url or JSON-LD)')
  assert.ok(shell.includes('<div id="root"></div>'),'Expected unfilled Vite app shell; finalizer must run after docs, only once')
  assert.ok(existsSync(join(outDir,'org-home/index.html')),'Organization home must be generated first')
  const siteRoutes=['/']
  function walk(dir,relative='') {
    for(const e of readdirSync(dir,{withFileTypes:true})) {
      const path=relative ? `${relative}/${e.name}` : e.name
      if(e.isDirectory() && !path.startsWith('_') && path!=='org-home' && path!=='assets') walk(join(dir,e.name),path)
      else if(e.isFile() && e.name==='index.html' && relative) siteRoutes.push(`/${relative}`)
    }
  }
  walk(outDir)
  for(const name of ['_app','_site']) mkdirSync(join(outDir,name),{recursive:true})
  for(const [route,title,document] of [
    ['/privacy-policy','Privacy Policy — ZenStory',legalContent],
    ['/terms-of-service','Terms of Service — ZenStory',legalContent.terms],
  ]) {
    mkdirSync(join(outDir,route),{recursive:true})
    writeFileSync(join(outDir,route,'index.html'),legalPage(shell,route,title,document))
    siteRoutes.push(route)
  }
  writeFileSync(join(outDir,'_app/home.html'),pageHead(shell,'/',APP,'ZenStory — AI novel-writing workbench'))
  writeFileSync(join(outDir,'_app/pricing.html'),pageHead(shell,'/pricing',APP,'ZenStory pricing — AI writing workbench'))
  const modifiedOn = new Map()
  for (const route of siteRoutes) {
    const path = route === '/' ? 'org-home' : route.slice(1)
    const date = pageModifiedOn(readFileSync(join(outDir,path,'index.html'), 'utf8'), SITE+route)
    if (date) modifiedOn.set(route, date)
  }
  writeFileSync(join(outDir,'_site/sitemap.xml'),sitemap(SITE,[...new Set(siteRoutes)].sort(),modifiedOn))
  writeFileSync(join(outDir,'_app/sitemap.xml'),sitemap(APP,['/','/pricing']))
  writeFileSync(join(outDir,'_site/robots.txt'),`# Public organization corpus and legacy redirects are crawlable.\nUser-agent: *\nAllow: /\nDisallow: /api\n\nSitemap: ${SITE}/sitemap.xml\n`)
  writeFileSync(join(outDir,'_app/robots.txt'),`User-agent: *\nAllow: /\nDisallow: /api\n${contract.appPrefixes.filter(p=>p!=='pricing').map(p=>`Disallow: /${p}`).join('\n')}\n\nSitemap: ${APP}/sitemap.xml\n`)
  renameSync(join(outDir,'index.html'),join(outDir,'_app/index.html'))
  for(const file of ['robots.txt','sitemap.xml']) rmSync(join(outDir,file),{force:true})
  for(const file of ['index.html','robots.txt','sitemap.xml']) assert.equal(existsSync(join(outDir,file)),false,`Root ${file} shadows host routing`)
  console.log(`Domain layout: ${new Set(siteRoutes).size} organization URLs; 2 app URLs; no root filesystem shadows.`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if(process.argv.includes('--write-config')) writeFileSync(join(web,'vercel.json'),JSON.stringify(vercelConfig,null,2)+'\n')
  else {
    assert.deepEqual(JSON.parse(readFileSync(join(web,'vercel.json'),'utf8')),vercelConfig,'Regenerate vercel.json with node scripts/build-site-layout.mjs --write-config')
    finalizeSite(resolve(process.argv[2] || join(web,'dist')))
  }
}
