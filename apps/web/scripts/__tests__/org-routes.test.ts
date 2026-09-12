import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { orgPageRoutes } from '../../vite.config'

describe('default-build organization sitemap routes', () => {
  it('includes task guides and the writing comparison once without exposing internal output paths', () => {
    const routes = orgPageRoutes()
    expect(new Set(routes).size).toBe(routes.length)
    for (const route of ['/novel-to-game/quick-start', '/video-recap/capcut-draft', '/compare/writing-workflows']) {
      expect(routes.filter(candidate => candidate === route)).toHaveLength(1)
    }
    expect(routes).not.toContain('/org-home')
    expect(routes).not.toContain('/compare')
    expect(routes).not.toContain('/_app')
  })

  it('rejects invalid comparison data before Vite can start writing output', () => {
    const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../content')
    const root = mkdtempSync(path.join(tmpdir(), 'zenstory-vite-comparisons-'))
    const content = path.join(root, 'content')
    cpSync(source, content, { recursive: true })
    const valid = JSON.parse(readFileSync(path.join(content, 'comparisons.json'), 'utf8'))[0]
    const replaceProject = (index: number, project: string) => ({
      ...valid,
      options: valid.options.map((option: { project: string }, candidate: number) => (
        candidate === index ? { ...option, project } : option
      )),
    })
    const cases = [
      [{ ...valid, slug: '../escape' }],
      [{ ...valid, slug: undefined }],
      [{ ...valid, slug: 123 }],
      [replaceProject(0, 'unknown')],
      [replaceProject(1, valid.options[0].project)],
      [valid, valid],
    ]

    try {
      for (const invalid of cases) {
        writeFileSync(path.join(content, 'comparisons.json'), JSON.stringify(invalid))
        expect(() => orgPageRoutes(content)).toThrow(/Invalid comparison identity|Invalid comparison options|Duplicate comparison route/)
      }
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
