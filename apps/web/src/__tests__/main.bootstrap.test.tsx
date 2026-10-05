import { createRoot } from 'react-dom/client'
import { initAnalytics } from '../lib/analytics'
import { captureAuthCallbackParams } from '../lib/authCallbackParams'
import { installChunkRecoveryHandlers } from '../lib/chunkRecovery'
import { initWebVitalsLogging, initWebVitalsMonitoring } from '../lib/webVitals'

const { createRootMock, renderMock } = vi.hoisted(() => ({
  createRootMock: vi.fn(),
  renderMock: vi.fn(),
}))

vi.mock('react-dom/client', () => ({
  createRoot: createRootMock,
}))

vi.mock('../App.tsx', () => ({
  default: () => <div data-testid="app-root">App</div>,
}))

vi.mock('../components/PageLoader', () => ({
  PageLoader: () => <div data-testid="page-loader">Loading</div>,
}))

vi.mock('../lib/analytics', () => ({
  initAnalytics: vi.fn(),
}))

vi.mock('../lib/authCallbackParams', () => ({
  captureAuthCallbackParams: vi.fn(),
}))

vi.mock('../lib/webVitals', () => ({
  initWebVitalsLogging: vi.fn(),
  initWebVitalsMonitoring: vi.fn(),
}))

vi.mock('../lib/chunkRecovery', () => ({
  installChunkRecoveryHandlers: vi.fn(),
}))

describe('main bootstrap', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    document.body.innerHTML = '<div id="root"></div>'
    createRootMock.mockReturnValue({ render: renderMock })
  })

  it('initializes analytics, monitoring, and renders the root app', async () => {
    await import('../main.tsx')

    expect(initAnalytics).toHaveBeenCalledOnce()
    expect(initWebVitalsMonitoring).toHaveBeenCalledOnce()
    expect(initWebVitalsLogging).toHaveBeenCalledOnce()
    expect(installChunkRecoveryHandlers).toHaveBeenCalledOnce()
    expect(createRoot).toHaveBeenCalledWith(document.getElementById('root'))
    expect(renderMock).toHaveBeenCalledOnce()
  })

  it('scrubs OAuth callback credentials from the URL before analytics starts', async () => {
    await import('../main.tsx')

    expect(captureAuthCallbackParams).toHaveBeenCalledOnce()
    expect(vi.mocked(captureAuthCallbackParams).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(initAnalytics).mock.invocationCallOrder[0],
    )
  })
})
