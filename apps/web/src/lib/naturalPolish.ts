/** Must match MAX_SELECTED_TEXT_LENGTH in api/editor.py. */
export const MAX_NATURAL_POLISH_CHARS = 6000;

/**
 * Natural polish helpers.
 *
 * Prompt selection lives on the server so every client path shares the same
 * authoritative rewrite instructions.
 */

export function preserveSelectionWhitespace(
  originalSelection: string,
  rewrittenText: string,
): string {
  const leading = originalSelection.match(/^\s*/)?.[0] ?? "";
  const trailing = originalSelection.match(/\s*$/)?.[0] ?? "";
  return `${leading}${rewrittenText.trim()}${trailing}`;
}

/** Stable error codes the server returns (HTTP 502) when one 去AI味 attempt can't be used. */
export const NATURAL_POLISH_OUTPUT_UNUSABLE = "ERR_NATURAL_POLISH_OUTPUT_UNUSABLE";
export const NATURAL_POLISH_PROVIDER_FAILED = "ERR_NATURAL_POLISH_PROVIDER_FAILED";

export interface NaturalPolishRetryableFailure {
  /** `output`: the reply couldn't be matched back to the selection; `provider`: the model didn't answer. */
  kind: "output" | "provider";
  /** Whether the server confirmed this attempt's AI message was given back. */
  quotaRefunded: boolean;
}

/**
 * Read a failed 去AI味 request as a retryable failure the author can retry by hand.
 *
 * Duck-typed on `errorCode` / `details` (as set by `ApiError`) so callers don't need
 * the API client. Anything else (quota, auth, network) returns null and keeps its
 * own message.
 */
export function readNaturalPolishRetryableFailure(error: unknown): NaturalPolishRetryableFailure | null {
  if (!error || typeof error !== "object") return null;
  const { errorCode, details } = error as { errorCode?: unknown; details?: unknown };
  if (errorCode !== NATURAL_POLISH_OUTPUT_UNUSABLE && errorCode !== NATURAL_POLISH_PROVIDER_FAILED) {
    return null;
  }
  const detail = details && typeof details === "object" ? (details as Record<string, unknown>) : {};
  return {
    kind: errorCode === NATURAL_POLISH_PROVIDER_FAILED ? "provider" : "output",
    quotaRefunded: detail.quota_refunded === true,
  };
}
