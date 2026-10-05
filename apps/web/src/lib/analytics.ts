import posthog from "posthog-js";
import type { CaptureResult } from "posthog-js";
import logger from "./logger";
import type { User } from "../contexts/AuthContext";
import { parseEnvBoolean } from "../config/env";

export interface AnalyticsEnv {
  DEV: boolean;
  MODE?: string;
  VITE_POSTHOG_ENABLED?: string;
  VITE_POSTHOG_KEY?: string;
  VITE_POSTHOG_HOST?: string;
}

export type AnalyticsProperties = Record<string, unknown>;

interface AnalyticsInitStatus {
  enabled: boolean;
  hasKey: boolean;
  host: string;
  reason?: "disabled_flag" | "missing_key" | "opted_out";
}

const DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com";
const PAGE_VIEW_EVENT = "page_view";
const PAGEVIEW_DEDUPE_WINDOW_MS = 1000;
const ANALYTICS_OPT_OUT_STORAGE_KEY = "zenstory:analytics-opt-out";

/**
 * Query parameters allowed to leave the browser in analytics URLs. Everything
 * else (OAuth tokens, SSO `token`, `email`, invite `code`, ...) is dropped, and
 * URL fragments are never sent.
 */
const URL_QUERY_ALLOWLIST = new Set(["source", "ref", "plan"]);
const URL_QUERY_ALLOWLIST_PREFIXES = ["utm_"];
/** Routes whose URLs carry credentials; they never produce a page view. */
const UNTRACKED_PATH_PREFIXES = ["/auth/callback"];
/** PostHog properties holding a bare path. */
const PATH_PROPERTY_KEYS = new Set(["$pathname", "$prev_pageview_pathname", "$initial_pathname"]);

let isInitialized = false;
let lastPageViewKey: string | null = null;
let lastPageViewAt = 0;
let lastInitEnv: AnalyticsEnv | null = null;

