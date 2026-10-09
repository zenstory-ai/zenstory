/**
 * Date utilities for handling UTC to local time conversion.
 *
 * This module provides functions to parse and format datetime strings
 * received from the backend, which stores all timestamps in UTC.
 * The frontend displays these in the user's local timezone with i18n support.
 *
 * Architecture:
 * - Backend returns datetime strings without timezone suffix (e.g., "2024-01-12T10:00:00")
 * - parseUTCDate() ensures these strings are correctly interpreted as UTC
 * - Formatting functions use i18n for localized output in user's language
 * - Relative time formatting (e.g., "5 minutes ago") for recent timestamps
 * - Full date formatting for older timestamps
 *
 * Usage:
 * ```ts
 * import { parseUTCDate, formatRelativeTime, formatFullDate } from '../lib/dateUtils';
 *
 * // Parse UTC string from backend
 * const localDate = parseUTCDate('2024-01-12T10:00:00');
 *
 * // Show relative time for recent items
 * const relative = formatRelativeTime(utcString); // "5 分钟前" or "5 minutes ago"
 *
 * // Show full date for detailed display
 * const full = formatFullDate(utcString); // "2024/01/12 10:30"
 * ```
 *
 * @module lib/dateUtils
 */

import i18n from 'i18next';
import { getLocaleCode } from './i18n-helpers';

/**
 * Format local Date into YYYY-MM-DD using local timezone calendar day.
 */
export function getLocalDateString(baseDate: Date = new Date()): string {
  const year = baseDate.getFullYear();
  const month = String(baseDate.getMonth() + 1).padStart(2, '0');
  const day = String(baseDate.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

const BEIJING_DAY_FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

/**
 * YYYY-MM-DD of the Beijing calendar day, the day boundary quotas and
 * project statistics use, whatever the viewer's own timezone.
 */
export function getBeijingDateString(baseDate: Date = new Date()): string {
  const parts = Object.fromEntries(
    BEIJING_DAY_FORMAT.formatToParts(baseDate).map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/**
 * Format a calendar date ("YYYY-MM-DD", or a string starting with one) as
 * that same day. `new Date("YYYY-MM-DD")` is UTC midnight, which shows as the
 * previous day west of UTC; this never shifts the day. Other values are
 * returned unchanged.
 */
export function formatCalendarDate(value: string, locale: string = getLocaleCode()): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const [, year, month, day] = match;
  return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))).toLocaleDateString(locale, {
    timeZone: 'UTC',
  });
}

/**
 * Parse a UTC datetime string from backend and convert to local Date object.
 *
 * Backend returns datetime strings without timezone suffix (e.g., "2024-01-12T10:00:00").
 * This function ensures they are correctly interpreted as UTC by appending 'Z' if needed.
 *
 * @param dateString - UTC datetime string from backend (ISO 8601 format)
 * @returns Date object in local timezone
 *
 * @example
 * ```ts
 * // String without timezone suffix gets 'Z' appended
 * const date = parseUTCDate('2024-01-12T10:00:00');
 * // Equivalent to: new Date('2024-01-12T10:00:00Z')
 *
 * // Strings with timezone info are passed through
 * const dateWithZ = parseUTCDate('2024-01-12T10:00:00Z');
 * const dateWithOffset = parseUTCDate('2024-01-12T10:00:00+08:00');
 * ```
 */
export function parseUTCDate(dateString: string): Date {
  // A bare calendar date ("2024-01-12") has no time to shift; read it as a local day.
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateString);
  if (dateOnly) {
    return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]));
  }
  // If the string doesn't end with 'Z' or timezone offset, append 'Z' to mark as UTC
  if (
    !dateString.endsWith("Z") &&
    !dateString.match(/[+-]\d{2}:\d{2}$/) &&
    !dateString.match(/[+-]\d{4}$/)
  ) {
    return new Date(dateString + "Z");
  }
  return new Date(dateString);
}

/**
 * Format a UTC datetime string to relative time (e.g., "刚刚", "5 分钟前").
 *
 * Produces human-readable relative timestamps for recent dates, falling back
 * to locale-formatted dates for items older than 7 days.
 *
 * Time thresholds:
 * - < 1 minute: "刚刚" (just now)
 * - < 60 minutes: "X 分钟前" (X minutes ago)
 * - < 24 hours: "X 小时前" (X hours ago)
 * - < 7 days: "X 天前" (X days ago)
 * - >= 7 days: Full date format (e.g., "Jan 12, 10:30 AM")
 *
 * @param dateString - UTC datetime string from backend
 * @returns Formatted relative time string in current language
 *
 * @example
 * ```ts
 * // Recent timestamp (5 minutes ago)
 * formatRelativeTime('2024-01-12T09:55:00'); // "5 分钟前" (zh) or "5 minutes ago" (en)
 *
 * // Older timestamp (more than 7 days)
 * formatRelativeTime('2024-01-01T10:00:00'); // "Jan 1, 10:00 AM"
 * ```
 */
