/**
 * OAuth callback credentials arrive in the URL (`/auth/callback#access_token=...`).
 * Read them once at boot, before analytics, error tracking or any other code can
 * observe `window.location`, and remove them from the address bar. The
 * OAuthCallback page then takes them from here.
 */

export const AUTH_CALLBACK_PATH = "/auth/callback";

let capturedParams: URLSearchParams | null = null;

/**
 * Must run before `initAnalytics()`. Query parameters win over hash parameters,
 * matching how the callback page used to read them.
 */
export function captureAuthCallbackParams(): void {
  if (typeof window === "undefined") return;
  if (window.location.pathname !== AUTH_CALLBACK_PATH) return;

  const query = window.location.search.replace(/^\?/, "");
  const hash = window.location.hash.replace(/^#/, "");
  if (!query && !hash) return;

  const params = new URLSearchParams(query);
  new URLSearchParams(hash).forEach((value, key) => {
    if (!params.has(key)) params.set(key, value);
  });
  capturedParams = params;

  window.history.replaceState(window.history.state, document.title, window.location.pathname);
}

/** Returns the captured callback parameters once, then forgets them. */
export function takeAuthCallbackParams(): URLSearchParams | null {
  const params = capturedParams;
  capturedParams = null;
  return params;
}
