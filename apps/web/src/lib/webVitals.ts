import { onCLS, onINP, onFCP, onLCP, onTTFB, type Metric } from 'web-vitals'
import logger from './logger'
import { trackEvent } from './analytics'

export interface WebVitalsEnv {
  DEV: boolean
  VITE_ENABLE_WEB_VITALS_LOGGING?: string
}

export type WebVitalReporter = (callback: (metric: Metric) => void) => void

interface InitWebVitalsLoggingInput {
  env?: WebVitalsEnv
  reporters?: readonly WebVitalReporter[]
  log?: (...args: unknown[]) => void
}

interface InitWebVitalsMonitoringInput {
  reporters?: readonly WebVitalReporter[]
  track?: (metric: Metric) => void
  /** Share of page loads that report web vitals (0-1). */
  sampleRate?: number
  random?: () => number
}

/** Web vitals are aggregate signals; 10% of page loads is plenty. */
export const WEB_VITALS_SAMPLE_RATE = 0.1

const DEFAULT_REPORTERS: readonly WebVitalReporter[] = [onCLS, onINP, onFCP, onLCP, onTTFB]

export const shouldEnableWebVitalsLogging = (env: WebVitalsEnv): boolean => {
  return env.DEV && env.VITE_ENABLE_WEB_VITALS_LOGGING === 'true'
}

export const formatWebVitalLogArgs = (metric: Metric): [string, string, number, string] => {
  return ['[web-vitals]', metric.name, metric.value, metric.rating]
}

export const formatWebVitalAnalyticsProps = (metric: Metric) => ({
  metric_name: metric.name,
  metric_value: metric.value,
  metric_rating: metric.rating,
  metric_delta: metric.delta,
  navigation_type: metric.navigationType,
})

export function initWebVitalsLogging({
  env = import.meta.env,
  reporters = DEFAULT_REPORTERS,
  log = (...args: unknown[]) => logger.debug(...args),
}: InitWebVitalsLoggingInput = {}): boolean {
  if (!shouldEnableWebVitalsLogging(env)) {
    return false
  }

  reporters.forEach((report) => {
    report((metric: Metric) => {
      log(...formatWebVitalLogArgs(metric))
    })
  })

  return true
}

export function initWebVitalsMonitoring({
  reporters = DEFAULT_REPORTERS,
  sampleRate = WEB_VITALS_SAMPLE_RATE,
  random = Math.random,
  track = (metric: Metric) => {
    trackEvent('web_vital', { ...formatWebVitalAnalyticsProps(metric), sample_rate: sampleRate })
  },
}: InitWebVitalsMonitoringInput = {}): boolean {
  // Decide once per page load so a sampled page reports all of its metrics.
  if (random() >= sampleRate) {
    return false
  }

  reporters.forEach((report) => {
    report((metric: Metric) => {
      track(metric)
    })
  })

  return true
}