declare global {
  interface Window {
    __zenstoryPosthog?: typeof posthog;
    __zenstoryAnalyticsStatus?: AnalyticsInitStatus;
  }
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function isAnalyticsEnabled(env: AnalyticsEnv = import.meta.env): boolean {
  return parseEnvBoolean(env.VITE_POSTHOG_ENABLED, false) && Boolean(env.VITE_POSTHOG_KEY?.trim());
}

export function getAnalyticsHost(env: AnalyticsEnv = import.meta.env): string {
  return env.VITE_POSTHOG_HOST?.trim() || DEFAULT_POSTHOG_HOST;
}

function isAllowedQueryKey(key: string): boolean {
  const normalized = key.toLowerCase();
  return (
    URL_QUERY_ALLOWLIST.has(normalized)
    || URL_QUERY_ALLOWLIST_PREFIXES.some((prefix) => normalized.startsWith(prefix))
  );
}

/** Keep only allow-listed query parameters; returns "" or "?a=b". */
export function sanitizeSearch(search: string): string {
  const params = new URLSearchParams(search.replace(/^\?/, ""));
  const kept = new URLSearchParams();
  params.forEach((value, key) => {
    if (isAllowedQueryKey(key)) kept.append(key, value);
  });
  const rendered = kept.toString();
  return rendered ? `?${rendered}` : "";
}

/**
 * Strip the fragment and non-allow-listed query parameters from a URL so
 * credentials and personal data never reach the analytics vendor.
 */
export function sanitizeUrl(raw: string): string {
  if (!raw) return raw;
  try {
    const isRelative = raw.startsWith("/") && !raw.startsWith("//");
    const base = isBrowser() ? window.location.origin : "http://localhost";
    const url = new URL(raw, base);
    const path = `${url.pathname}${sanitizeSearch(url.search)}`;
    return isRelative ? path : `${url.origin}${path}`;
  } catch {
    return raw.split(/[?#]/, 1)[0] ?? "";
  }
}

function isUrlPropertyKey(key: string): boolean {
  const normalized = key.toLowerCase();
  return normalized.endsWith("_url") || normalized.endsWith("referrer");
}

/** Sanitize every URL-like property of an analytics payload. */
export function sanitizeAnalyticsProperties<T extends Record<string, unknown>>(properties: T): T {
  const sanitized: Record<string, unknown> = { ...properties };
  for (const [key, value] of Object.entries(sanitized)) {
    if (key === "hash" || key === "$hash") {
      delete sanitized[key];
    } else if (typeof value !== "string") {
      continue;
    } else if (key === "search") {
      sanitized[key] = sanitizeSearch(value) || undefined;
    } else if (PATH_PROPERTY_KEYS.has(key)) {
      sanitized[key] = value.split(/[?#]/, 1)[0];
    } else if (isUrlPropertyKey(key)) {
      sanitized[key] = sanitizeUrl(value);
    }
  }
  return sanitized as T;
}

/**
 * PostHog `before_send` hook: last line of defense for everything the SDK
 * sends on its own ($current_url, $referrer, $initial_* person properties,
 * exception autocapture), not just events sent through this module.
 */
export function sanitizeCaptureResult(result: CaptureResult | null): CaptureResult | null {
  if (!result) return result;
  return {
    ...result,
    properties: sanitizeAnalyticsProperties(result.properties ?? {}),
    ...(result.$set ? { $set: sanitizeAnalyticsProperties(result.$set) } : {}),
    ...(result.$set_once ? { $set_once: sanitizeAnalyticsProperties(result.$set_once) } : {}),
  };
}

function getCommonProperties(): AnalyticsProperties {
  if (!isBrowser()) return {};

  return {
    page_path: window.location.pathname,
    page_url: sanitizeUrl(window.location.href),
    page_title: document.title,
    referrer: document.referrer ? sanitizeUrl(document.referrer) : undefined,
    app_env: import.meta.env.MODE,
  };
}

function readOptOut(): boolean {
  if (!isBrowser()) return false;
  try {
    return window.localStorage.getItem(ANALYTICS_OPT_OUT_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function isAnalyticsOptedOut(): boolean {
  return readOptOut();
}

/**
 * Viewer-controlled analytics switch (Settings > General). Opting out stops
 * all PostHog capture (events and error tracking) and is remembered per
 * browser; opting back in starts it again without a reload.
 */
export function setAnalyticsOptOut(optOut: boolean): void {
  if (!isBrowser()) return;
  try {
    if (optOut) {
      window.localStorage.setItem(ANALYTICS_OPT_OUT_STORAGE_KEY, "true");
    } else {
      window.localStorage.removeItem(ANALYTICS_OPT_OUT_STORAGE_KEY);
    }
  } catch {
    // Storage blocked: the switch still applies for this page load.
  }

  if (optOut) {
    if (isInitialized) posthog.opt_out_capturing();
    return;
  }
  if (isInitialized) {
    posthog.opt_in_capturing();
  } else if (lastInitEnv) {
    initAnalytics(lastInitEnv, { ignoreOptOut: true });
  }
}

function shouldSkipPageView(pageViewKey: string): boolean {
  const now = Date.now();
  const shouldSkip =
    lastPageViewKey === pageViewKey && now - lastPageViewAt < PAGEVIEW_DEDUPE_WINDOW_MS;

  lastPageViewKey = pageViewKey;
  lastPageViewAt = now;
  return shouldSkip;
}

function setAnalyticsStatus(status: AnalyticsInitStatus): void {
  if (isBrowser() && import.meta.env.DEV) {
    window.__zenstoryAnalyticsStatus = status;
  }
}

export function initAnalytics(
  env: AnalyticsEnv = import.meta.env,
  options: { ignoreOptOut?: boolean } = {},
): boolean {
  if (!isBrowser()) return false;
  if (isInitialized) return true;
  lastInitEnv = env;

  const enabledFlag = parseEnvBoolean(env.VITE_POSTHOG_ENABLED, false);
  const apiKey = env.VITE_POSTHOG_KEY?.trim();
  if (!enabledFlag) {
    const status = {
      enabled: false,
      hasKey: Boolean(apiKey),
      host: getAnalyticsHost(env),
      reason: "disabled_flag" as const,
    };
    setAnalyticsStatus(status);
    logger.info("[analytics] PostHog disabled", {
      reason: status.reason,
      enabled_flag: env.VITE_POSTHOG_ENABLED ?? null,
      has_key: status.hasKey,
    });
    return false;
  }
  if (!apiKey) {
    const status = {
      enabled: false,
      hasKey: false,
      host: getAnalyticsHost(env),
      reason: "missing_key" as const,
    };
    setAnalyticsStatus(status);
    logger.warn("[analytics] PostHog disabled", {
      reason: status.reason,
    });
    return false;
  }

  if (!options.ignoreOptOut && readOptOut()) {
    const status = {
      enabled: false,
      hasKey: true,
      host: getAnalyticsHost(env),
      reason: "opted_out" as const,
    };
    setAnalyticsStatus(status);
    logger.info("[analytics] PostHog disabled", { reason: status.reason });
    return false;
  }

  posthog.init(
    apiKey,
    {
      api_host: getAnalyticsHost(env),
      defaults: "2026-01-30",
      internal_or_test_user_hostname: undefined,
      person_profiles: "identified_only",
      autocapture: false,
      capture_pageview: false,
      disable_session_recording: true,
      // Pin every DOM-derived capture off locally so a PostHog project
      // setting cannot start sending manuscript or chat text.
      capture_dead_clicks: false,
      capture_heatmaps: false,
      rageclick: false,
      mask_all_text: true,
      mask_all_element_attributes: true,
      before_send: sanitizeCaptureResult,
      request_batching: !env.DEV,
    } as unknown as Parameters<typeof posthog.init>[1]
  );
  // PostHog persists opt_out_capturing() in its own storage key, so a browser
  // that opted out and then reloaded would stay silenced after the switch was
  // turned back on. Our flag is the only source of truth: reaching this point
  // means the viewer has not opted out, so clear any stale PostHog opt-out.
  if (posthog.has_opted_out_capturing()) {
    posthog.opt_in_capturing();
  }
  posthog.startExceptionAutocapture();
  isInitialized = true;
  setAnalyticsStatus({
    enabled: true,
    hasKey: true,
    host: getAnalyticsHost(env),
  });
  if (env.DEV && isBrowser()) {
    window.__zenstoryPosthog = posthog;
  }

  logger.info("[analytics] PostHog initialized", {
    host: getAnalyticsHost(env),
  });

  return true;
}

export function trackEvent(eventName: string, properties: AnalyticsProperties = {}): void {
  if (!isInitialized || !isBrowser()) return;
  const normalizedEventName = eventName.trim();
  if (!normalizedEventName) return;

  posthog.capture(normalizedEventName, sanitizeAnalyticsProperties({
    ...getCommonProperties(),
    ...properties,
  }));
}

export function trackPageView(properties: AnalyticsProperties = {}): void {
  if (!isInitialized || !isBrowser()) return;

  const path = window.location.pathname;
  if (UNTRACKED_PATH_PREFIXES.some((prefix) => path.startsWith(prefix))) return;

  const search = sanitizeSearch(window.location.search);
  const pageViewKey = `${path}${search}`;
  if (shouldSkipPageView(pageViewKey)) return;

  trackEvent(PAGE_VIEW_EVENT, {
    path,
    search: search || undefined,
    ...properties,
  });
}

export function identifyUser(user: Pick<User, "id" | "email" | "username" | "is_superuser">): void {
  if (!isInitialized) return;

  posthog.identify(user.id, {
    is_superuser: user.is_superuser,
  });
}

export function resetAnalytics(): void {
  if (!isInitialized) return;

  posthog.reset(true);
  lastPageViewKey = null;
  lastPageViewAt = 0;
}

export function captureException(
  error: unknown,
  additionalProperties: AnalyticsProperties = {}
): void {
  if (!isInitialized) return;

  posthog.captureException(error, sanitizeAnalyticsProperties({
    ...getCommonProperties(),
    ...additionalProperties,
  }));
}
