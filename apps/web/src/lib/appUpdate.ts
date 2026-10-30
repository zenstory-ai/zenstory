/**
 * Detects that a newer build of the app has been deployed while this tab is
 * open. Each build writes `/version.json` naming its entry script (see the
 * `appVersionManifest` plugin in vite.config.ts); the entry file name is
 * content-hashed and changes whenever any chunk changes, so comparing it with
 * the entry script of this document identifies a stale tab without a separate
 * version counter.
 */

export const APP_VERSION_URL = "/version.json";
const CHECK_INTERVAL_MS = 30 * 60_000;
const MIN_CHECK_GAP_MS = 60_000;

export interface AppUpdateState {
  /** A newer build is deployed; the user has not dismissed this one. */
  available: boolean;
  /**
   * This tab already failed to load code from the old build and could not
   * recover on its own, so a reload is needed rather than merely suggested.
   */
  required: boolean;
}

let state: AppUpdateState = { available: false, required: false };
let latestEntry: string | null = null;
let dismissedEntry: string | null = null;
let lastCheckAt = 0;
let checkInFlight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function setState(next: AppUpdateState): void {
  if (next.available === state.available && next.required === state.required) return;
  state = next;
  listeners.forEach((listener) => listener());
}

export function subscribeAppUpdate(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getAppUpdateState(): AppUpdateState {
  return state;
}

/** Path of this document's entry module, e.g. `/assets/index-abc123.js`. */
export function currentEntry(doc: Document = document): string | null {
  const script = doc.querySelector<HTMLScriptElement>('script[type="module"][src]');
  if (!script) return null;
  try {
    return new URL(script.src, doc.baseURI).pathname;
  } catch {
    return null;
  }
}

async function fetchLatestEntry(): Promise<string | null> {
  try {
    const response = await fetch(`${APP_VERSION_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    const entry = (body as { entry?: unknown } | null)?.entry;
    return typeof entry === "string" && entry.startsWith("/") ? entry : null;
  } catch {
    return null;
  }
}

/** Compares the deployed build with this tab. Failures leave the state unchanged. */
export function checkForAppUpdate(doc: Document = document): Promise<void> {
  if (checkInFlight) return checkInFlight;
  const running = currentEntry(doc);
  if (!running) return Promise.resolve();
  lastCheckAt = Date.now();
  checkInFlight = fetchLatestEntry()
    .then((entry) => {
      if (!entry || entry === running) return;
      latestEntry = entry;
      if (entry !== dismissedEntry) setState({ ...state, available: true });
    })
    .finally(() => {
      checkInFlight = null;
    });
  return checkInFlight;
}

/** Called when stale code could not be recovered by the automatic reload. */
export function markAppUpdateRequired(): void {
  setState({ available: true, required: true });
}

/** Hides the suggestion until a build newer than the one already offered appears. */
export function dismissAppUpdate(): void {
  if (state.required) return;
  dismissedEntry = latestEntry;
  setState({ ...state, available: false });
}

/**
 * Polls while the tab is visible and checks again when the user returns to it.
 * Does nothing when the document has no built entry script (development).
 */
export function startAppUpdateChecks(doc: Document = document): () => void {
  if (typeof window === "undefined" || !currentEntry(doc)) return () => undefined;

  const checkIfDue = () => {
    if (doc.visibilityState !== "visible") return;
    if (Date.now() - lastCheckAt < MIN_CHECK_GAP_MS) return;
    void checkForAppUpdate(doc);
  };
  const timer = window.setInterval(checkIfDue, CHECK_INTERVAL_MS);
  doc.addEventListener("visibilitychange", checkIfDue);
  window.addEventListener("online", checkIfDue);
  return () => {
    window.clearInterval(timer);
    doc.removeEventListener("visibilitychange", checkIfDue);
    window.removeEventListener("online", checkIfDue);
  };
}
