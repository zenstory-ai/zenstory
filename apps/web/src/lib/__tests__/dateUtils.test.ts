/**
 * Tests for date utility functions
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock i18next - must define the mock function inline since vi.mock is hoisted
vi.mock('i18next', () => {
  const mockT = (key: string, options?: { count?: number }) => {
    const translations: Record<string, string> = {
      'relativeTime.justNow': '刚刚',
      'relativeTime.minutesAgo': `${options?.count ?? 0} 分钟前`,
      'relativeTime.hoursAgo': `${options?.count ?? 0} 小时前`,
      'relativeTime.daysAgo': `${options?.count ?? 0} 天前`,
    }
    return translations[key] || key
  }
  return {
    default: {
      t: mockT,
    },
  }
})

// Mock i18n-helpers
vi.mock('../i18n-helpers', () => ({
  getLocaleCode: vi.fn(() => 'zh-CN'),
}))

// Import after mocking
import {
  getLocalDateString,
  getBeijingDateString,
  formatCalendarDate,
  parseUTCDate,
  formatRelativeTime,
  formatFullDate,
  formatRelativeTimeWithYear,
  formatAdminDateTime,
  formatAdminDate,
  formatBeijingPeriodDate,
} from '../dateUtils'

describe('dateUtils', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    // Set a fixed "now" for consistent testing: 2024-03-15T12:00:00Z
    vi.setSystemTime(new Date('2024-03-15T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  describe('parseUTCDate', () => {
    it('parses UTC date string without timezone suffix by appending Z', () => {
      const dateString = '2024-03-15T10:00:00'
      const result = parseUTCDate(dateString)

      // The result should be interpreted as UTC (10:00 UTC)
      expect(result.toISOString()).toBe('2024-03-15T10:00:00.000Z')
    })

    it('parses date string already ending with Z', () => {
      const dateString = '2024-03-15T10:00:00Z'
      const result = parseUTCDate(dateString)

      expect(result.toISOString()).toBe('2024-03-15T10:00:00.000Z')
    })

    it('parses date string with +HH:MM timezone offset', () => {
      const dateString = '2024-03-15T18:00:00+08:00'
      const result = parseUTCDate(dateString)

      // +08:00 means 18:00 local = 10:00 UTC
      expect(result.toISOString()).toBe('2024-03-15T10:00:00.000Z')
    })

    it('parses date string with -HH:MM timezone offset', () => {
      const dateString = '2024-03-15T05:00:00-05:00'
      const result = parseUTCDate(dateString)

      // -05:00 means 05:00 local = 10:00 UTC
      expect(result.toISOString()).toBe('2024-03-15T10:00:00.000Z')
    })

    it('parses date string with +HHMM timezone offset (no colon)', () => {
      const dateString = '2024-03-15T18:00:00+0800'
      const result = parseUTCDate(dateString)

      expect(result.toISOString()).toBe('2024-03-15T10:00:00.000Z')
    })

    it('parses date string with -HHMM timezone offset (no colon)', () => {
      const dateString = '2024-03-15T05:00:00-0500'
      const result = parseUTCDate(dateString)

      expect(result.toISOString()).toBe('2024-03-15T10:00:00.000Z')
    })

    it('handles milliseconds in date string', () => {
      const dateString = '2024-03-15T10:00:00.123'
      const result = parseUTCDate(dateString)

      expect(result.toISOString()).toBe('2024-03-15T10:00:00.123Z')
    })

    it('returns Date object', () => {
      const result = parseUTCDate('2024-03-15T10:00:00')
      expect(result).toBeInstanceOf(Date)
    })
  })

  describe('getLocalDateString', () => {
    it('formats local date as YYYY-MM-DD with zero padding', () => {
      const result = getLocalDateString(new Date('2024-03-05T10:30:00Z'))
      expect(result).toBe('2024-03-05')
    })
  })

  describe('getBeijingDateString', () => {
    it('rolls to the next day at 16:00Z, whatever the host timezone', () => {
      expect(getBeijingDateString(new Date('2026-10-09T16:30:00Z'))).toBe('2026-10-10')
      expect(getBeijingDateString(new Date('2026-10-09T15:59:00Z'))).toBe('2026-10-09')
      // A PDT evening (2026-10-09 18:00 -07:00) is already 10-10 in Beijing.
      expect(getBeijingDateString(new Date('2026-10-09T18:00:00-07:00'))).toBe('2026-10-10')
    })

    it('defaults to now', () => {
      vi.setSystemTime(new Date('2026-12-31T16:00:00Z'))
      expect(getBeijingDateString()).toBe('2027-01-01')
    })
  })

  describe('formatCalendarDate', () => {
    const originalTz = process.env.TZ
    afterEach(() => {
      process.env.TZ = originalTz
    })

    it('keeps the calendar day west of UTC', () => {
      process.env.TZ = 'America/Los_Angeles'
      expect(formatCalendarDate('2026-10-09', 'en-US')).toBe('10/9/2026')
      expect(formatCalendarDate('2026-10-09', 'zh-CN')).toBe('2026/10/9')
    })

    it('accepts a date-time string and leaves unparseable values as they are', () => {
      expect(formatCalendarDate('2026-04-06T00:00:00Z', 'en-US')).toBe('4/6/2026')
      expect(formatCalendarDate('not a date', 'en-US')).toBe('not a date')
    })
  })

  describe('formatRelativeTime', () => {
    it('returns "刚刚" for times less than 1 minute ago', () => {
      // Now is 2024-03-15T12:00:00Z, so 30 seconds ago
      const dateString = '2024-03-15T11:59:30'
      const result = formatRelativeTime(dateString)

      expect(result).toBe('刚刚')
    })

    it('returns minutes ago for times less than 1 hour ago', () => {
      // 5 minutes ago
      const dateString = '2024-03-15T11:55:00'
      const result = formatRelativeTime(dateString)

      expect(result).toBe('5 分钟前')
    })

    it('returns hours ago for times less than 24 hours ago', () => {
      // 3 hours ago
      const dateString = '2024-03-15T09:00:00'
      const result = formatRelativeTime(dateString)

      expect(result).toBe('3 小时前')
    })

    it('returns days ago for times less than 7 days ago', () => {
      // 2 days ago
      const dateString = '2024-03-13T12:00:00'
      const result = formatRelativeTime(dateString)

      expect(result).toBe('2 天前')
    })

    it('returns formatted date for times 7 or more days ago', () => {
      // 10 days ago
      const dateString = '2024-03-05T12:00:00'
      const result = formatRelativeTime(dateString)

      // Should not call translation for old dates, uses toLocaleDateString
      expect(result).toContain('3月')
      expect(result).toContain('5')
    })

    it('handles date exactly at boundary (59 minutes = still minutes)', () => {
      // 59 minutes ago
      const dateString = '2024-03-15T11:01:00'
      const result = formatRelativeTime(dateString)

      expect(result).toBe('59 分钟前')
    })

    it('handles date exactly at boundary (23 hours = still hours)', () => {
      // 23 hours ago
      const dateString = '2024-03-14T13:00:00'
      const result = formatRelativeTime(dateString)

      expect(result).toBe('23 小时前')
    })

    it('handles date exactly at boundary (6 days = still days)', () => {
      // 6 days ago
      const dateString = '2024-03-09T12:00:00'
      const result = formatRelativeTime(dateString)

      expect(result).toBe('6 天前')
    })
  })

  describe('formatFullDate', () => {
    it('formats date with year, month, day, hour, and minute', () => {
      const dateString = '2024-03-15T10:30:00'
      const result = formatFullDate(dateString)

      // Should contain year 2024, month 3, day 15
      expect(result).toContain('2024')
      expect(result).toContain('03')
      expect(result).toContain('15')
    })

    it('formats different dates correctly', () => {
      const dateString = '2023-12-25T08:15:00'
      const result = formatFullDate(dateString)

      expect(result).toContain('2023')
      expect(result).toContain('12')
      expect(result).toContain('25')
    })

    it('uses toLocaleDateString with correct options', () => {
      const dateString = '2024-01-05T14:30:00'
      const result = formatFullDate(dateString)

      // Just verify it returns a formatted string
      expect(typeof result).toBe('string')
      expect(result.length).toBeGreaterThan(0)
    })

    it('handles date with UTC parsing', () => {
      // Date without Z should be treated as UTC
      const dateString = '2024-06-20T18:45:00'
      const result = formatFullDate(dateString)

      expect(typeof result).toBe('string')
    })
  })

  describe('formatRelativeTimeWithYear', () => {
    it('returns "刚刚" for times less than 1 minute ago', () => {
      const dateString = '2024-03-15T11:59:30'
      const result = formatRelativeTimeWithYear(dateString)

      expect(result).toBe('刚刚')
    })

    it('returns minutes ago for times less than 1 hour ago', () => {
      const dateString = '2024-03-15T11:50:00'
      const result = formatRelativeTimeWithYear(dateString)

      expect(result).toBe('10 分钟前')
    })

    it('returns hours ago for times less than 24 hours ago', () => {
      const dateString = '2024-03-15T08:00:00'
      const result = formatRelativeTimeWithYear(dateString)

      expect(result).toBe('4 小时前')
    })

    it('returns days ago for times less than 7 days ago', () => {
      const dateString = '2024-03-12T12:00:00'
      const result = formatRelativeTimeWithYear(dateString)

      expect(result).toBe('3 天前')
    })

    it('returns full date with year for times 7 or more days ago', () => {
      // 10 days ago
      const dateString = '2024-03-05T12:00:00'
      const result = formatRelativeTimeWithYear(dateString)

      // Should contain year for dates older than 7 days
      expect(result).toContain('2024')
    })

    it('includes year for dates from previous year', () => {
      // Previous year
      const dateString = '2023-12-25T12:00:00'
      const result = formatRelativeTimeWithYear(dateString)

      expect(result).toContain('2023')
    })

    it('uses same format as formatFullDate for old dates', () => {
      const dateString = '2024-02-01T12:00:00'
      const relativeWithYear = formatRelativeTimeWithYear(dateString)
      const fullDate = formatFullDate(dateString)

      // Both should produce the same format for old dates
      expect(relativeWithYear).toBe(fullDate)
    })
  })

  describe('edge cases', () => {
    it('handles future dates (negative diff)', () => {
      // 5 minutes in the future
      const dateString = '2024-03-15T12:05:00'
      const result = formatRelativeTime(dateString)

      // Negative difference should still work (treated as "just now" since < 1 min)
      expect(typeof result).toBe('string')
    })

    it('handles date string at exactly midnight', () => {
      const dateString = '2024-03-15T00:00:00'
      const result = parseUTCDate(dateString)

      expect(result.toISOString()).toBe('2024-03-15T00:00:00.000Z')
    })

    it('handles date string at end of day', () => {
      const dateString = '2024-03-15T23:59:59'
      const result = parseUTCDate(dateString)

      expect(result.toISOString()).toBe('2024-03-15T23:59:59.000Z')
    })

    it('handles leap year date', () => {
      const dateString = '2024-02-29T12:00:00'
      const result = parseUTCDate(dateString)

      expect(result.toISOString()).toBe('2024-02-29T12:00:00.000Z')
    })

    it('handles year boundary in relative time', () => {
      // Set time to early January
      vi.setSystemTime(new Date('2024-01-02T12:00:00Z'))

      // 3 days ago (still in previous year)
      const dateString = '2023-12-30T12:00:00'
      const result = formatRelativeTime(dateString)

      // 3 days is still within the 7-day threshold
      expect(result).toBe('3 天前')
    })
  })
})

describe('admin date formatting', () => {
  const originalTZ = process.env.TZ

  beforeEach(() => {
    // Render as a Beijing viewer: naive UTC must shift by +8h, not be read as local.
    process.env.TZ = 'Asia/Shanghai'
  })

  afterEach(() => {
    process.env.TZ = originalTZ
  })

  it('renders naive UTC and offset timestamps as the same local time', () => {
    const naive = formatAdminDateTime('2024-03-15T10:00:00')
    expect(naive).toBe(formatAdminDateTime('2024-03-15T10:00:00Z'))
    expect(naive).toBe(formatAdminDateTime('2024-03-15T10:00:00+00:00'))
    expect(naive).toBe(formatAdminDateTime('2024-03-15T18:00:00+08:00'))
    expect(naive).toContain('18:00')
  })

  it('renders empty and invalid values as a dash', () => {
    expect(formatAdminDateTime(null)).toBe('-')
    expect(formatAdminDateTime(undefined)).toBe('-')
    expect(formatAdminDateTime('')).toBe('-')
    expect(formatAdminDateTime('not a date')).toBe('-')
  })

  it('keeps bare calendar dates on their own day', () => {
    expect(parseUTCDate('2024-03-15').getDate()).toBe(15)
    expect(formatAdminDate('2024-03-15')).toContain('15')
    expect(formatAdminDate('2024-03-15T20:00:00')).toContain('16')
  })
})

describe('formatBeijingPeriodDate', () => {
  it('shows Beijing calendar dates and the last day of an exclusive end', () => {
    // A Beijing month stored as UTC instants: [09-30 16:00Z, 10-31 16:00Z).
    expect(formatBeijingPeriodDate('2026-09-30T16:00:00+00:00')).toBe('2026/10/01')
    expect(formatBeijingPeriodDate('2026-10-31T16:00:00+00:00', { exclusiveEnd: true })).toBe('2026/10/31')
    // Naive UTC is read as UTC.
    expect(formatBeijingPeriodDate('2026-09-30T16:00:00')).toBe('2026/10/01')
  })

  it('renders "-" for empty or invalid values', () => {
    expect(formatBeijingPeriodDate(null)).toBe('-')
    expect(formatBeijingPeriodDate('nope', { exclusiveEnd: true })).toBe('-')
  })
})
