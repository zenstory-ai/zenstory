import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../errorHandler', () => ({
  resolveApiErrorMessage: vi.fn(),
  toUserErrorMessage: vi.fn(),
}))
vi.mock('../logger', () => ({ logger: { log: vi.fn(), warn: vi.fn(), error: vi.fn() } }))

describe('API origin for isolated preview builds', () => {
  beforeEach(() => vi.resetModules())
  afterEach(() => vi.unstubAllEnvs())

  it('uses the serving origin in a deployed build when no API override is configured', async () => {
    vi.stubEnv('PROD', true)
    vi.stubEnv('VITE_API_BASE_URL', '')
    const { getApiBase } = await import('../apiClient')
    expect(getApiBase()).toBe(window.location.origin)
  })

  it('preserves an explicitly configured production API origin', async () => {
    vi.stubEnv('PROD', true)
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.zenstory.ai')
    const { getApiBase } = await import('../apiClient')
    expect(getApiBase()).toBe('https://api.zenstory.ai')
  })

  it('keeps the local development default', async () => {
    vi.stubEnv('PROD', false)
    vi.stubEnv('VITE_API_BASE_URL', '')
    const { getApiBase } = await import('../apiClient')
    expect(getApiBase()).toBe('http://localhost:8000')
  })
})
