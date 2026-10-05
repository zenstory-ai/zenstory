import { describe, expect, it, vi } from 'vitest'
import type { Metric } from 'web-vitals'
import {
  formatWebVitalLogArgs,
  formatWebVitalAnalyticsProps,
  initWebVitalsLogging,
  initWebVitalsMonitoring,
  WEB_VITALS_SAMPLE_RATE,
  shouldEnableWebVitalsLogging,
  type WebVitalReporter,
} from '../webVitals'

const createMetric = (name: Metric['name']): Metric => ({
  name,
  value: 0.05,
  rating: 'good',
  delta: 0.05,
  id: `${name}-id`,
  entries: [],
  navigationType: 'navigate',
})

describe('webVitals', () => {
  it('enables logging only when explicitly opted in', () => {
    expect(shouldEnableWebVitalsLogging({ DEV: true })).toBe(false)
    expect(
      shouldEnableWebVitalsLogging({ DEV: false, VITE_ENABLE_WEB_VITALS_LOGGING: 'true' }),
    ).toBe(false)
    expect(
      shouldEnableWebVitalsLogging({ DEV: true, VITE_ENABLE_WEB_VITALS_LOGGING: 'true' }),
    ).toBe(true)
  })

  it('registers each reporter and logs metrics when enabled', () => {
    const callbacks: Array<(metric: Metric) => void> = []
    const reporterOneSpy = vi.fn((callback: (metric: Metric) => void) => {
      callbacks.push(callback)
    })
    const reporterTwoSpy = vi.fn((callback: (metric: Metric) => void) => {
      callbacks.push(callback)
    })
    const logSpy = vi.fn()

    const didStart = initWebVitalsLogging({
      env: { DEV: true, VITE_ENABLE_WEB_VITALS_LOGGING: 'true' },
      reporters: [reporterOneSpy as WebVitalReporter, reporterTwoSpy as WebVitalReporter],
      log: logSpy,
    })

    expect(didStart).toBe(true)
    expect(reporterOneSpy).toHaveBeenCalledTimes(1)
    expect(reporterTwoSpy).toHaveBeenCalledTimes(1)

    callbacks.forEach((callback) => callback(createMetric('CLS')))
    expect(logSpy).toHaveBeenNthCalledWith(1, '[web-vitals]', 'CLS', 0.05, 'good')
    expect(logSpy).toHaveBeenNthCalledWith(2, '[web-vitals]', 'CLS', 0.05, 'good')
  })

  it('does not register reporters when logging is disabled', () => {
    const reporterSpy = vi.fn()
    const didStart = initWebVitalsLogging({
      env: { DEV: true, VITE_ENABLE_WEB_VITALS_LOGGING: 'false' },
      reporters: [reporterSpy as WebVitalReporter],
      log: vi.fn(),
    })

    expect(didStart).toBe(false)
    expect(reporterSpy).not.toHaveBeenCalled()
  })

  it('formats log args consistently', () => {
    expect(formatWebVitalLogArgs(createMetric('TTFB'))).toEqual(['[web-vitals]', 'TTFB', 0.05, 'good'])
  })

  it('formats analytics props consistently', () => {
    expect(formatWebVitalAnalyticsProps(createMetric('LCP'))).toEqual({
      metric_name: 'LCP',
      metric_value: 0.05,
      metric_rating: 'good',
      metric_delta: 0.05,
      navigation_type: 'navigate',
    })
  })

  it('registers reporters for analytics monitoring', () => {
    const callbacks: Array<(metric: Metric) => void> = []
    const reporterSpy = vi.fn((callback: (metric: Metric) => void) => {
      callbacks.push(callback)
    })
    const trackSpy = vi.fn()

    const didStart = initWebVitalsMonitoring({
      reporters: [reporterSpy as WebVitalReporter],
      track: trackSpy,
      random: () => 0,
    })

    expect(didStart).toBe(true)
    expect(reporterSpy).toHaveBeenCalledTimes(1)

    callbacks[0]?.(createMetric('INP'))
    expect(trackSpy).toHaveBeenCalledWith(createMetric('INP'))
  })

  it('samples page loads: unsampled pages register no reporters', () => {
    const reporterSpy = vi.fn()

    const didStart = initWebVitalsMonitoring({
      reporters: [reporterSpy as WebVitalReporter],
      track: vi.fn(),
      random: () => WEB_VITALS_SAMPLE_RATE,
    })

    expect(WEB_VITALS_SAMPLE_RATE).toBeLessThan(1)
    expect(didStart).toBe(false)
    expect(reporterSpy).not.toHaveBeenCalled()
  })
})
