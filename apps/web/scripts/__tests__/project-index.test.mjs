import assert from 'node:assert/strict'
import { cpSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const scriptsDir = resolve(here, '..')
const webRoot = resolve(scriptsDir, '..')
const projects = JSON.parse(readFileSync(join(webRoot, 'content/projects.json'), 'utf8'))
const org = JSON.parse(readFileSync(join(webRoot, 'content/org.json'), 'utf8'))
const generatorFiles = ['build-org-pages.mjs', 'site-shell.mjs', 'org-pages.css']

const build = (t) => {
  const root = mkdtempSync(join(tmpdir(), 'zenstory-project-index-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  cpSync(join(webRoot, 'content'), join(root, 'content'), { recursive: true })
  for (const file of generatorFiles) cpSync(join(scriptsDir, file), join(root, 'scripts', file), { recursive: true })
  const out = join(root, 'output')
  const result = spawnSync(process.execPath, [join(root, 'scripts/build-org-pages.mjs'), out], { cwd: root, encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  return out
}

const count = (html, pattern) => [...html.matchAll(pattern)].length
const body = (html) => html.slice(html.indexOf('<body'))

test('project catalog keeps every project destination, entry path, source and dated star snapshot', (t) => {
  const out = build(t)

  for (const lang of ['en', 'zh']) {
    const html = readFileSync(join(out, lang === 'en' ? 'projects' : 'zh/projects', 'index.html'), 'utf8')
    const page = body(html)
    assert.match(html, /<body data-page="projects">/)
    assert.equal(count(page, /<article class="project-entry">/g), projects.length)

    for (const project of projects) {
      assert.ok(page.includes(`href="/${lang === 'zh' ? 'zh/' : ''}${project.slug}"`), `${lang}: missing ${project.slug} page`)
      assert.ok(page.includes(`href="${project.github}"`), `${lang}: missing ${project.slug} source`)
      assert.ok(page.includes(project.stars.toLocaleString('en-US')), `${lang}: missing ${project.slug} stars`)
      if (project.install && project.entry) assert.ok(page.includes(`href="/${lang === 'zh' ? 'zh/' : ''}${project.slug}#start-h"`), `${lang}: missing ${project.slug} setup`)
    }

    assert.match(page, /href="https:\/\/app\.zenstory\.ai\/login\?[^"\s]*source=org_projects/)
    assert.match(page, /href="\/(?:zh\/)?compare\/writing-workflows"/)
    assert.equal(count(page, new RegExp(org.proof.as_of.replaceAll('-', '\\-'), 'g')), 1, `${lang}: snapshot date appears once`)
    assert.equal(projects.reduce((sum, project) => sum + project.stars, 0), org.proof.stars_total)
  }
})

test('project relationships and agent hosts remain semantic plain lists with their distinct boundaries', (t) => {
  const out = build(t)

  for (const lang of ['en', 'zh']) {
    const html = body(readFileSync(join(out, lang === 'en' ? 'projects' : 'zh/projects', 'index.html'), 'utf8'))
    const relations = html.match(/<dl class="project-relations">([\s\S]*?)<\/dl>/)?.[1]
    assert.ok(relations, `${lang}: missing relationship list`)
    assert.equal(count(relations, /<dt>/g), 3)
    assert.equal(count(relations, /<dd>/g), 3)
    assert.match(relations, /Drama Skills/)
    assert.match(relations, /Novel to Game/)
    assert.match(relations, /Video Recap Skills/)
    const adaptation = [...relations.matchAll(/<dd>([\s\S]*?)<\/dd>/g)][1]?.[1]
    assert.ok(adaptation, `${lang}: adaptation needs its own relationship row`)
    assert.match(adaptation, lang === 'en' ? /\bnovel\b[^.;]*Drama Skills/i : /小说[^；。]*Drama Skills/)
    assert.match(adaptation, lang === 'en' ? /\bnovel\b[^.;]*Novel to Game/i : /小说[^；。]*Novel to Game/)
    assert.match(adaptation, lang === 'en' ? /\bfootage\b[^.;]*Video Recap Skills/i : /视频素材[^；。]*Video Recap Skills/)
    assert.match(relations, lang === 'en' ? /may use the source/ : /使用权/)
    assert.match(relations, lang === 'en' ? /community plugin/ : /社区插件/)
    assert.match(relations, lang === 'en' ? /not something you add to DeepSeek's web chat/ : /不是装进 DeepSeek 网页聊天/)

    const hosts = html.match(/<ul class="project-hosts">([\s\S]*?)<\/ul>/)?.[1]
    assert.ok(hosts, `${lang}: missing host list`)
    assert.equal(count(hosts, /<li>/g), org.proof.harnesses.length)
    assert.doesNotMatch(hosts, /<a\b/)
    for (const host of org.proof.harnesses) assert.ok(hosts.includes(host), `${lang}: missing ${host}`)
  }
})
