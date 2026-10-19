/**
 * The idea typed on the dashboard travels to the new project through
 * localStorage (`zenstory_inspiration_<projectId>`); ChatPanel sends it as the
 * first message. Same 5-minute freshness rule as ChatPanel.
 */
const INSPIRATION_KEY_PREFIX = "zenstory_inspiration_";
const INSPIRATION_MAX_AGE_MS = 5 * 60 * 1000;

/** True when the project was just opened with a dashboard idea that is about to be sent. */
export function hasPendingInspiration(projectId: string | null | undefined, now = Date.now()): boolean {
  if (!projectId) return false;
  try {
    const raw = localStorage.getItem(`${INSPIRATION_KEY_PREFIX}${projectId}`);
    if (!raw) return false;
    const { content, timestamp } = JSON.parse(raw) as { content?: unknown; timestamp?: unknown };
    return (
      typeof content === "string" &&
      content.trim().length > 0 &&
      typeof timestamp === "number" &&
      now - timestamp < INSPIRATION_MAX_AGE_MS
    );
  } catch {
    return false;
  }
}

const OPEN_CHAT_KEY_PREFIX = "zenstory_open_chat_";

/**
 * A work just created from the dashboard without an idea: its first conversation's starter
 * cards are the next step, so phones open it on the AI tab once (same freshness window).
 */
export function requestChatOnOpen(projectId: string, now = Date.now()): void {
  try {
    localStorage.setItem(`${OPEN_CHAT_KEY_PREFIX}${projectId}`, String(now));
  } catch {
    // Storage unavailable: the work simply opens on the editor, which links to the AI.
  }
}

/** True once for a fresh request from `requestChatOnOpen`; the request is cleared either way. */
export function takeChatOnOpen(projectId: string | null | undefined, now = Date.now()): boolean {
  if (!projectId) return false;
  try {
    const key = `${OPEN_CHAT_KEY_PREFIX}${projectId}`;
    const raw = localStorage.getItem(key);
    if (raw === null) return false;
    localStorage.removeItem(key);
    const requestedAt = Number(raw);
    return Number.isFinite(requestedAt) && now - requestedAt < INSPIRATION_MAX_AGE_MS;
  } catch {
    return false;
  }
}