export function formatRelativeTime(dateString: string, now: Date = new Date()): string {
  const date = parseUTCDate(dateString);
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return i18n.t('relativeTime.justNow');
  if (diffMins < 60) return i18n.t('relativeTime.minutesAgo', { count: diffMins });
  if (diffHours < 24) return i18n.t('relativeTime.hoursAgo', { count: diffHours });
  if (diffDays < 7) return i18n.t('relativeTime.daysAgo', { count: diffDays });

  return date.toLocaleDateString(getLocaleCode(), {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Format a UTC datetime string to full date with year.
 *
 * Produces a complete localized date/time string including year,
 * suitable for file timestamps, version history, and detailed views.
 *
 * @param dateString - UTC datetime string from backend
 * @returns Formatted date string (e.g., "2024/01/12 10:30" or "01/12/2024, 10:30 AM")
 *
 * @example
 * ```ts
 * formatFullDate('2024-01-12T10:30:00');
 * // Returns: "2024/01/12 10:30" (zh) or "01/12/2024, 10:30 AM" (en)
 * ```
 */
export function formatFullDate(dateString: string): string {
  const date = parseUTCDate(dateString);
  return date.toLocaleDateString(getLocaleCode(), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Format a UTC datetime string to relative time with full date fallback for older items.
 *
 * Combination of formatRelativeTime and formatFullDate behavior.
 * Shows relative time for recent items (within 7 days) and full date with year
 * for older items. This is useful for lists where you want readable timestamps
 * but also need the year for context on older entries.
 *
 * Time thresholds:
 * - < 1 minute: "刚刚" (just now)
 * - < 60 minutes: "X 分钟前" (X minutes ago)
 * - < 24 hours: "X 小时前" (X hours ago)
 * - < 7 days: "X 天前" (X days ago)
 * - >= 7 days: Full date with year (e.g., "2024/01/01 10:30")
 *
 * @param dateString - UTC datetime string from backend
 * @returns Formatted time string with relative or full date format
 *
 * @example
 * ```ts
 * // Recent item (2 hours ago)
 * formatRelativeTimeWithYear('2024-01-12T08:00:00'); // "2 小时前"
 *
 * // Older item (more than 7 days)
 * formatRelativeTimeWithYear('2024-01-01T10:00:00'); // "2024/01/01 10:00"
 * ```
 */
export function formatRelativeTimeWithYear(dateString: string): string {
  const date = parseUTCDate(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return i18n.t('relativeTime.justNow');
  if (diffMins < 60) return i18n.t('relativeTime.minutesAgo', { count: diffMins });
  if (diffHours < 24) return i18n.t('relativeTime.hoursAgo', { count: diffHours });
  if (diffDays < 7) return i18n.t('relativeTime.daysAgo', { count: diffDays });

  return date.toLocaleDateString(getLocaleCode(), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const ADMIN_DATE_TIME_FORMAT: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
};

/**
 * Format a backend timestamp in the viewer's local time.
 *
 * Accepts naive UTC ("2024-01-12T10:00:00"), offset ISO strings and bare
 * calendar dates. Empty or unparseable values render as "-".
 */
export function formatAdminDateTime(
  value: string | null | undefined,
  options: Intl.DateTimeFormatOptions = ADMIN_DATE_TIME_FORMAT,
): string {
  if (!value) return "-";
  const date = parseUTCDate(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString(getLocaleCode(), options);
}

/** Like formatAdminDateTime, without the time of day. */
export function formatAdminDate(value: string | null | undefined): string {
  return formatAdminDateTime(value, { year: "numeric", month: "2-digit", day: "2-digit" });
}

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Format a quota period bound as a Beijing calendar date, the calendar quota
 * periods follow. With `exclusiveEnd` the value is a period's end (the instant
 * the next period starts), so the last day inside the period is shown.
 */
export function formatBeijingPeriodDate(
  value: string | null | undefined,
  { exclusiveEnd = false }: { exclusiveEnd?: boolean } = {},
): string {
  if (!value) return "-";
  const date = parseUTCDate(value);
  if (Number.isNaN(date.getTime())) return "-";
  const shown = exclusiveEnd ? new Date(date.getTime() - ONE_DAY_MS) : date;
  return shown.toLocaleDateString(getLocaleCode(), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  });
}
