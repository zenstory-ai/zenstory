/**
 * First-touch acquisition: where this browser first came from, sent with the
 * registration (email sign-up body or Google OAuth state) so the admin
 * acquisition view can count new accounts by channel.
 *
 * Kept for 30 days in localStorage. The first visit wins unless it carried no
 * signal (a direct visit) and a later one does. Only codes, the referrer's host
 * name and the landing path are kept: no full URLs, query strings or identifiers.
 * The server re-validates every field and derives the channel.
 */

const ACQUISITION_KEY = 'zenstory:acquisition';
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
const TOKEN_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const ID_SEGMENT = /^(?:[0-9a-f]{8}-[0-9a-f-]{27}|\d+)$/i;
// Returns from our own redirects carry the referrer of the hop, not of the visitor.
const IGNORED_PATHS = ['/auth/callback', '/dashboard/billing/payment-return'];
const IGNORED_REFERRER_HOSTS = ['accounts.google.com', 'api.zenstory.ai'];

export interface Acquisition {
  source?: string;
  utm_source?: string;
  utm_medium?: string;
  utm_campaign?: string;
  referrer_host?: string;
  landing_path?: string;
}

interface StoredAcquisition extends Acquisition {
  captured_at: number;
}

const hasSignal = (touch: Acquisition) =>
  Boolean(touch.source || touch.utm_source || touch.utm_medium || touch.utm_campaign || touch.referrer_host);

function token(value: string | null): string | undefined {
  const normalized = value?.trim().toLowerCase();
  return normalized && TOKEN_PATTERN.test(normalized) ? normalized : undefined;
}

function referrerHost(referrer: string, ownHost: string): string | undefined {
  try {
    const host = new URL(referrer).hostname.toLowerCase();
    if (!host || host === ownHost || IGNORED_REFERRER_HOSTS.includes(host) || host.endsWith('.up.railway.app')) {
      return undefined;
    }
    return host;
  } catch {
    return undefined;
  }
}

function landingPath(pathname: string): string {
  const path = pathname
    .split('/')
    .map((segment) => (ID_SEGMENT.test(segment) ? ':id' : segment))
    .join('/');
  return path.slice(0, 200) || '/';
}

function readStored(): StoredAcquisition | null {
  const raw = localStorage.getItem(ACQUISITION_KEY);
  if (!raw) return null;
  const parsed: unknown = JSON.parse(raw);
  if (!parsed || typeof parsed !== 'object' || typeof (parsed as StoredAcquisition).captured_at !== 'number') {
    return null;
  }
  return parsed as StoredAcquisition;
}

/** Record this visit as the first touch when appropriate. Best effort: storage may be unavailable. */
export function rememberAcquisition(
  location: Pick<Location, 'pathname' | 'search' | 'hostname'> = window.location,
  referrer: string = document.referrer,
  now: number = Date.now(),
): void {
  try {
    if (IGNORED_PATHS.some((path) => location.pathname.startsWith(path))) return;
    const params = new URLSearchParams(location.search);
    const touch: Acquisition = {
      source: token(params.get('source')),
      utm_source: token(params.get('utm_source')),
      utm_medium: token(params.get('utm_medium')),
      utm_campaign: token(params.get('utm_campaign')),
      referrer_host: referrerHost(referrer, location.hostname.toLowerCase()),
      landing_path: landingPath(location.pathname),
    };
    const stored = readStored();
    if (stored && now - stored.captured_at < MAX_AGE_MS && (hasSignal(stored) || !hasSignal(touch))) return;
    const compact = Object.fromEntries(Object.entries(touch).filter(([, value]) => value !== undefined));
    localStorage.setItem(ACQUISITION_KEY, JSON.stringify({ ...compact, captured_at: now }));
  } catch {
    // Attribution is optional; never block the app on storage.
  }
}

/** The recorded first touch without its timestamp, or undefined. */
export function getAcquisition(now: number = Date.now()): Acquisition | undefined {
  try {
    const stored = readStored();
    if (!stored || now - stored.captured_at >= MAX_AGE_MS) return undefined;
    const { captured_at: _capturedAt, ...acquisition } = stored;
    return acquisition;
  } catch {
    return undefined;
  }
}
