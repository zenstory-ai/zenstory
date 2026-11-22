import { trackEvent } from "./analytics";

/**
 * Privacy-clean telemetry for editor safety flows (409 stale-write conflicts,
 * unsaved-draft recovery, and local draft storage failures).
 *
 * Strictly emits only coarse enums and booleans (`surface`, `is_mobile`,
 * `reason`, `resolution`, `storage_available`) via `trackEvent` so the user's
 * analytics opt-out preference is always respected and manuscript text,
 * filenames/titles, or PII can never leave the browser.
 */
export type EditorSaveConflictSurface = "manual" | "auto_save";
export type EditorRecoverySurface = "draft_recovery" | "conflict_review" | "deleted_file_recovery";
export type EditorSafetyReason =
  | "stale_write"
  | "external_edit"
  | "recover"
  | "conflict"
  | "chunk-reload"
  | "page-exit"
  | "file-deleted"
  | "read_failed"
  | "clear_failed";
export type EditorSafetyResolution = "restore_local" | "use_server" | "merge" | "discard";

function detectMobileViewport(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(max-width: 767px)").matches;
}

export function trackEditorSaveConflict409(params: {
  surface: EditorSaveConflictSurface;
  isMobile: boolean;
  reason?: "stale_write";
}): void {
  trackEvent("editor_save_conflict_409", {
    surface: params.surface,
    is_mobile: params.isMobile,
    reason: params.reason ?? "stale_write",
  });
}

export function trackEditorDraftRecoveryShown(params: {
  surface: EditorRecoverySurface;
  isMobile: boolean;
  reason: EditorSafetyReason;
  storageAvailable?: boolean;
}): void {
  trackEvent("editor_draft_recovery_shown", {
    surface: params.surface,
    is_mobile: params.isMobile,
    reason: params.reason,
    ...(params.storageAvailable !== undefined ? { storage_available: params.storageAvailable } : {}),
  });
}

export function trackEditorDraftRecoveryResolved(params: {
  surface: EditorRecoverySurface;
  isMobile: boolean;
  reason?: EditorSafetyReason;
  resolution: EditorSafetyResolution;
}): void {
  trackEvent("editor_draft_recovery_resolved", {
    surface: params.surface,
    is_mobile: params.isMobile,
    ...(params.reason !== undefined ? { reason: params.reason } : {}),
    resolution: params.resolution,
  });
}

export function trackEditorDraftStorageFailed(params: {
  surface: EditorRecoverySurface;
  isMobile?: boolean;
  reason: EditorSafetyReason;
  storageAvailable?: boolean;
}): void {
  trackEvent("editor_draft_storage_failed", {
    surface: params.surface,
    is_mobile: params.isMobile ?? detectMobileViewport(),
    reason: params.reason,
    storage_available: params.storageAvailable ?? false,
  });
}
