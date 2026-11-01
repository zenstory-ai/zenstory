import assert from 'node:assert/strict'
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import test, { after, before } from 'node:test'

const languages = {
  en: {
    file: 'org-home/index.html',
    canonical: 'https://zenstory.ai/',
    guides: '/guides',
    knowledgeLabels: [/get(?:ting)? started|beginner|start here/i, /workflow/i, /technique|template/i, /troubleshoot|reference/i],
  },
  zh: {
    file: 'zh/index.html',
    canonical: 'https://zenstory.ai/zh',
    guides: '/zh/guides',
    knowledgeLabels: [/入门/, /工作流/, /技法|模板/, /排错|参考/],
  },
}

const topics = JSON.parse(readFileSync(new URL('../../content/guide-topics.json', import.meta.url), 'utf8'))
const showcases = JSON.parse(readFileSync(new URL('../../content/showcases.json', import.meta.url), 'utf8'))
const publicRoot = fileURLToPath(new URL('../../public/', import.meta.url))
const githubAttachment = /^https:\/\/github\.com\/user-attachments\/assets\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i

let out
let pages

before(() => {
  out = mkdtempSync(join(tmpdir(), 'zenstory-homepage-showcase-'))
  const result = spawnSync(process.execPath, [new URL('../build-org-pages.mjs', import.meta.url).pathname, out], {
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, result.stderr)
  pages = Object.fromEntries(
    Object.entries(languages).map(([lang, config]) => [lang, readFileSync(join(out, config.file), 'utf8')]),
  )
})

after(() => rmSync(out, { recursive: true, force: true }))

const attribute = (tag, name) => {
  const match = tag.match(new RegExp(`\\b${name}=(?:"([^"]*)"|'([^']*)')`, 'i'))
  return match?.[1] ?? match?.[2]
}

// Text extraction for trusted, generated fixture fragments; this is not an HTML sanitizer.
const textOf = (html) => html
  .replace(/<[^>]+>/g, ' ')
  .replace(/&(?:nbsp|#160);/gi, ' ')
  .replace(/&(?:amp|#38);/gi, '&')
  .replace(/&(?:lt|#60);/gi, '<')
  .replace(/&(?:gt|#62);/gi, '>')
  .replace(/&(?:quot|#34);/gi, '"')
  .replace(/&#39;|&apos;/gi, "'")
  .replace(/\s+/g, ' ')
  .trim()

const section = (html, id) => {
  const opening = new RegExp(`<section\\b[^>]*\\baria-labelledby=(?:"${id}"|'${id}')[^>]*>`, 'i').exec(html)
  if (!opening) return undefined
  const remainder = html.slice(opening.index + opening[0].length)
  const nextBand = remainder.search(/<section\b[^>]*\bclass=(?:"[^"]*\bband\b[^"]*"|'[^']*\bband\b[^']*')/i)
  const mainEnd = remainder.search(/<\/main>/i)
  const end = nextBand >= 0 ? nextBand : mainEnd >= 0 ? mainEnd : remainder.length
  return html.slice(opening.index, opening.index + opening[0].length + end)
}

const elementsWithClass = (html, className, requiredElement) => {
  const matches = []
  for (const match of html.matchAll(/<([a-z][\w-]*)\b[^>]*>/gi)) {
    const element = match[1].toLowerCase()
    if (requiredElement && element !== requiredElement) continue
    const classes = attribute(match[0], 'class')?.split(/\s+/) ?? []
    if (!classes.includes(className)) continue
    const end = html.indexOf(`</${element}>`, match.index + match[0].length)
    assert.ok(end >= 0, `unclosed ${element}.${className}`)
    matches.push(html.slice(match.index, end + element.length + 3))
  }
  return matches
}

const anchors = (html) => [...html.matchAll(/<a\b[^>]*\bhref=(?:"[^"]+"|'[^']+')[^>]*>[\s\S]*?<\/a>/gi)].map((match) => ({
  tag: match[0],
  href: attribute(match[0], 'href'),
  text: textOf(match[0]),
}))

const localPathExists = (href) => {
  const path = href.split(/[?#]/, 1)[0].replace(/^\/+|\/+$/g, '')
  const file = path ? join(out, path, 'index.html') : join(out, 'org-home/index.html')
  return existsSync(file)
}

const isSameLanguageMethod = (lang, href) => {
  if (lang === 'zh') return /^\/zh\/[^/?#]+\/[^/?#]+\/?$/.test(href) && !/^\/zh\/(?:guides|projects|compare)\//.test(href)
  return /^\/(?!zh\/)[^/?#]+\/[^/?#]+\/?$/.test(href) && !/^\/(?:guides|projects|compare)\//.test(href)
}

const isolatedGenerator = (t) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-homepage-invalid-'))
  const contentDir = join(root, 'content')
  const scriptsDir = join(root, 'scripts')
  const outputDir = join(root, 'output')
  t.after(() => rmSync(root, { recursive: true, force: true }))
  cpSync(fileURLToPath(new URL('../../content/', import.meta.url)), contentDir, { recursive: true })
  mkdirSync(scriptsDir, { recursive: true })
  for (const file of ['build-org-pages.mjs', 'site-shell.mjs', 'org-pages.css']) {
    copyFileSync(fileURLToPath(new URL(`../${file}`, import.meta.url)), join(scriptsDir, file))
  }
  return {
    content: (name) => join(contentDir, name),
    outputDir,
    run: () => spawnSync(process.execPath, [join(scriptsDir, 'build-org-pages.mjs'), outputDir], { encoding: 'utf8' }),
  }
}

const mutateJson = (file, mutate) => {
  const data = JSON.parse(readFileSync(file, 'utf8'))
  mutate(data)
  writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`)
}

test('homepage generator emits English and Chinese entry documents', () => {
  for (const config of Object.values(languages)) assert.ok(existsSync(join(out, config.file)), config.file)
})

test('browser language selection is opt-in only on the English home, before the body', () => {
  const marker = '<script data-home-language>'
  assert.ok(pages.en.includes(marker), 'English root entry needs language selection')
  assert.ok(pages.en.indexOf(marker) < pages.en.indexOf('<body'), 'choose a language before rendering the page content')
  assert.ok(!pages.zh.includes(marker), 'an explicit Chinese URL must keep its language')
  for (const route of ['projects', 'zh/projects', 'guides', 'oh-story']) {
    const html = readFileSync(join(out, route, 'index.html'), 'utf8')
    assert.ok(!html.includes(marker), route)
    assert.doesNotMatch(html, /href="\/\?lang=en"[^>]*hreflang="en"/, `${route}: only the root language entry needs an explicit English hint`)
  }
  for (const html of Object.values(pages)) {
    assert.match(html, /href="\/\?lang=en"[^>]*hreflang="en"/, 'the English home link must work even when cookies cannot be saved')
  }
})

test('the project relationship diagram is directly readable without a disclosure', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const relationships = section(html, 'model-h')
    assert.match(relationships, /<svg\b/)
    assert.doesNotMatch(relationships, /<details\b|<summary\b/, `${lang}: the overview must not require another click`)
  }
})

test('homepage corpus contains exactly five cases, four CDN videos, and four creative owners; the hero case is not repeated below', () => {
  assert.equal(showcases.length, 5)
  assert.equal(showcases.filter((item) => item.video).length, 4)
  assert.deepEqual(
    new Set(showcases.map((item) => item.owner)),
    new Set(['oh-story', 'drama-skills', 'novel-to-game', 'video-recap']),
  )
  for (const [lang, html] of Object.entries(pages)) {
    const examples = section(html, 'examples-h')
    // The hero stage is the case with a verified chain; the showcase carries the other four.
    assert.equal(elementsWithClass(examples, 'case-card', 'article').length, 4, `${lang}: wrong rendered case count`)
    assert.ok(!examples.includes(`id="case-${showcases.find((item) => item.chain).slug}"`), `${lang}: the hero case repeats in the showcase`)
    assert.equal((examples.match(/<video\b/gi) ?? []).length, 4, `${lang}: wrong rendered video count`)
  }
})

test('generator rejects a showcase method that does not resolve to a published page', (t) => {
  const fixture = isolatedGenerator(t)
  mutateJson(fixture.content('showcases.json'), (items) => { items[0].method = '/novel-to-game/nonexistent' })
  const result = fixture.run()
  assert.notEqual(result.status, 0, 'invalid showcase method must not fall back to its project page')
  assert.ok(!existsSync(join(fixture.outputDir, 'org-home/index.html')), 'generator must fail before publishing a homepage')
})

test('generator rejects a tool without a homepage role instead of silently dropping it', (t) => {
  const fixture = isolatedGenerator(t)
  mutateJson(fixture.content('home-reading.json'), (reading) => { delete reading.tools['oh-story'] })
  const result = fixture.run()
  assert.notEqual(result.status, 0)
  assert.ok(!existsSync(join(fixture.outputDir, 'org-home/index.html')))
})

test('homepage removes the duplicate material paths and hero evidence drawer', () => {
  for (const [lang, html] of Object.entries(pages)) {
    assert.equal(section(html, 'start-h'), undefined, `${lang}: the duplicate step navigation must not remain`)
    assert.equal(elementsWithClass(html, 'stage-evidence', 'details').length, 0)
    assert.equal(elementsWithClass(html, 'home-paths').length, 0)
  }
})

test('generator rejects a missing article referenced by a homepage knowledge level', (t) => {
  const fixture = isolatedGenerator(t)
  mutateJson(fixture.content('home-reading.json'), (reading) => { reading.levels[0][4][0] = 'nonexistent-home-reading-slug' })
  const result = fixture.run()
  assert.notEqual(result.status, 0, 'unknown knowledge article must not be silently filtered')
  assert.ok(!existsSync(join(fixture.outputDir, 'org-home/index.html')), 'generator must fail before publishing a homepage')
})

test('homepage presents the showcase and learning journey in the agreed order', () => {
  // Results → relationships → one tool catalog → learning references.
  const ids = ['examples-h', 'model-h', 'choose-h', 'guides-h']
  for (const [lang, html] of Object.entries(pages)) {
    const positions = ids.map((id) => html.indexOf(`id="${id}"`))
    positions.forEach((position, index) => assert.ok(position >= 0, `${lang}: missing ${ids[index]}`))
    assert.deepEqual([...positions].sort((a, b) => a - b), positions, `${lang}: homepage section order changed`)
  }
})

test('homepage hero has one primary action to the tool catalog and a secondary outcomes link', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const hero = html.slice(html.indexOf('<main'), html.indexOf('<section'))
    const heroLinks = anchors(hero)
    assert.ok(heroLinks.some(({ href }) => href === '#examples-h'), `${lang}: hero needs an outcomes link`)
    const primary = heroLinks.filter(({ tag }) => (attribute(tag, 'class')?.split(/\s+/) ?? []).includes('btn'))
    assert.deepEqual(primary.map(({ href }) => href), ['#choose-h'])
    assert.ok(html.includes('id="choose-h"'), `${lang}: primary action target is missing`)
    assert.doesNotMatch(hero, /npx skills add/)
  }
})

test('hero sources are attached to existing labels, not a separate directory', () => {
  const chainCase = showcases.find((item) => item.chain)
  for (const [lang, html] of Object.entries(pages)) {
    const hero = html.slice(html.indexOf('<main'), html.indexOf('<section'))
    const caption = hero.match(/<figure class="stage">[\s\S]*<figcaption>([\s\S]*?)<\/figcaption>/)[1]
    assert.deepEqual(anchors(caption).map(({ href }) => href), [chainCase.chain.example, chainCase.demo])
    assert.ok(textOf(caption).length <= (lang === 'en' ? 90 : 40), `${lang}: keep one case line and one playable action`)
    assert.ok(hero.includes(`href="${chainCase.chain.source.url}" title="${chainCase.chain.source.note[lang]}"`))
    const art = anchors(hero).find(({ href }) => href === chainCase.chain.scene.source)
    assert.match(art.tag, /class="tile-media"/, `${lang}: link the artwork itself to the pinned original`)
    assert.ok(art.tag.includes(`src="${chainCase.chain.scene.file}"`))
    assert.match(textOf(caption), lang === 'zh' ? /原画/ : /art/i, `${lang}: the existing caption must identify art rather than live gameplay`)
    assert.doesNotMatch(hero, /Antigravity|OpenClaw|Reasonix|MIT/)
  }
})

test('homepage tool rows link to their real project installation steps', () => {
  const projects = JSON.parse(readFileSync(new URL('../../content/projects.json', import.meta.url), 'utf8'))
  for (const [lang, html] of Object.entries(pages)) {
    const prefix = lang === 'zh' ? '/zh' : ''
    const choose = section(html, 'choose-h')
    const rows = elementsWithClass(choose, 'tool-choice', 'article')
    for (const row of rows) {
      const slug = attribute(row.match(/<article\b[^>]*>/i)[0], 'id').replace(/^tool-/, '')
      const project = projects.find((p) => p.slug === slug)
      if (!project.install) continue
      const setup = anchors(row).find(({ href }) => href === `${prefix}/${slug}#start-h`)
      assert.ok(setup, `${lang}: ${slug} row needs a link to its install steps`)
      const landing = readFileSync(join(out, `${prefix}/${slug}`.replace(/^\//, ''), 'index.html'), 'utf8')
      assert.ok(landing.includes('id="start-h"') && landing.includes(project.install.split(' ').slice(0, 4).join(' ')), `${lang}: ${slug}#start-h must show the install command`)
    }
  }
})

test('tool selection keeps six project titles without redundant group headings', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const choose = section(html, 'choose-h')
    const creative = elementsWithClass(choose, 'tool-grid')[0]
    const environments = elementsWithClass(choose, 'environment-grid')[0]
    assert.equal(elementsWithClass(creative, 'tool-choice', 'article').length, 4)
    assert.equal(elementsWithClass(environments, 'tool-choice', 'article').length, 2)
    assert.equal((choose.match(/<h3\b/g) ?? []).length, 6, `${lang}: only the six project names need subheadings`)
    assert.equal(elementsWithClass(choose, 'count').length, 0, `${lang}: no decorative group totals`)
  }
})

test('knowledge layers say where their more-link lands, and the anchor exists', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const levels = elementsWithClass(section(html, 'guides-h'), 'knowledge-level')
    const more = levels.map((level) => anchors(level).find(({ tag }) => (attribute(tag, 'class') ?? '').split(/\s+/).includes('topic-more')))
    assert.equal(new Set(more.map(({ text }) => text)).size, levels.length, `${lang}: each layer needs its own more-link label`)
    for (const { href } of more) {
      const [path, fragment] = href.split('#')
      assert.ok(localPathExists(path), `${lang}: missing ${path}`)
      if (fragment) assert.ok(readFileSync(join(out, path.replace(/^\/+/, ''), 'index.html'), 'utf8').includes(`id="${fragment}"`), `${lang}: ${href} has no target`)
    }
    assert.ok(!more[2].href.endsWith('/guides/ai-video'), `${lang}: techniques span more than the AI-video category`)
    assert.match(more[3].text, lang === 'en' ? /term|glossary/i : /术语/, `${lang}: the glossary link must say it looks up terms`)
  }
})

test('homepage hero shows a real outcome image instead of only describing results', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const hero = html.slice(html.indexOf('<main'), html.indexOf('<section'))
    const images = hero.match(/<img\b[^>]*>/gi) ?? []
    assert.ok(images.length >= 1, `${lang}: hero needs a visible outcome image`)
    assert.ok(images.some((image) => {
      const source = attribute(image, 'src') ?? ''
      return source && !source.endsWith('/brand/zenstory-ai-mark.svg') && (attribute(image, 'alt') ?? '').trim()
    }), `${lang}: hero image must be an actual described outcome, not the brand mark`)
  }
})

test('hero connects a visible original passage to an honestly labelled game detail', () => {
  const item = showcases.find((candidate) => candidate.chain)
  const scene = item.chain.scene
  assert.equal(createHash('sha256').update(readFileSync(join(publicRoot, scene.file))).digest('hex'), scene.asset_sha256, 'the local art must match its source receipt')
  assert.equal(scene.asset_sha256, scene.source_sha256, 'the game environment art must remain unmodified')
  for (const [lang, html] of Object.entries(pages)) {
    const hero = html.slice(html.indexOf('<main'), html.indexOf('<section'))
    const figure = hero.slice(hero.indexOf('<figure class="stage">'), hero.indexOf('</figure>'))
    assert.ok(textOf(figure).includes(item.chain.source.excerpt.split('。')[0]), `${lang}: the original must be visible beside the outcome, not only in a closed disclosure`)
    assert.match(figure, /<blockquote\b[^>]*lang="zh-Hant"/)
    assert.match(textOf(figure), lang === 'zh' ? /原画/ : /art/i)
    assert.ok(figure.includes(`src="${item.chain.scene.file}"`), `${lang}: use the project’s actual environment art, not a synthetic brand image`)
    assert.doesNotMatch(figure, /story-game-battle\.jpg|wukong\.webp/, `${lang}: no raw battle HUD or floating character in the hero`)
    assert.ok(anchors(hero).some(({ href }) => href === item.chain.scene.source), `${lang}: cropped presentation must retain its full original source`)
  }
})

test('knowledge references retain native disclosures and crawlable sources', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const levels = elementsWithClass(section(html, 'guides-h'), 'knowledge-level', 'details')
    assert.equal(levels.length, 4, `${lang}: keep all four reference layers in native disclosures`)
    for (const level of levels) {
      assert.match(level, /^<details\b[^>]*>/)
      assert.doesNotMatch(level.match(/^<details\b[^>]*>/)[0], /\bopen(?:[\s=>])/)
      assert.match(level, /<summary>[\s\S]*?<h3\b/)
      assert.ok(anchors(level).length > 0, `${lang}: disclosure must retain crawlable method links`)
    }
  }
})

test('homepage showcases at least three real cases with readable evidence and reproducible methods', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const examples = section(html, 'examples-h')
    assert.ok(examples, `${lang}: missing examples section`)
    const cards = elementsWithClass(examples, 'case-card', 'article')
    assert.ok(cards.length >= 3, `${lang}: expected at least three case cards`)
    for (const [index, card] of cards.entries()) {
      const title = card.match(/<h[23]\b[^>]*>[\s\S]*?<\/h[23]>/i)?.[0]
      const summary = card.match(/<p\b[^>]*>[\s\S]*?<\/p>/i)?.[0]
      assert.ok(textOf(title ?? '').length >= 3, `${lang} case ${index + 1}: missing readable title`)
      assert.ok(textOf(summary ?? '').length >= 12, `${lang} case ${index + 1}: missing readable summary`)
      const links = anchors(card)
      const methods = links.filter(({ href }) => isSameLanguageMethod(lang, href))
      assert.ok(methods.length >= 1, `${lang} case ${index + 1}: missing same-language method link`)
      for (const { href } of methods) assert.ok(localPathExists(href), `${lang} case ${index + 1}: missing generated route ${href}`)
      assert.ok(links.some(({ href }) => /^https:\/\/github\.com\/zenstory-ai\//.test(href)), `${lang} case ${index + 1}: missing public GitHub source`)
    }
  }
})

test('every homepage case has a semantic figure with a readable caption', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const cards = elementsWithClass(section(html, 'examples-h'), 'case-card', 'article')
    assert.ok(cards.length >= 3, `${lang}: expected at least three case cards`)
    for (const [index, card] of cards.entries()) {
      const figure = card.match(/<figure\b[^>]*>[\s\S]*?<\/figure>/i)?.[0]
      assert.ok(figure, `${lang} case ${index + 1}: missing figure`)
      const caption = figure.match(/<figcaption\b[^>]*>[\s\S]*?<\/figcaption>/i)?.[0]
      assert.ok(textOf(caption ?? '').length >= 5, `${lang} case ${index + 1}: missing readable caption`)
    }
  }
})

test('homepage embeds at least three CDN videos with conservative native playback', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const examples = section(html, 'examples-h')
    const videos = [...examples.matchAll(/<video\b[^>]*>[\s\S]*?<\/video>/gi)].map((match) => match[0])
    assert.ok(videos.length >= 3, `${lang}: expected video in at least three cases`)
    for (const [index, video] of videos.entries()) {
      const opening = video.match(/<video\b[^>]*>/i)[0]
      assert.match(opening, /\bcontrols\b/i, `${lang} video ${index + 1}: missing controls`)
      assert.match(opening, /\bplaysinline\b/i, `${lang} video ${index + 1}: missing playsinline`)
      assert.equal(attribute(opening, 'preload'), 'none', `${lang} video ${index + 1}: preload must be none`)
      assert.doesNotMatch(opening, /\bautoplay\b/i, `${lang} video ${index + 1}: autoplay is forbidden`)
      const poster = attribute(opening, 'poster') ?? ''
      assert.match(poster, /^\/(?!\/)[^?#]+$/, `${lang} video ${index + 1}: poster must be a local path`)
      assert.ok(existsSync(join(publicRoot, poster.replace(/^\//, ''))), `${lang} video ${index + 1}: poster source is missing ${poster}`)
      const sources = video.match(/<source\b[^>]*>/gi) ?? []
      assert.ok(sources.length >= 1, `${lang} video ${index + 1}: missing source`)
      for (const source of sources) {
        assert.equal(attribute(source, 'type'), 'video/mp4', `${lang} video ${index + 1}: source must declare video/mp4`)
        assert.match(attribute(source, 'src') ?? '', githubAttachment, `${lang} video ${index + 1}: source must use the verified GitHub attachment CDN`)
      }
    }
  }
})

test('video cases expose descriptions, method pages, and direct-watch fallbacks in initial HTML', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const cards = elementsWithClass(section(html, 'examples-h'), 'case-card', 'article')
      .filter((card) => /<video\b/i.test(card))
    assert.ok(cards.length >= 3, `${lang}: expected at least three video cases`)
    for (const [index, card] of cards.entries()) {
      const summary = card.match(/<p\b[^>]*>[\s\S]*?<\/p>/i)?.[0]
      assert.ok(textOf(summary ?? '').length >= 12, `${lang} video case ${index + 1}: missing initial description`)
      const links = anchors(card)
      const methods = links.filter(({ href }) => isSameLanguageMethod(lang, href))
      assert.ok(methods.length >= 1, `${lang} video case ${index + 1}: missing method page`)
      for (const { href } of methods) assert.ok(localPathExists(href), `${lang} video case ${index + 1}: missing generated method ${href}`)
      const sourceUrls = (card.match(/<source\b[^>]*>/gi) ?? []).map((source) => attribute(source, 'src'))
      for (const sourceUrl of sourceUrls) {
        const fallback = links.find(({ href }) => href === sourceUrl)
        assert.ok(fallback && fallback.text.length >= 3, `${lang} video case ${index + 1}: missing readable direct-watch fallback`)
      }
    }
  }
})

test('articles removed from the homepage paths remain published and discoverable in the library', () => {
  const routes = [
    '/oh-story/agent-skills-for-writers', '/oh-story/novel-outline-template', '/oh-story/novel-opening',
    '/oh-story/import-and-continue', '/drama-skills/novel-to-short-drama', '/novel-to-game/quick-start',
    '/video-recap/video-to-narration', '/video-recap/original-audio-and-narration', '/video-recap/capcut-draft',
  ]
  for (const [lang, config] of Object.entries(languages)) {
    const index = readFileSync(join(out, config.guides.replace(/^\//, ''), 'index.html'), 'utf8')
    for (const route of routes) {
      const href = `${lang === 'zh' ? '/zh' : ''}${route}`
      assert.ok(localPathExists(href), `${lang}: removed shortcut must not remove ${href}`)
      assert.ok(anchors(index).some((link) => link.href === href), `${lang}: library must still expose ${href}`)
    }
  }
})

test('the showcase says how many cases a phone visitor can swipe through', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const cases = elementsWithClass(section(html, 'examples-h'), 'case-card', 'article').length
    const hint = section(html, 'examples-h').match(/<p class="swipe-hint" aria-hidden="true">([^<]+)</)?.[1] ?? ''
    assert.ok(hint.includes(String(cases)), `${lang}: swipe hint must count the ${cases} cases`)
  }
})

test('the workbench tool row provides the only direct homepage writing signup', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const row = elementsWithClass(section(html, 'choose-h'), 'tool-choice', 'article').find((row) => row.includes('id="tool-workbench"'))
    const signup = `https://app.zenstory.ai/register?lang=${lang}&amp;source=org_home_tools`
    assert.equal(anchors(row).filter(({ href }) => href === signup).length, 1)
    assert.equal(anchors(html).filter(({ href }) => href.startsWith('https://app.zenstory.ai/register?')).length, 1)
    assert.ok(anchors(row).some(({ href }) => href === `${lang === 'zh' ? '/zh' : ''}/workbench`), 'keep project details accessible')
  }
})

test('relationships explain the two material sources without repeating tool actions', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const model = section(html, 'model-h')
    assert.equal(anchors(model).length, 0, `${lang}: relationships explain; the tool catalog owns actions`)
    assert.doesNotMatch(model, /<ol\b|<details\b/)
    assert.match(model, lang === 'zh' ? /一部小说可以改成短剧或游戏/ : /A novel can become a short drama or a game/)
    assert.match(model, lang === 'zh' ? /现成的视频素材/ : /existing footage/)
  }
})

test('homepage condenses the knowledge system into four meaningful levels', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const guides = section(html, 'guides-h')
    assert.ok(guides, `${lang}: missing knowledge section`)
    const levels = elementsWithClass(guides, 'knowledge-level')
    assert.equal(levels.length, 4, `${lang}: expected four knowledge levels`)
    levels.forEach((level, index) => {
      assert.match(textOf(level), languages[lang].knowledgeLabels[index], `${lang}: knowledge level ${index + 1} has the wrong purpose`)
      const links = anchors(level).filter(({ href }) => href.startsWith('/')).map(({ href }) => href.split('#')[0])
      assert.ok(links.length >= 1, `${lang}: knowledge level ${index + 1} needs a crawlable route`)
      for (const href of links) assert.ok(localPathExists(href), `${lang}: knowledge level ${index + 1} points to missing route ${href}`)
    })

  }
})

test('homepage leaves the complete ten-category taxonomy on the guides index', () => {
  assert.equal(topics.length, 10, 'guide taxonomy should still contain ten categories')
  for (const [lang, html] of Object.entries(pages)) {
    assert.equal(elementsWithClass(html, 'topic-card').length, 0, `${lang}: homepage must not flatten all ten categories`)
    const index = readFileSync(join(out, languages[lang].guides.replace(/^\//, ''), 'index.html'), 'utf8')
    for (const topic of topics) {
      const href = `${languages[lang].guides}/${topic.slug}`
      assert.ok(index.includes(`href="${href}"`), `${lang}: guides index missing ${topic.slug}`)
    }
  }
})

test('homepage keeps indexable static HTML and bilingual SEO signals', () => {
  for (const [lang, html] of Object.entries(pages)) {
    assert.ok(html.length < 80000, `${lang}: homepage must stay below 80 kB`)
    assert.equal((html.match(/<h1\b/gi) ?? []).length, 1, `${lang}: homepage needs one H1`)
    assert.ok(html.includes(`<link rel="canonical" href="${languages[lang].canonical}">`), `${lang}: wrong canonical`)
    assert.ok(html.includes('<link rel="alternate" hreflang="en" href="https://zenstory.ai/">'), `${lang}: missing English alternate`)
    assert.ok(html.includes('<link rel="alternate" hreflang="zh-CN" href="https://zenstory.ai/zh">'), `${lang}: missing Chinese alternate`)
    assert.ok(html.includes('<link rel="alternate" hreflang="x-default" href="https://zenstory.ai/">'), `${lang}: missing default alternate`)
    assert.doesNotMatch(html, /<meta\b[^>]*\bcontent=(?:"[^"]*noindex|'[^']*noindex)/i, `${lang}: homepage must remain indexable`)
    assert.doesNotMatch(html, /<script\b[^>]*\bsrc=/i, `${lang}: homepage must not depend on a client-side bundle`)
  }
})

test('homepage outcome images declare dimensions, accessible text, and loading behavior', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const examples = section(html, 'examples-h')
    assert.ok(examples, `${lang}: missing examples section`)
    const hero = html.slice(html.indexOf('<main'), html.indexOf('<section'))
    for (const image of `${hero}${examples}`.match(/<img\b[^>]*>/gi) ?? []) {
      assert.match(attribute(image, 'width') ?? '', /^\d+$/, `${lang}: showcase image needs width`)
      assert.match(attribute(image, 'height') ?? '', /^\d+$/, `${lang}: showcase image needs height`)
      assert.ok((attribute(image, 'alt') ?? '').trim(), `${lang}: showcase image needs alt text`)
      assert.match(attribute(image, 'loading') ?? '', /^(?:lazy|eager)$/, `${lang}: showcase image needs loading behavior`)
    }
  }
})
