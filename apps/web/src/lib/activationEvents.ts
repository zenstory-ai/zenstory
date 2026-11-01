/**
 * New-user screen milestones reported to the server (`POST /api/v1/activation/events`).
 *
 * The server keeps one row per account and event; this module only avoids sending what
 * it already knows: an event the server confirmed is remembered per account (and, best
 * effort, across reloads), and each file is checked once for `ai_file_opened` until the AI
 * writes into it again (`rearmAiFileOpened`). Fields are enums and booleans only, never manuscript text or prompts.
 * Every failure is silent: these calls must never affect the screen they observe.
 */
import { api } from "./apiClient";

export type ClientActivationEvent =
  | { event_name: "persona_submitted"; skipped: boolean }
  | { event_name: "dashboard_viewed"; is_mobile?: boolean }
  | { event_name: "ai_file_opened"; file_id: string; is_mobile?: boolean; while_ai_writing?: boolean }
  | {
    event_name: "ai_send_blocked_quota";
    surface: "chat" | "dashboard";
    project_id?: string | null;
    server_refused?: boolean;
    is_mobile?: boolean;
  };

const STORAGE_PREFIX = "zenstory:activation-reported:";

/** Events the server confirmed, per account. */
const confirmed = new Map<string, Set<string>>();
/** Requests in flight or already answered for this page load: `user|event` or `user|ai_file_opened|file`. */
const attempted = new Set<string>();

function confirmedFor(userId: string): Set<string> {
  let events = confirmed.get(userId);
  if (!events) {
    events = new Set();
    try {
      const raw = localStorage.getItem(STORAGE_PREFIX + userId);
      const parsed: unknown = raw ? JSON.parse(raw) : [];
      if (Array.isArray(parsed)) parsed.forEach((name) => typeof name === "string" && events!.add(name));
    } catch {
      // Storage is a convenience; the server stays idempotent without it.
    }
    confirmed.set(userId, events);
  }
  return events;
}

function rememberConfirmed(userId: string, eventName: string): void {
  const events = confirmedFor(userId);
  events.add(eventName);
  try {
    localStorage.setItem(STORAGE_PREFIX + userId, JSON.stringify([...events]));
  } catch {
    // Ignore: at worst the next page load sends one more idempotent request.
  }
}

/** Report a screen milestone for `userId`; resolves when settled and never rejects. */
export async function reportActivationEvent(userId: string | null | undefined, event: ClientActivationEvent): Promise<void> {
  if (!userId) return;
  if (confirmedFor(userId).has(event.event_name)) return;
  const attemptKey = event.event_name === "ai_file_opened"
    ? `${userId}|${event.event_name}|${event.file_id}`
    : `${userId}|${event.event_name}`;
  if (attempted.has(attemptKey)) return;
  attempted.add(attemptKey);
  try {
    const result = await api.post<{ recorded: boolean }>("/api/v1/activation/events", event);
    if (result?.recorded) {
      rememberConfirmed(userId, event.event_name);
    } else if (event.event_name !== "ai_file_opened") {
      // Not recorded: a server-side hiccup. Allow one more try later in this page load.
      // A file without an AI version stays checked until the AI writes into it.
      attempted.delete(attemptKey);
    }
  } catch {
    // Network, auth or validation failure: drop silently and let a later screen retry.
    attempted.delete(attemptKey);
  }
}

/**
 * The AI finished writing `fileId`: it may now have an AI version, so the next open (or the
 * reload of the open file) is checked again even if an earlier check returned "not recorded".
 */
export function rearmAiFileOpened(userId: string | null | undefined, fileId: string): void {
  if (!userId) return;
  attempted.delete(`${userId}|ai_file_opened|${fileId}`);
}

/** Forget in-memory state (logout, tests). Persisted confirmations stay per account. */
export function resetActivationEventState(): void {
  confirmed.clear();
  attempted.clear();
}
