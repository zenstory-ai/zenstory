import { ApiError } from "./apiClient";
import { translateError } from "./errorHandler";

/**
 * Whether the subscription grants the paid materials library.
 *
 * Returns null while the entitlement is unknown (status not loaded and no
 * tier to infer from).
 */
export function hasMaterialsLibraryAccess(
  features: Record<string, unknown> | undefined,
  tier: string | undefined,
): boolean | null {
  const explicitAccess = features?.materials_library_access;
  if (typeof explicitAccess === "boolean") {
    return explicitAccess;
  }
  if (tier === undefined) {
    return null;
  }
  return tier !== "free";
}

/** True for the backend's "feature not included in your plan" (402) error. */
export function isFeatureNotIncludedError(error: unknown): boolean {
  return error instanceof ApiError && error.errorCode === "ERR_FEATURE_NOT_INCLUDED";
}

/**
 * Translate a decomposition job failure code (`error_message` on materials).
 *
 * The backend stores only ERR_* codes; anything else is treated as the
 * generic failure so raw text never reaches the UI.
 */
export function materialJobErrorText(errorMessage: string | null | undefined): string | null {
  if (!errorMessage) {
    return null;
  }
  const code = /^ERR_[A-Z0-9_]+$/.test(errorMessage)
    ? errorMessage
    : "ERR_MATERIAL_DECOMPOSE_FAILED";
  return translateError(code);
}
