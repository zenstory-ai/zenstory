import assert from 'node:assert/strict'
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
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

test('homepage corpus contains exactly five cases, four CDN videos, and four creative owners', () => {
  assert.equal(showcases.length, 5)
  assert.equal(showcases.filter((item) => item.video).length, 4)
  assert.deepEqual(
    new Set(showcases.map((item) => item.owner)),
    new Set(['oh-story', 'drama-skills', 'novel-to-game', 'video-recap']),
  )
  for (const [lang, html] of Object.entries(pages)) {
    const examples = section(html, 'examples-h')
    assert.equal(elementsWithClass(examples, 'case-card', 'article').length, 5, `${lang}: wrong rendered case count`)
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

test('generator rejects a missing article referenced by a homepage path', (t) => {
  const fixture = isolatedGenerator(t)
  mutateJson(fixture.content('home-reading.json'), (reading) => { reading.paths[0][5][0] = 'nonexistent-home-reading-slug' })
  const result = fixture.run()
  assert.notEqual(result.status, 0, 'unknown path article must not be silently filtered')
  assert.ok(!existsSync(join(fixture.outputDir, 'org-home/index.html')), 'generator must fail before publishing a homepage')
})

test('generator rejects a homepage path whose steps belong to a tool it does not name', (t) => {
  const fixture = isolatedGenerator(t)
  mutateJson(fixture.content('home-reading.json'), (reading) => { reading.paths[2][7] = ['oh-story'] })
  const result = fixture.run()
  assert.notEqual(result.status, 0, 'a path must not point readers at the wrong starting tool')
  assert.ok(!existsSync(join(fixture.outputDir, 'org-home/index.html')), 'generator must fail before publishing a homepage')
})

test('generator rejects a missing article referenced by a homepage knowledge level', (t) => {
  const fixture = isolatedGenerator(t)
  mutateJson(fixture.content('home-reading.json'), (reading) => { reading.levels[0][4][0] = 'nonexistent-home-reading-slug' })
  const result = fixture.run()
  assert.notEqual(result.status, 0, 'unknown knowledge article must not be silently filtered')
  assert.ok(!existsSync(join(fixture.outputDir, 'org-home/index.html')), 'generator must fail before publishing a homepage')
})

test('homepage presents the showcase and learning journey in the agreed order', () => {
  // Results first, then the visitor's material, then the tool for it, then reference.
  const ids = ['examples-h', 'start-h', 'choose-h', 'guides-h', 'model-h']
  for (const [lang, html] of Object.entries(pages)) {
    const positions = ids.map((id) => html.indexOf(`id="${id}"`))
    positions.forEach((position, index) => assert.ok(position >= 0, `${lang}: missing ${ids[index]}`))
    assert.deepEqual([...positions].sort((a, b) => a - b), positions, `${lang}: homepage section order changed`)
  }
})

test('homepage hero links to outcomes, the material paths and the hero case’s own setup', () => {
  const chainCase = showcases.find((item) => item.chain)
  for (const [lang, html] of Object.entries(pages)) {
    const prefix = lang === 'zh' ? '/zh' : ''
    const hero = html.slice(html.indexOf('<main'), html.indexOf('<section'))
    const heroLinks = anchors(hero)
    assert.ok(heroLinks.some(({ href }) => href === '#examples-h'), `${lang}: hero needs an outcomes link`)
    const primary = heroLinks.filter(({ tag }) => {
      const classes = attribute(tag, 'class')?.split(/\s+/) ?? []
      return classes.includes('btn') && !classes.includes('ghost')
    })
    assert.deepEqual(primary.map(({ href }) => href), ['#start-h'], `${lang}: the one primary hero action leads to the material paths`)
    assert.ok(html.includes('id="start-h"'), `${lang}: primary hero target is missing`)
    // The case on the stage gets its own next step, owned by the project that made it.
    const setup = `${prefix}/${chainCase.owner}#start-h`
    assert.ok(heroLinks.some(({ href }) => href === setup), `${lang}: hero case needs a link to ${setup}`)
    const owner = readFileSync(join(out, `${prefix}/${chainCase.owner}`.replace(/^\//, ''), 'index.html'), 'utf8')
    assert.ok(owner.includes('id="start-h"') && owner.includes(`zenstory-ai/${chainCase.owner}`), `${lang}: ${setup} must hold the install steps`)
    // No unrelated install command sits next to the case.
    assert.doesNotMatch(hero, /npx skills add/, `${lang}: hero must not carry a generic install command`)
  }
})

test('hero caption stays short: one case line and three actions, no setup manual', () => {
  const chainCase = showcases.find((item) => item.chain)
  for (const [lang, html] of Object.entries(pages)) {
    const prefix = lang === 'zh' ? '/zh' : ''
    const hero = html.slice(html.indexOf('<main'), html.indexOf('<section'))
    const caption = hero.match(/<figure class="stage">[\s\S]*<figcaption>([\s\S]*?)<\/figcaption>/)[1]
    assert.deepEqual(anchors(caption).map(({ href }) => href), [`${prefix}/${chainCase.owner}#start-h`, chainCase.chain.example, '#examples-h'], `${lang}: caption actions`)
    assert.ok(textOf(caption).length <= (lang === 'en' ? 90 : 40), `${lang}: caption is too long: ${textOf(caption)}`)
    // Host names, licence counts and environment explanations belong to the tool table, not the hero.
    assert.doesNotMatch(hero, /Antigravity|OpenClaw|Reasonix|MIT/, `${lang}: hero repeats the host and licence strip`)
    // The source chapter and its public-domain edition stay attached to the pinned source link.
    assert.ok(hero.includes(`href="${chainCase.chain.source.url}" title="${chainCase.chain.source.note[lang]}"`), `${lang}: source note left the pinned source link`)
  }
})

test('homepage next steps land where they say: case → path → tool row → install', () => {
  const projects = JSON.parse(readFileSync(new URL('../../content/projects.json', import.meta.url), 'utf8'))
  for (const [lang, html] of Object.entries(pages)) {
    const prefix = lang === 'zh' ? '/zh' : ''
    assert.ok(anchors(section(html, 'examples-h')).some(({ href }) => href === '#start-h'), `${lang}: after the cases, point to the material paths`)
    const choose = section(html, 'choose-h')
    const rows = elementsWithClass(choose, 'tool-choice', 'article')
    const rowIds = rows.map((row) => attribute(row.match(/<article\b[^>]*>/i)[0], 'id'))
    for (const path of elementsWithClass(section(html, 'start-h'), 'learning-path')) {
      const tools = anchors(elementsWithClass(path, 'path-tools')[0] ?? '')
      assert.ok(tools.length >= 1, `${lang}: every path names the tool it starts with`)
      for (const { href } of tools) assert.ok(rowIds.includes(href.slice(1)), `${lang}: path tool ${href} has no row in the tool table`)
    }
    const branches = elementsWithClass(section(html, 'start-h'), 'path-branches', 'ul')[0]
    for (const [route, name] of [['/drama-skills/', 'Drama Skills'], ['/novel-to-game/', 'Novel to Game']]) {
      const branch = anchors(branches).find(({ href }) => href.startsWith(`${prefix}${route}`))
      assert.ok(branch && branch.text.includes(name), `${lang}: adaptation branch must name ${name}`)
    }
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

test('homepage offers three crawlable paths for the visitor’s three input states', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const start = section(html, 'start-h')
    assert.ok(start, `${lang}: missing learning-path section`)
    const paths = elementsWithClass(start, 'learning-path')
    assert.equal(paths.length, 3, `${lang}: expected exactly three learning paths`)
    paths.forEach((path, index) => {
      assert.ok(textOf(path).length >= 12, `${lang}: learning path ${index + 1} needs a readable explanation`)
      const links = anchors(path).filter(({ href }) => href.startsWith('/') && !href.startsWith('/docs') && !href.includes('#'))
      assert.ok(links.length >= 1, `${lang}: learning path ${index + 1} needs an internal link`)
      for (const { href } of links) assert.ok(localPathExists(href), `${lang}: learning path ${index + 1} points to missing route ${href}`)
    })
  }
})

test('the idea path lets a visitor with no agent start writing in the browser workbench', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const paths = elementsWithClass(section(html, 'start-h'), 'learning-path')
    const signup = `https://app.zenstory.ai/register?lang=${lang}&amp;source=org_home_path`
    assert.equal(paths[0].split(`href="${signup}"`).length - 1, 1, `${lang}: the idea path needs one direct workbench start`)
    // The workbench writes novels and scripts; adaptation and recap paths start with their skill packs.
    for (const path of paths.slice(1)) assert.ok(!path.includes(signup), `${lang}: only paths the workbench can start offer it`)
  }
})

test('manuscript path keeps continuation ordered and presents drama or game as alternatives', () => {
  for (const [lang, html] of Object.entries(pages)) {
    const paths = elementsWithClass(section(html, 'start-h'), 'learning-path')
    const manuscript = paths[1]
    const orderedLists = manuscript.match(/<ol\b[^>]*>[\s\S]*?<\/ol>/gi) ?? []
    const branchLists = manuscript.match(/<ul\b[^>]*\bclass=(?:"[^"]*\bpath-branches\b[^"]*"|'[^']*\bpath-branches\b[^']*')[^>]*>[\s\S]*?<\/ul>/gi) ?? []
    assert.equal(orderedLists.length, 1, `${lang}: manuscript path needs one ordered continuation list`)
    assert.equal((orderedLists[0].match(/<li\b/gi) ?? []).length, 1, `${lang}: continuation list must contain one step`)
    assert.equal(branchLists.length, 1, `${lang}: manuscript path needs one alternatives list`)
    assert.equal((branchLists[0].match(/<li\b/gi) ?? []).length, 2, `${lang}: alternatives list must contain drama and game`)
    assert.match(textOf(manuscript), lang === 'en' ? /choose one/i : /任选一条/, `${lang}: missing branch instruction`)
    const prefix = lang === 'zh' ? '/zh' : ''
    for (const route of [`${prefix}/drama-skills/novel-to-short-drama`, `${prefix}/novel-to-game/quick-start`]) {
      assert.ok(!orderedLists[0].includes(`href="${route}"`), `${lang}: adaptation route must not appear as a sequential step`)
      assert.ok(branchLists[0].includes(`href="${route}"`), `${lang}: adaptation route must appear as an alternative`)
    }
    for (const path of [paths[0], paths[2]]) {
      assert.equal((path.match(/<ol\b[^>]*>[\s\S]*?<\/ol>/gi) ?? []).length, 1, `${lang}: sequential path needs one ordered list`)
      assert.equal((path.match(/<li\b/gi) ?? []).length, 3, `${lang}: sequential path must keep three steps`)
      assert.doesNotMatch(path, /\bpath-branches\b/, `${lang}: sequential path must not render alternatives`)
    }
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
      const links = anchors(level).filter(({ href }) => href.startsWith('/') && !href.includes('#'))
      assert.ok(links.length >= 1, `${lang}: knowledge level ${index + 1} needs a crawlable route`)
      for (const { href } of links) assert.ok(localPathExists(href), `${lang}: knowledge level ${index + 1} points to missing route ${href}`)
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
