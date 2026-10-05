import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import { logger } from "./logger";

const CHUNK_RELOAD_STORAGE_KEY = "zenstory:chunk-reload-once";
const CHUNK_RELOAD_AT_STORAGE_KEY = "zenstory:chunk-reload-at";
const GENERIC_CHUNK_ERROR_SOURCES = new Set(["vite:preloadError", "unhandledrejection"]);
/**
 * A guard set by a generic source (hover preload, unhandled rejection) names
 * no route, so no single route import can prove it recovered. Any successful
 * route import clears it once it is this old: late enough that a failure
 * repeating on every page load still stops after one reload, early enough that
 * a later deploy in the same tab can recover again.
 */
const GENERIC_GUARD_MIN_AGE_MS = 10_000;
const DYNAMIC_IMPORT_ERROR_PATTERNS = [
  "Failed to fetch dynamically imported module",
  "Importing a module script failed",
  "ChunkLoadError",
];
let pendingChunkReload: Promise<never> | null = null;

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message || String(error);
  }
  return String(error ?? "");
}

export function isChunkLoadError(error: unknown): boolean {
  const message = getErrorMessage(error);
  return DYNAMIC_IMPORT_ERROR_PATTERNS.some((pattern) => message.includes(pattern));
}

export function reloadForChunkErrorOnce(error: unknown, source: string): boolean {
  if (typeof window === "undefined" || !isChunkLoadError(error)) {
    return false;
  }

  const guardedSource = sessionStorage.getItem(CHUNK_RELOAD_STORAGE_KEY);
  if (guardedSource !== null) {
    if (
      pendingChunkReload !== null
      && GENERIC_CHUNK_ERROR_SOURCES.has(guardedSource)
      && !GENERIC_CHUNK_ERROR_SOURCES.has(source)
    ) {
      sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, source);
    }
    return pendingChunkReload !== null;
  }

  logger.warn("Recovering from stale chunk load failure", {
    source,
    message: getErrorMessage(error),
  });
  pendingChunkReload = new Promise<never>(() => {});
  sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, source);
  sessionStorage.setItem(CHUNK_RELOAD_AT_STORAGE_KEY, String(Date.now()));
  window.location.reload();
  return true;
}

export function installChunkRecoveryHandlers(): void {
  if (typeof window === "undefined") {
    return;
  }

  window.addEventListener("vite:preloadError", (event) => {
    const viteEvent = event as Event & {
      payload?: unknown;
    };
    // Vite rejects the import only while this event remains uncancelled. Let
    // lazyRoute observe that rejection and suspend on the reload already in
    // progress instead of resolving React.lazy with an undefined module.
    reloadForChunkErrorOnce(viteEvent.payload ?? event, "vite:preloadError");
  });

  window.addEventListener("unhandledrejection", (event) => {
    if (reloadForChunkErrorOnce(event.reason, "unhandledrejection")) {
      event.preventDefault();
    }
  });
}

function clearRecoveredGuard(importedSource: string): void {
  const guardedSource = sessionStorage.getItem(CHUNK_RELOAD_STORAGE_KEY);
  if (guardedSource === null) return;

  const isOwnGuard = guardedSource === importedSource;
  const isStaleGenericGuard =
    GENERIC_CHUNK_ERROR_SOURCES.has(guardedSource)
    && Date.now() - Number(sessionStorage.getItem(CHUNK_RELOAD_AT_STORAGE_KEY) ?? 0)
      >= GENERIC_GUARD_MIN_AGE_MS;

  if (isOwnGuard || isStaleGenericGuard) {
    sessionStorage.removeItem(CHUNK_RELOAD_STORAGE_KEY);
    sessionStorage.removeItem(CHUNK_RELOAD_AT_STORAGE_KEY);
  }
}

export function lazyRoute<TProps>(
  importer: () => Promise<{ default: ComponentType<TProps> }>,
  source: string,
): LazyExoticComponent<ComponentType<TProps>> {
  return lazy(async () => {
    try {
      const module = await importer();
      if (typeof window !== "undefined" && pendingChunkReload === null) {
        clearRecoveredGuard(source);
      }
      return module;
    } catch (error) {
      if (reloadForChunkErrorOnce(error, source)) {
        const pendingReload = pendingChunkReload;
        if (pendingReload) {
          return pendingReload;
        }
      }
      throw error;
    }
  });
}
