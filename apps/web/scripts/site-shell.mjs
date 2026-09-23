/**
 * Shared shell for every static page on zenstory.ai: head metadata, the
 * organization header and footer, and the language model.
 *
 * Language model: one language per URL. English pages live at the root
 * (`/oh-story`), Chinese pages under `/zh` (`/zh/oh-story`). Each page names
 * its counterpart with hreflang links; the header language switch is a plain
 * link to that counterpart. No inline language script, no localStorage.
 *
 * The workbench docs are the one exception: they stay on a single URL with the
 * Chinese article first and the English article below (see build-docs-pages).
 */
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const SITE = 'https://zenstory.ai'
export const APP = 'https://app.zenstory.ai'
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

export const FONTS = 'https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Plus+Jakarta+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap'

/** Header language switch: two links, the current language marked. */
export const langSwitch = (lang, links) => `<div class="lang-switch" role="group" aria-label="${t(lang, 'Language', '语言')}">
      <a href="${esc(links.en)}" lang="en" hreflang="en"${lang === 'en' ? ' aria-current="true"' : ''}>EN</a>
      <a href="${esc(links.zh)}" lang="zh-CN" hreflang="zh-CN"${lang === 'zh' ? ' aria-current="true"' : ''}>中文</a>
    </div>`

/**
 * Site header. `section` is the active top-level route ('/projects', '/guides',
 * '/glossary' or null); `switchLinks` are the two language destinations.
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
      ${item('/glossary', t(lang, 'Glossary', '术语'))}
      <a href="${org.github}">GitHub</a>
    </nav>
    <div class="top-tools">
      ${langSwitch(lang, switchLinks)}
      <a class="nav-app" href="${APP}">${t(lang, 'Open app', '打开工作台')}${arrowGlyph}</a>
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
        <ul>${projects.map((p) => `<li><a href="${localized(lang, `/${p.slug}`)}">${esc(p.name.en)}</a></li>`).join('')}</ul>
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
          <li><a href="${APP}">${t(lang, 'Open app', '打开工作台')}</a></li>
          <li><a href="/docs">${t(lang, 'Workbench docs', '工作台文档')}</a></li>
          <li><a href="/privacy-policy">${t(lang, 'Privacy policy', '隐私政策')}</a></li>
          <li><a href="/terms-of-service">${t(lang, 'Terms of service', '服务条款')}</a></li>
        </ul>
      </nav>
    </div>
    <p class="copyright">© ${new Date().getUTCFullYear()} ZenStory AI · ${t(lang, 'MIT-licensed open source', 'MIT 许可的开源项目')} · <a href="${org.github}">github.com/zenstory-ai</a> · <a href="/llms.txt">llms.txt</a></p>
  </div>
</footer>`

/**
 * A complete document. `route` is this page's own path (already localized);
 * `alternates` is `{en, zh}` of the two language paths, or null when the page
 * has no counterpart (then `switchLinks` must be given, e.g. in-page anchors).
 */
export const page = ({ lang, route, alternates = null, switchLinks = alternates, section = null, title, description, ogType = 'article', ld, body, head = '' }) => {
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
<link rel="preload" as="style" href="${FONTS}">
<link rel="stylesheet" href="${FONTS}" media="print" onload="this.media='all'">
<noscript><link rel="stylesheet" href="${FONTS}"></noscript>
${head}${jsonld({ '@context': 'https://schema.org', '@graph': ld })}
</head>
<body>
<a class="skip" href="#main">${t(lang, 'Skip to content', '跳到正文')}</a>
${nav(lang, { section, switchLinks })}
<main id="main">
${body}
</main>
${footer(lang)}
${copyScript}
</body>
</html>
`
}
