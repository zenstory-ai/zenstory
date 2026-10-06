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

  it('includes every generated topic and pagination route in both language registries', () => {
    const content = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../content')
    const topics = JSON.parse(readFileSync(path.join(content, 'guide-topics.json'), 'utf8')) as { slug: string }[]
    const guides = JSON.parse(readFileSync(path.join(content, 'guides.json'), 'utf8')) as { topic: string }[]
    const articles = JSON.parse(readFileSync(path.join(content, 'articles.json'), 'utf8')) as { topic: string; langs: string[] }[]
    const routes = orgPageRoutes()
    const expected: string[] = []
    for (const lang of ['en', 'zh']) {
      for (const topic of topics) {
        const items = [...guides, ...articles.filter(article => article.langs.includes(lang))]
        const pages = Math.max(1, Math.ceil(items.filter(item => item.topic === topic.slug).length / 24))
        for (let number = 1; number <= pages; number++) {
          const route = `${lang === 'zh' ? '/zh' : ''}/guides/${topic.slug}${number > 1 ? `/page/${number}` : ''}`
          expected.push(route)
          expect(routes.filter(candidate => candidate === route), route).toHaveLength(1)
        }
      }
    }
    const actual = routes.filter(route => /^\/(?:zh\/)?guides\/[^/]+(?:\/page\/\d+)?$/.test(route))
    expect(actual.sort()).toEqual(expected.sort())
  })

  it('does not invent English pagination for Chinese-only topic entries', () => {
    const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../content')
    const root = mkdtempSync(path.join(tmpdir(), 'zenstory-topic-languages-'))
    const content = path.join(root, 'content')
    cpSync(source, content, { recursive: true })
    try {
      const articles = Array.from({ length: 25 }, (_, index) => ({
        owner: 'oh-story', slug: `topic-language-${index}`, topic: 'plot-and-outline', langs: ['zh'],
      }))
      writeFileSync(path.join(content, 'guides.json'), '[]')
      writeFileSync(path.join(content, 'articles.json'), JSON.stringify(articles))
      const routes = orgPageRoutes(content)
      expect(routes).toContain('/guides/plot-and-outline')
      expect(routes).toContain('/zh/guides/plot-and-outline')
      expect(routes).toContain('/zh/guides/plot-and-outline/page/2')
      expect(routes).not.toContain('/guides/plot-and-outline/page/2')
      expect(routes).not.toContain('/zh/guides/plot-and-outline/page/3')
      expect(new Set(routes).size).toBe(routes.length)
    } finally { rmSync(root, { recursive: true, force: true }) }
  })

})
