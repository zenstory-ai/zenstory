import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test, { before, after } from 'node:test'

const projects = JSON.parse(readFileSync(new URL('../../content/projects.json', import.meta.url), 'utf8'))
const org = JSON.parse(readFileSync(new URL('../../content/org.json', import.meta.url), 'utf8'))
let out
before(() => {
  out = mkdtempSync(join(tmpdir(), 'zenstory-project-stars-'))
  const build = spawnSync(process.execPath, [new URL('../build-org-pages.mjs', import.meta.url).pathname, out], { encoding: 'utf8' })
  assert.equal(build.status, 0, build.stderr)
})
after(() => rmSync(out, { recursive: true, force: true }))

test('project stars have one shared dated snapshot, distinct from pinned source-review dates', () => {
  assert.equal(org.proof.stars_total, projects.reduce((sum, project) => sum + project.stars, 0))
  assert.match(org.proof.as_of, /^\d{4}-\d{2}-\d{2}$/)
  for (const project of projects) {
    assert.ok(Number.isSafeInteger(project.stars) && project.stars >= 0)
    for (const lang of ['en', 'zh']) {
      const html = readFileSync(join(out, lang === 'en' ? project.slug : `zh/${project.slug}`, 'index.html'), 'utf8')
      const snapshots = [...html.matchAll(/<span class="asof star-snapshot">([^<]+)<\/span>/g)]
      assert.equal(snapshots.length, 1, `${lang}/${project.slug}: one plain star date, not a second proof badge`)
      assert.ok(snapshots[0][1].includes(org.proof.as_of))
      assert.ok(html.includes(project.stars.toLocaleString('en-US')))
      assert.ok(html.includes(project.sources.checked_on), 'keep the actual source-review date')
      assert.doesNotMatch(html, /Star counts are a \d{4}-\d{2}-\d{2} snapshot|星标数为 \d{4}-\d{2}-\d{2} 快照/, 'legacy star date clauses must not conflict with the shared snapshot')
    }
  }
})
