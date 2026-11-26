import { describe, expect, it } from 'vitest'

import { docsNavigation, flattenDocs } from '../docsNavigation'

describe('docsNavigation', () => {
  it('defines the expected top-level docs sections with localized titles', () => {
    expect(docsNavigation).toHaveLength(5)
    expect(docsNavigation.map((item) => item.path)).toEqual([
      '/docs/getting-started',
      '/docs/user-guide',
      '/docs/advanced',
      '/docs/reference',
      '/docs/troubleshooting',
    ])

    docsNavigation.forEach((item) => {
      expect(item.title).toBeTruthy()
      expect(item.titleZh).toBeTruthy()
      expect(item.children?.length).toBeGreaterThan(0)
    })
  })

  it('flattens the nested docs tree in parent-first order', () => {
    const flattened = flattenDocs(docsNavigation)

    expect(flattened).toHaveLength(28)
    expect(flattened[0]?.path).toBe('/docs/getting-started')
    expect(flattened.at(-1)?.path).toBe('/docs/troubleshooting/error-messages')

    const gettingStartedIndex = flattened.findIndex((item) => item.path === '/docs/getting-started')
    const quickStartIndex = flattened.findIndex((item) => item.path === '/docs/getting-started/quick-start')
    const userGuideIndex = flattened.findIndex((item) => item.path === '/docs/user-guide')
    const editorIndex = flattened.findIndex((item) => item.path === '/docs/user-guide/editor')

    expect(gettingStartedIndex).toBeLessThan(quickStartIndex)
    expect(userGuideIndex).toBeLessThan(editorIndex)
  })

  it('produces unique localized entries for every path in the tree', () => {
    const flattened = flattenDocs(docsNavigation)
    const uniquePaths = new Set(flattened.map((item) => item.path))

    expect(uniquePaths.size).toBe(flattened.length)
    expect(flattened.every((item) => item.title.trim().length > 0)).toBe(true)
    expect(flattened.every((item) => item.titleZh.trim().length > 0)).toBe(true)
  })
})
