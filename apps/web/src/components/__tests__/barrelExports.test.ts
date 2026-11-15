import { describe, expect, it } from 'vitest'
import * as inspirations from '../inspirations'

describe('barrel exports', () => {
  it('re-exports inspirations components', () => {
    expect(inspirations.InspirationDetailDialog).toBeDefined()
    expect(inspirations.InspirationGrid).toBeDefined()
    expect(inspirations.FeaturedInspirationsSection).toBeDefined()
    expect(inspirations.DashboardInspirationSuggestions).toBeDefined()
  })
})
