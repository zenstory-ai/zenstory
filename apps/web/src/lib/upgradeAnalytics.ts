import logger from "./logger";
import { getAccessToken, getApiBase } from "./apiClient";
import { trackEvent } from "./analytics";

export const UPGRADE_FUNNEL_EVENT = "zenstory:upgrade-funnel";

export type UpgradeFunnelAction = "expose" | "click" | "conversion";
export type UpgradeFunnelSurface = "modal" | "toast" | "page";
export type UpgradeFunnelCta = "primary" | "secondary" | "direct";

const EVENT_NAME_BY_ACTION: Record<UpgradeFunnelAction, string> = {
  expose: "upgrade_entry_expose",
  click: "upgrade_entry_click",
  conversion: "upgrade_entry_conversion",
};

export interface UpgradeFunnelPayload {
  event_name: string;
  action: UpgradeFunnelAction;
  source: string;
  surface: UpgradeFunnelSurface;
  cta?: UpgradeFunnelCta;
  destination?: string;
  meta?: Record<string, string | number | boolean | null>;
  occurred_at: string;
}

export interface TrackUpgradeFunnelEventInput {
  action: UpgradeFunnelAction;
  source: string;
  surface?: UpgradeFunnelSurface;
  cta?: UpgradeFunnelCta;
  destination?: string;
  meta?: Record<string, string | number | boolean | null>;
}

const PENDING_STORAGE_KEY = "zenstory_upgrade_funnel_pending_events";
const MAX_PENDING_EVENTS = 200;
const RETRY_DELAY_MS = 5000;
const DROP_STATUS_CODES = new Set([400, 404, 413, 422]);

const ACTION_SET = new Set<UpgradeFunnelAction>(["expose", "click", "conversion"]);
const SURFACE_SET = new Set<UpgradeFunnelSurface>(["modal", "toast", "page"]);
const CTA_SET = new Set<UpgradeFunnelCta>(["primary", "secondary", "direct"]);

let isFlushing = false;
let flushHooksBound = false;
let pendingHydrated = false;
let retryTimer: number | null = null;
/** Access token the backend answered 401 for; no retries until it changes. */
let rejectedAccessToken: string | null = null;
const pendingQueue: UpgradeFunnelPayload[] = [];

declare global {
  interface Window {
    __zenstoryTrackEvent?: (eventName: string, payload: UpgradeFunnelPayload) => void;
  }

  interface WindowEventMap {
    [UPGRADE_FUNNEL_EVENT]: CustomEvent<UpgradeFunnelPayload>;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isUpgradeFunnelPayload(value: unknown): value is UpgradeFunnelPayload {
  if (!isRecord(value)) return false;
  if (typeof value.event_name !== "string") return false;
  if (typeof value.source !== "string") return false;
  if (typeof value.occurred_at !== "string") return false;
  if (!ACTION_SET.has(value.action as UpgradeFunnelAction)) return false;
  if (!SURFACE_SET.has(value.surface as UpgradeFunnelSurface)) return false;

  if (value.cta != null && !CTA_SET.has(value.cta as UpgradeFunnelCta)) {
    return false;
  }

  if (value.destination != null && typeof value.destination !== "string") {
    return false;
  }

  if (value.meta != null && !isRecord(value.meta)) {
    return false;
  }

  return true;
}

function readPendingEvents(): UpgradeFunnelPayload[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = localStorage.getItem(PENDING_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isUpgradeFunnelPayload).slice(-MAX_PENDING_EVENTS);
  } catch {
    return [];
  }
}

function writePendingEvents(events: UpgradeFunnelPayload[]): void {
  if (typeof window === "undefined") return;

  try {
    localStorage.setItem(PENDING_STORAGE_KEY, JSON.stringify(events.slice(-MAX_PENDING_EVENTS)));
  } catch {
    // Ignore storage failures in analytics path.
  }
}

function hydratePendingQueue(): void {
  if (pendingHydrated) return;
  pendingHydrated = true;

  const persisted = readPendingEvents();
  if (persisted.length === 0) return;
  pendingQueue.push(...persisted);
}

function persistPendingQueue(): void {
  writePendingEvents(pendingQueue);
}

function clearRetryTimer(): void {
  if (retryTimer === null || typeof window === "undefined") return;
  window.clearTimeout(retryTimer);
  retryTimer = null;
}

function scheduleRetryFlush(): void {
  if (typeof window === "undefined" || retryTimer !== null) return;

  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    void flushPendingUpgradeFunnelEvents();
  }, RETRY_DELAY_MS);
}

function enqueuePendingEvent(payload: UpgradeFunnelPayload): void {
  hydratePendingQueue();
  pendingQueue.push(payload);
  if (pendingQueue.length > MAX_PENDING_EVENTS) {
    pendingQueue.splice(0, pendingQueue.length - MAX_PENDING_EVENTS);
  }
  persistPendingQueue();
}

function ensureFlushHooks(): void {
  if (typeof window === "undefined" || flushHooksBound) return;
  flushHooksBound = true;

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      void flushPendingUpgradeFunnelEvents({ keepalive: true });
      return;
    }
    void flushPendingUpgradeFunnelEvents();
  });

  window.addEventListener("focus", () => {
    void flushPendingUpgradeFunnelEvents();
  });

  window.addEventListener("online", () => {
    void flushPendingUpgradeFunnelEvents();
  });

  window.addEventListener("pagehide", () => {
    void flushPendingUpgradeFunnelEvents({ keepalive: true });
  });
}

