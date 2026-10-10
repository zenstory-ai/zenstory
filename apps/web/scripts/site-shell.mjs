/**
 * Shared shell for every static page on zenstory.ai: head metadata, the
 * organization header and footer, and the language model.
 *
 * Language model: one language per URL. English pages live at the root
 * (`/oh-story`), Chinese pages under `/zh` (`/zh/oh-story`). Each page names
 * its counterpart with hreflang links; the header language switch is a plain
 * link to that counterpart; clicking it also stores the choice in a cookie the home redirect reads
 * (langChoiceScript). The page language itself never depends on a script or on storage.
 *
 * The workbench docs are the one exception: they stay on a single URL with the
 * Chinese article first and the English article below (see build-docs-pages).
 */
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const SITE = 'https://zenstory.ai'
export const APP = 'https://app.zenstory.ai'
/**
 * A link into the workbench app (HTML-escaped). `lang` opens the app in the page's language; `source`
 * names the slot that sent the visitor, which the app's analytics keeps. "Open the app" links go to
 * `/login` (a signed-in visitor lands on the dashboard), actions that start writing go to `/register`;
 * neither passes through the app's own landing page.
 */
export const appHref = (lang, path, source) => `${APP}${path}?lang=${lang}&amp;source=${source}`
export const LANGS = ['en', 'zh']
/** BCP 47 tag per site language (html lang, hreflang, JSON-LD inLanguage). */
export const LOCALE = { en: 'en', zh: 'zh-CN' }
export const OG_LOCALE = { en: 'en_US', zh: 'zh_CN' }

const here = dirname(fileURLToPath(import.meta.url))
export const webRoot = resolve(here, '..')
export const org = JSON.parse(readFileSync(join(webRoot, 'content/org.json'), 'utf8'))
export const projects = JSON.parse(readFileSync(join(webRoot, 'content/projects.json'), 'utf8'))

export const esc = (s) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export const jsonld = (obj) => `<script type="application/ld+json">${JSON.stringify(obj).replace(/</g, '\\u003c')}</script>`

/** Pick the rendering for `lang`. */
export const t = (lang, en, zh) => (lang === 'zh' ? zh : en)

/** The URL path of an English route in `lang`. `/` becomes `/zh` in Chinese. */
export const localized = (lang, route) => (lang === 'zh' ? (route === '/' ? '/zh' : `/zh${route}`) : route)
/** Both language paths of an English route. */
export const alternatesOf = (route) => ({ en: localized('en', route), zh: localized('zh', route) })

export const orgNode = { '@type': 'Organization', '@id': `${SITE}/#org`, name: org.name, url: SITE, sameAs: [org.github], logo: `${SITE}/brand/zenstory-ai-mark.svg` }

export const brandMark = (size = 28) => `<img src="/brand/zenstory-ai-mark.svg" alt="" width="${size}" height="${size}">`
export const arrowGlyph = '<span class="arrow" aria-hidden="true">→</span>'
export const extGlyph = '<span class="arrow" aria-hidden="true">↗</span>'

// Classic script (no type="module"): organization pages must stay free of app JavaScript.
// Reveals the copy buttons only when a clipboard exists.
export const copyScript = `<script>(function(){if(navigator.clipboard&&navigator.clipboard.writeText){var c=document.querySelectorAll('button.copy');for(var j=0;j<c.length;j++){c[j].hidden=false;c[j].addEventListener('click',function(){var s=this;if(!(navigator.clipboard&&navigator.clipboard.writeText))return;navigator.clipboard.writeText(s.getAttribute('data-copy')).then(function(){var l=s.querySelector('.copy-idle'),d=s.querySelector('.copy-done');l.hidden=true;d.hidden=false;setTimeout(function(){l.hidden=false;d.hidden=true},1600)},function(){})})}}})()</script>`

/**
 * Progressive enhancement: ordinary topic links work without this script. Ranks matches: a word found
 * in a title outranks one found in a description; topic and project names only count for a word that
 * matches no title or description. Question phrasing (怎么写开头, how to …) and a few writer synonyms
 * (拆文/拆书, 开头/开篇, 对白/台词) are folded in; with no full match, the closest guides are listed.
 */
