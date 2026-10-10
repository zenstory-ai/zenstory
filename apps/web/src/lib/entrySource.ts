/**
 * The `?source=` a visitor first arrived with in this tab, e.g. `org_header` from a zenstory.ai link.
 * Kept for the signup event: between landing and signing up a visitor often moves from /login to
 * /register or out through Google and back, and those URLs no longer carry it.
 */
const ENTRY_SOURCE_KEY = 'zenstory:entry-source';
const SOURCE_PATTERN = /^[a-z0-9_]{1,64}$/;

/** Remember the landing URL's `source` unless this tab already has one. Best effort: storage may be unavailable. */
export function rememberEntrySource(search: string = window.location.search): void {
  try {
    const source = new URLSearchParams(search).get('source');
    if (!source || !SOURCE_PATTERN.test(source) || sessionStorage.getItem(ENTRY_SOURCE_KEY)) return;
    sessionStorage.setItem(ENTRY_SOURCE_KEY, source);
  } catch {
    // Attribution is optional; never block the app on storage.
  }
}

export function getEntrySource(): string | undefined {
  try {
    return sessionStorage.getItem(ENTRY_SOURCE_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}
