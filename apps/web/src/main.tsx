import { StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './lib/i18n'
import App from './App.tsx'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PageLoader } from './components/PageLoader'
import { ErrorBoundary } from './components/ErrorBoundary'
import { initWebVitalsLogging, initWebVitalsMonitoring } from './lib/webVitals'
import { initAnalytics } from './lib/analytics'
import { captureAuthCallbackParams } from './lib/authCallbackParams'
import { rememberEntrySource } from './lib/entrySource'
import { installChunkRecoveryHandlers } from './lib/chunkRecovery'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      gcTime: 10 * 60 * 1000, // 10 minutes
      refetchOnWindowFocus: false,
      refetchOnMount: false,
      retry: 1,
    },
  },
})

// Pull OAuth credentials out of the URL before anything can report it.
captureAuthCallbackParams()
rememberEntrySource()
initAnalytics()
initWebVitalsMonitoring()
initWebVitalsLogging()
installChunkRecoveryHandlers()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary area="app" variant="page">
      <QueryClientProvider client={queryClient}>
        <Suspense fallback={<PageLoader />}>
          <App />
        </Suspense>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)