export const guideSearchScript = `<script>(function(){
var form=document.querySelector('[data-guide-search]');if(!form)return;
var input=form.querySelector('input'),status=form.querySelector('[data-search-status]'),results=document.querySelector('[data-search-results]'),ul=results.querySelector('ul'),browse=document.querySelectorAll('[data-library-browse]');
var FILLER=/怎么样|怎么|如何|怎样|为什么|什么|哪些|哪个|能不能|可以|请问|一下|吗|呢|吧|啊|的|了/g;
var STOP={how:1,to:1,a:1,an:1,the:1,do:1,i:1,my:1,for:1,in:1,of:1,with:1,what:1,is:1,and:1,or:1,write:1};
var ALIAS={'拆文':['拆书','拆解'],'拆书':['拆文','拆解'],'开头':['开篇','黄金三章','第一章'],'开篇':['开头','黄金三章'],'扫榜':['榜单'],'对白':['对话','台词'],'对话':['对白','台词'],'台词':['对白','对话'],'人设':['人物','角色'],'角色':['人物','人设'],'人物':['角色','人设'],'剪映':['capcut'],'capcut':['剪映'],'卡文':['写不下去'],'续写':['接着写'],'opening':['first chapter','hook'],'dialogue':['dialog'],'storyboard':['shot list']};
function norm(v){return (v||'').normalize('NFKC').toLocaleLowerCase();}
var data=[].slice.call(ul.children).map(function(row,i){return {row:row,i:i,title:norm(row.getAttribute('data-search-title')),body:norm(row.getAttribute('data-search-body')),topic:norm(row.getAttribute('data-search-topic'))};});
function words(q){var ws=norm(q).replace(FILLER,' ').split(/[\\s,，。？?！!、;；:：]+/).map(function(w){return /^[写做改用学找][\\u4e00-\\u9fff]{2,}$/.test(w)?w.slice(1):w;}).filter(function(w){return w&&!STOP[w];}),multi=ws.filter(function(w){return !/^[\\u4e00-\\u9fff]$/.test(w);});return multi.length?multi:ws;}
function hit(text,w){var f=[w].concat(ALIAS[w]||[]);for(var k=0;k<f.length;k++)if(text.indexOf(f[k])!==-1)return true;return false;}
function strong(d,w){return hit(d.title,w)?10:hit(d.body,w)?3:0;}
function grams(list){var out=[];list.forEach(function(w){if(/^[\\u4e00-\\u9fff]{3,}$/.test(w)){for(var k=0;k+2<=w.length;k++)out.push(w.slice(k,k+2));}else out.push(w);});return out;}
function update(){
var raw=input.value.trim(),list=words(raw),ranked=[],related=false;
if(list.length){
var weak=list.map(function(w){return !data.some(function(d){return strong(d,w);});});
data.forEach(function(d){var s=0;for(var k=0;k<list.length;k++){var v=strong(d,list[k])||(weak[k]&&hit(d.topic,list[k])?1:0);if(!v)return;s+=v;}ranked.push({d:d,s:s});});
if(!ranked.length){related=true;var g=grams(list);data.forEach(function(d){var s=0;g.forEach(function(w){s+=strong(d,w);});if(s)ranked.push({d:d,s:s});});}
}
ranked.sort(function(a,b){return b.s-a.s||a.d.i-b.d.i;});if(related)ranked=ranked.slice(0,20);
data.forEach(function(d){d.row.hidden=true;});ranked.forEach(function(r){r.d.row.hidden=false;ul.appendChild(r.d.row);});
results.hidden=!raw;for(var j=0;j<browse.length;j++)browse[j].hidden=!!raw&&ranked.length>0;
status.textContent=!raw?'':!ranked.length?status.getAttribute('data-empty'):related?status.getAttribute('data-related').replace('{n}',ranked.length):ranked.length+' '+status.getAttribute('data-count');
var url=new URL(location.href);if(raw)url.searchParams.set('q',raw);else url.searchParams.delete('q');history.replaceState(null,'',url.pathname+url.search+url.hash);}
form.addEventListener('submit',function(event){event.preventDefault();update();});input.addEventListener('input',update);
form.addEventListener('reset',function(){input.value='';update();input.focus();});
window.addEventListener('popstate',function(){input.value=new URL(location.href).searchParams.get('q')||'';update();});
input.value=new URL(location.href).searchParams.get('q')||'';form.hidden=false;update();
})()</script>`