function getErrorStatus(error: unknown): number | null {
  if (!isRecord(error)) return null;
  if (!("status" in error)) return null;
  const status = error.status;
  return typeof status === "number" ? status : null;
}

export function trackUpgradeFunnelEvent({
  action,
  source,
  surface = "modal",
  cta,
  destination,
  meta,
}: TrackUpgradeFunnelEventInput): void {
  const normalizedSource = source.trim();
  if (!normalizedSource) return;

  const payload: UpgradeFunnelPayload = {
    event_name: EVENT_NAME_BY_ACTION[action],
    action,
    source: normalizedSource,
    surface,
    cta,
    destination,
    meta,
    occurred_at: new Date().toISOString(),
  };

  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent<UpgradeFunnelPayload>(UPGRADE_FUNNEL_EVENT, {
        detail: payload,
      })
    );

    try {
      window.__zenstoryTrackEvent?.(payload.event_name, payload);
    } catch (error) {
      logger.warn("[upgrade-funnel] failed to call external tracker", error);
    }

    trackEvent(payload.event_name, {
      ...payload.meta,
      action: payload.action,
      source: payload.source,
      surface: payload.surface,
      cta: payload.cta,
      destination: payload.destination,
    });

    ensureFlushHooks();
    enqueuePendingEvent(payload);
    void flushPendingUpgradeFunnelEvents();
  }
}

async function sendUpgradeFunnelToBackend(
  payload: UpgradeFunnelPayload,
  accessToken: string,
  keepalive = false
): Promise<"accepted" | "drop" | "retry" | "unauthorized"> {
  try {
    const response = await fetch(`${getApiBase()}/api/v1/subscription/upgrade-funnel-events`, {
      method: "POST",
      keepalive,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        action: payload.action,
        source: payload.source,
        surface: payload.surface,
        cta: payload.cta,
        destination: payload.destination,
        event_name: payload.event_name,
        meta: payload.meta,
        occurred_at: payload.occurred_at,
      }),
    });

    if (response.ok) {
      return "accepted";
    }

    if (response.status === 401) {
      logger.warn("[upgrade-funnel] backend tracking unauthorized; pausing until next login");
      return "unauthorized";
    }

    if (DROP_STATUS_CODES.has(response.status)) {
      logger.warn("[upgrade-funnel] backend tracking rejected", {
        status: response.status,
        dropped: true,
      });
      return "drop";
    }

    logger.warn("[upgrade-funnel] backend tracking failed", {
      status: response.status,
      willRetry: true,
    });
    return "retry";
  } catch (error) {
    const status = getErrorStatus(error);
    if (status !== null && DROP_STATUS_CODES.has(status)) {
      logger.warn("[upgrade-funnel] dropping invalid tracking payload", {
        status,
      });
      return "drop";
    }

    logger.warn("[upgrade-funnel] failed to send backend tracking event", error);
    return "retry";
  }
}

async function flushPendingUpgradeFunnelEvents(options: { keepalive?: boolean } = {}): Promise<void> {
  if (typeof window === "undefined") return;

  hydratePendingQueue();
  if (isFlushing || pendingQueue.length === 0) return;

  const accessToken = getAccessToken();
  if (!accessToken || accessToken === rejectedAccessToken) return;

  clearRetryTimer();
  isFlushing = true;
  try {
    while (pendingQueue.length > 0) {
      const current = pendingQueue[0];
      const outcome = await sendUpgradeFunnelToBackend(current, accessToken, options.keepalive === true);

      if (outcome === "accepted" || outcome === "drop") {
        pendingQueue.shift();
        persistPendingQueue();
        continue;
      }

      if (outcome === "unauthorized") {
        // An expired token stays expired: stop the 5s retry loop. A new
        // login stores a different token and flushing resumes.
        rejectedAccessToken = accessToken;
        break;
      }

      scheduleRetryFlush();
      break;
    }
  } finally {
    isFlushing = false;
  }
}

/**
 * Drop queued funnel events. Called on logout so one account's pending events
 * are never sent with the next account's token on a shared browser.
 */
export function clearPendingUpgradeFunnelEvents(): void {
  clearRetryTimer();
  pendingQueue.length = 0;
  pendingHydrated = true;
  rejectedAccessToken = null;
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(PENDING_STORAGE_KEY);
  } catch {
    // Ignore storage failures in analytics path.
  }
}

export function trackUpgradeExpose(source: string, surface: UpgradeFunnelSurface = "modal"): void {
  trackUpgradeFunnelEvent({
    action: "expose",
    source,
    surface,
  });
}

export function trackUpgradeClick(
  source: string,
  cta: UpgradeFunnelCta,
  destination?: string,
  surface: UpgradeFunnelSurface = "modal"
): void {
  trackUpgradeFunnelEvent({
    action: "click",
    source,
    surface,
    cta,
    destination,
  });
}

export function trackUpgradeConversion(
  source: string,
  destination: string,
  surface: UpgradeFunnelSurface = "page"
): void {
  trackUpgradeFunnelEvent({
    action: "conversion",
    source,
    surface,
    destination,
  });
}

if (typeof window !== "undefined") {
  hydratePendingQueue();
  if (pendingQueue.length > 0) {
    ensureFlushHooks();
    window.setTimeout(() => {
      void flushPendingUpgradeFunnelEvents();
    }, 0);
  }
}
