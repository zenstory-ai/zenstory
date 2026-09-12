import { describe, expect, it } from 'vitest'
import { orgPageRoutes } from '../../vite.config'

describe('default-build organization sitemap routes', () => {
  it('includes both task guides once without exposing internal output paths', () => {
    const routes = orgPageRoutes()
    expect(new Set(routes).size).toBe(routes.length)
    for (const route of ['/novel-to-game/quick-start', '/video-recap/capcut-draft']) {
      expect(routes.filter(candidate => candidate === route)).toHaveLength(1)
    }
    expect(routes).not.toContain('/org-home')
    expect(routes).not.toContain('/_app')
  })
})