export const FONTS = 'https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Plus+Jakarta+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap'
/** The homepage's own type system (see the `body[data-page="home"]` block in org-pages.css). */
export const HOME_FONTS = 'https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap'

/**
 * Remembers a language picked with the switch: a first-party `zs_lang` cookie (one year) that the
 * organization host's `/` and `/zh` redirects read (build-site-layout.mjs). Only the click sets it;
 * reading a page never does, and without JavaScript the switch is an ordinary link.
 */
export const langChoiceScript = `<script>document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('.lang-switch a[hreflang]');if(!a)return;document.cookie='zs_lang='+(a.getAttribute('hreflang')==='en'?'en':'zh')+';path=/;max-age=31536000;samesite=lax'+(location.protocol==='https:'?';secure':'')})</script>`

/** Header language switch: two links, the current language marked. */
export const langSwitch = (lang, links) => `<div class="lang-switch" role="group" aria-label="${t(lang, 'Language', '语言')}">
      <a href="${esc(links.en)}" lang="en" hreflang="en"${lang === 'en' ? ' aria-current="true"' : ''}>EN</a>
      <a href="${esc(links.zh)}" lang="zh-CN" hreflang="zh-CN"${lang === 'zh' ? ' aria-current="true"' : ''}>中文</a>
    </div>`

/**
 * Site header. `section` is the active top-level route ('/projects', '/guides'
 * or null; the glossary is reached from the footer and /guides); `switchLinks` are the two language destinations.
 */
export const nav = (lang, { section = null, switchLinks }) => {
  const item = (route, label) => `<a href="${localized(lang, route)}"${section === route ? ' aria-current="page"' : ''}>${label}</a>`
  return `
<header class="top">
  <div class="wrap top-row">
    <a class="brand" href="${localized(lang, '/')}">${brandMark(28)}<span class="wordmark">ZenStory AI</span></a>
    <nav aria-label="${t(lang, 'Site', '站点')}">
      ${item('/projects', t(lang, 'Projects', '项目'))}
      ${item('/guides', t(lang, 'Guides', '指南'))}
      <a href="${org.github}">GitHub</a>
    </nav>
    <div class="top-tools">
      ${langSwitch(lang, switchLinks)}
      <a class="nav-app" href="${appHref(lang, '/login', 'org_header')}">${t(lang, 'Open app', '打开工作台')}${arrowGlyph}</a>
    </div>
  </div>
</header>`
}

