/** Minimal HTTP client for the ZenStory Agent API (`X-Agent-API-Key` auth). */

export const EXIT = {
  OK: 0,
  ERROR: 1,
  USAGE: 2,
  AUTH: 3,
  NOT_FOUND: 4,
  RATE_LIMITED: 5,
} as const;

/** Mirrors RATE_LIMITS in apps/server/services/skill_md_service.py (per key, per hour). */
export const RATE_LIMIT_SUMMARY = 'read 2000/h, write 1000/h, search 500/h, writing-context 500/h per API key';

const DEFAULT_TIMEOUT_MS = 60_000;

export class CliError extends Error {
  readonly exitCode: number;
  readonly status?: number;
  readonly code?: string;

  constructor(message: string, exitCode: number = EXIT.ERROR, extra: { status?: number; code?: string } = {}) {
    super(message);
    this.name = 'CliError';
    this.exitCode = exitCode;
    this.status = extra.status;
    this.code = extra.code;
  }
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ClientOptions {
  apiKey: string;
  apiBase: string;
  userAgent?: string;
  fetch?: FetchLike;
  timeoutMs?: number;
}

export interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  headers?: Record<string, string>;
}

interface ErrorBody {
  detail?: unknown;
  error_code?: unknown;
  error_detail?: unknown;
  errors?: unknown;
}

function stringifyDetail(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatValidationErrors(errors: unknown): string | undefined {
  if (!Array.isArray(errors) || errors.length === 0) return undefined;
  return errors
    .map((e) => {
      const err = e as { loc?: unknown[]; msg?: string };
      const loc = Array.isArray(err.loc) ? err.loc.filter((p) => p !== 'body').join('.') : '';
      return loc ? `${loc}: ${err.msg ?? 'invalid'}` : (err.msg ?? 'invalid');
    })
    .join('; ');
}

/** Turn an HTTP error response into a CliError with a clear, key-free message. */
export function httpError(status: number, body: unknown, retryAfter?: string | null): CliError {
  const b = (body && typeof body === 'object' ? body : {}) as ErrorBody;
  const code = typeof b.error_code === 'string' ? b.error_code : undefined;
  // APIException responses carry the error code in `detail` and the human text in `error_detail`.
  const detail =
    stringifyDetail(b.error_detail) ??
    formatValidationErrors(b.errors) ??
    stringifyDetail(b.detail) ??
    (typeof body === 'string' && body.trim() ? body.trim().slice(0, 300) : undefined);
  const suffix = detail ? `: ${detail}` : '';
  const extra = { status, code };

  if (
    status === 409 && code === 'ERR_RESOURCE_CONFLICT' &&
    b.error_detail !== null && typeof b.error_detail === 'object' &&
    (b.error_detail as { reason?: unknown }).reason === 'stale_write'
  ) {
    return new CliError(
      'File changed on the server. Re-fetch it with `files get`, merge your edits, ' +
        'and retry with the new updated_at.',
      EXIT.ERROR,
      { status, code: 'STALE_WRITE' },
    );
  }

  switch (status) {
    case 401:
      return new CliError(
        `Authentication failed (401)${suffix}. The API key is missing, invalid or expired. ` +
          'Create a key in ZenStory Settings → Agent, then run `zenstory login` and paste it when prompted.',
        EXIT.AUTH,
        extra,
      );
    case 403:
      return new CliError(
        `Permission denied (403)${suffix}. The key may lack the required scope (read/write) ` +
          'or be restricted to other projects. Check the key in ZenStory Settings → Agent.',
        EXIT.AUTH,
        extra,
      );
    case 400:
      return new CliError(`Bad request (400)${suffix}`, EXIT.USAGE, extra);
    case 404:
      return new CliError(`Not found (404)${suffix}. Check the project/file id.`, EXIT.NOT_FOUND, extra);
    case 422:
      return new CliError(`Invalid request (422)${suffix}`, EXIT.USAGE, extra);
    case 429: {
      const wait = retryAfter ? ` Retry after ${retryAfter}s.` : '';
      return new CliError(
        `Rate limit exceeded (429).${wait} Agent API limits: ${RATE_LIMIT_SUMMARY}. ` +
          'Slow down, batch reads (e.g. `files list --fields`), and avoid polling.',
        EXIT.RATE_LIMITED,
        extra,
      );
    }
    case 503:
      return new CliError(`Service unavailable (503)${suffix}. Try again later.`, EXIT.ERROR, extra);
    case 504:
      return new CliError(`Gateway timeout (504)${suffix}. Try again, or narrow the request.`, EXIT.ERROR, extra);
    default:
      return new CliError(`Request failed (${status})${suffix}`, EXIT.ERROR, extra);
  }
}

export class ZenstoryClient {
  private readonly apiKey: string;
  private readonly apiBase: string;
  private readonly userAgent: string;
  private readonly fetchImpl: FetchLike;
  private readonly timeoutMs: number;

  constructor(opts: ClientOptions) {
    this.apiKey = opts.apiKey;
    this.apiBase = opts.apiBase.replace(/\/+$/, '');
    this.userAgent = opts.userAgent ?? 'zenstory-cli';
    this.fetchImpl = opts.fetch ?? ((input, init) => fetch(input, init));
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  url(path: string, query?: RequestOptions['query']): string {
    const url = new URL(`${this.apiBase}${path.startsWith('/') ? path : `/${path}`}`);
    for (const [k, v] of Object.entries(query ?? {})) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
    }
    return url.toString();
  }

  async request<T = unknown>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = {
      'X-Agent-API-Key': this.apiKey,
      Accept: 'application/json',
      'User-Agent': this.userAgent,
      ...opts.headers,
    };
    let body: string | undefined;
    if (opts.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(opts.body);
    }

    const target = this.url(path, opts.query);
    let res: Response;
    try {
      res = await this.fetchImpl(target, {
        method,
        headers,
        body,
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      throw new CliError(
        `Could not reach ${this.apiBase} (${reason}). Check your network and ZENSTORY_API_BASE / --api-base.`,
        EXIT.ERROR,
      );
    }

    const text = await res.text();
    let parsed: unknown = text;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    } else {
      parsed = null;
    }

    if (!res.ok) throw httpError(res.status, parsed, res.headers.get('retry-after'));
    return parsed as T;
  }

  get<T = unknown>(path: string, query?: RequestOptions['query']): Promise<T> {
    return this.request<T>('GET', path, { query });
  }

  post<T = unknown>(path: string, body: unknown, headers?: Record<string, string>): Promise<T> {
    return this.request<T>('POST', path, { body, headers });
  }

  put<T = unknown>(path: string, body: unknown): Promise<T> {
    return this.request<T>('PUT', path, { body });
  }

  delete<T = unknown>(path: string): Promise<T> {
    return this.request<T>('DELETE', path);
  }
}
