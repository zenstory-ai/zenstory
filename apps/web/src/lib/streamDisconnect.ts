/**
 * Delay before performing a single read-only GET reconcile after an unexpected SSE
 * disconnect so the backend has time to commit any in-flight partial/final reply.
 */
export const SSE_DISCONNECT_RECONCILE_DELAY_MS = 1500;

/**
 * True when an SSE error represents an unexpected transport drop (closed/errored stream)
 * rather than an explicit server error, quota refusal, or author-triggered abort.
 * Callers may run a single read-only GET reconcile; NEVER automatically retry POST.
 */
export function isUnexpectedStreamDisconnect(
  code: string | null | undefined,
  retryable: boolean | undefined,
): boolean {
  return retryable === true && (code === "STREAM_CLOSED" || code === "STREAM_ERROR");
}