export const footer = (lang) => `
<footer class="bottom">
  <div class="wrap">
    <div class="foot-grid">
      <div class="foot-brand">
        <a class="brand" href="${localized(lang, '/')}">${brandMark(28)}<span class="wordmark">ZenStory AI</span></a>
        <p class="foot-desc">${esc(t(lang, org.tagline.en, org.tagline.zh))}</p>
        <p class="foot-desc">${t(lang, `Open-source tools for creating and adapting stories, ${esc(org.proof.license)}-licensed.`, `用于创作与改编故事的开源工具，${esc(org.proof.license)} 许可。`)}</p>
        <p><a href="${org.github}">GitHub${extGlyph}</a></p>
      </div>
      <nav aria-label="${t(lang, 'Footer: projects', '页脚：项目')}">
        <p class="foot-h">${t(lang, 'Projects', '项目')}</p>
        <ul>${projects.map((p) => `<li><a href="${localized(lang, `/${p.slug}`)}">${esc(p.slug === 'workbench' ? t(lang, p.name.en, p.name.zh) : p.name.en)}</a></li>`).join('')}</ul>
      </nav>
      <nav aria-label="${t(lang, 'Footer: learn', '页脚：学习')}">
        <p class="foot-h">${t(lang, 'Learn', '学习')}</p>
        <ul>
          <li><a href="${localized(lang, '/guides')}">${t(lang, 'Guides', '实用指南')}</a></li>
          <li><a href="${localized(lang, '/glossary')}">${t(lang, 'Glossary', '术语表')}</a></li>
          <li><a href="/llms.txt">llms.txt</a></li>
        </ul>
      </nav>
      <nav aria-label="${t(lang, 'Footer: workbench', '页脚：工作台')}">
        <p class="foot-h">${t(lang, 'Workbench', '工作台')}</p>
        <ul>
          <li><a href="${appHref(lang, '/login', 'org_footer')}">${t(lang, 'Open app', '打开工作台')}</a></li>
          <li><a href="/docs">${t(lang, 'Workbench docs', '工作台文档')}</a></li>
          <li><a href="/privacy-policy">${t(lang, 'Privacy policy', '隐私政策')}</a></li>
          <li><a href="/terms-of-service">${t(lang, 'Terms of service', '服务条款')}</a></li>
          <li><a href="mailto:support@zenstory.ai">${t(lang, 'Support: support@zenstory.ai', '客服：support@zenstory.ai')}</a></li>
        </ul>
      </nav>
    </div>
    <p class="copyright">© ${new Date().getUTCFullYear()} ZenStory AI</p>
  </div>
</footer>`

/**
 * A complete document. `route` is this page's own path (already localized);
 * `alternates` is `{en, zh}` of the two language paths, or null when the page
 * has no counterpart (then `switchLinks` must be given, e.g. in-page anchors).
 * `pageId` marks `<body data-page>` for a page with its own visual system; `fonts`
 * is the Google Fonts stylesheet that system loads.
 */
export const page = ({ lang, route, alternates = null, switchLinks = alternates, section = null, title, description, ogType = 'article', ld, body, head = '', pageId = null, fonts = FONTS }) => {
  if (!switchLinks) throw new Error(`${route}: a page needs alternates or switchLinks`)
  const other = lang === 'zh' ? 'en' : 'zh'
  return `<!doctype html>
<html lang="${LOCALE[lang]}" data-lang="${lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}">
<link rel="canonical" href="${SITE}${route}">
${alternates ? `<link rel="alternate" hreflang="en" href="${SITE}${alternates.en}">
<link rel="alternate" hreflang="zh-CN" href="${SITE}${alternates.zh}">
<link rel="alternate" hreflang="zh" href="${SITE}${alternates.zh}">
<link rel="alternate" hreflang="x-default" href="${SITE}${alternates.en}">
` : ''}<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta property="og:type" content="${ogType}">
<meta property="og:site_name" content="ZenStory AI">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:url" content="${SITE}${route}">
<meta property="og:image" content="${SITE}/brand/og-zenstory-ai.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="${OG_LOCALE[lang]}">
${alternates ? `<meta property="og:locale:alternate" content="${OG_LOCALE[other]}">\n` : ''}<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#081431">
<link rel="stylesheet" href="/org/org.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preload" as="style" href="${fonts}">
<link rel="stylesheet" href="${fonts}" media="print" onload="this.media='all'">
<noscript><link rel="stylesheet" href="${fonts}"></noscript>
${head}${jsonld({ '@context': 'https://schema.org', '@graph': ld })}
</head>
<body${pageId ? ` data-page="${esc(pageId)}"` : ''}>
<a class="skip" href="#main">${t(lang, 'Skip to content', '跳到正文')}</a>
${nav(lang, { section, switchLinks })}
<main id="main">
${body}
</main>
${footer(lang)}
${copyScript}
${guideSearchScript}
${langChoiceScript}
</body>
</html>
`
}
