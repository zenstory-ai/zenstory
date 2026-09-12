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
const {siteOrigin:SITE, appOrigin:APP, apiOrigin:API, previewSiteOrigin:PREVIEW_SITE, previewAppOrigin:PREVIEW_APP} = contract
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
    ...Object.entries(internalAliases).map(([source,destination])=>redirect(source,destination)),
    // Explicit document aliases must not leave duplicate HTML URLs indexed.
    redirect('/index.html',`${APP}/`,host(appHosts)),
    redirect('/index.html',`${SITE}/`,host(`${siteHosts}|www\\.zenstory\\.ai`)),
    redirect('/:path((?:projects|oh-story|drama-skills|novel-to-game|video-recap|dsh|workbench|glossary|docs|privacy-policy|terms-of-service)(?:/.*)?)/index.html',`${SITE}/:path`),
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
function pageHead(shell,route,origin,title) {
  return shell
    .replace(/<title>[^<]*<\/title>/,`<title>${esc(title)}</title>`)
    .replace(/<meta\b[^>]*name=["']description["'][^>]*>/gi,`<meta data-rh="true" name="description" content="${esc(title)}" />`)
    .replace(/<meta\b[^>]*property=["']og:title["'][^>]*>/gi,`<meta data-rh="true" property="og:title" content="${esc(title)}" />`)
    .replace(/<meta\b[^>]*property=["']og:description["'][^>]*>/gi,`<meta data-rh="true" property="og:description" content="${esc(title)}" />`)
    .replace('</head>',`<link data-rh="true" rel="canonical" href="${origin}${route}" /><meta data-rh="true" property="og:url" content="${origin}${route}" /></head>`)
}
const sitemap=urls=>`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.map(url=>`  <url><loc>${esc(url)}</loc></url>`).join('\n')}\n</urlset>\n`

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
  for(const [route,title] of [['/privacy-policy','Privacy Policy — ZenStory'],['/terms-of-service','Terms of Service — ZenStory']]) {
    mkdirSync(join(outDir,route),{recursive:true})
    writeFileSync(join(outDir,route,'index.html'),pageHead(shell,route,SITE,title))
    siteRoutes.push(route)
  }
  writeFileSync(join(outDir,'_app/home.html'),pageHead(shell,'/',APP,'ZenStory — AI novel-writing workbench'))
  writeFileSync(join(outDir,'_app/pricing.html'),pageHead(shell,'/pricing',APP,'ZenStory pricing — AI writing workbench'))
  writeFileSync(join(outDir,'_site/sitemap.xml'),sitemap([...new Set(siteRoutes)].sort().map(route=>SITE+route)))
  writeFileSync(join(outDir,'_app/sitemap.xml'),sitemap([APP+'/',APP+'/pricing']))
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
