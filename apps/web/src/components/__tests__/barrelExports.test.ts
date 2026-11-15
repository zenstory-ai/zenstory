import { describe, expect, it } from 'vitest'
import * as inspirations from '../inspirations'

describe('barrel exports', () => {
  it('re-exports inspirations components', () => {
    expect(inspirations.InspirationCard).toBeDefined()
    expect(inspirations.InspirationDetailDialog).toBeDefined()
    expect(inspirations.InspirationGrid).toBeDefined()
  })
})
