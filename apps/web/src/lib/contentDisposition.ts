/**
 * Read the download filename from a Content-Disposition header.
 *
 * Prefers the RFC 5987/6266 `filename*=charset'lang'percent-encoded` form (the
 * export API sends `filename*=UTF-8''{项目名}_正文.txt`) over a plain
 * `filename=`. Parameters are split properly, so a following `; filename="…"`
 * or other parameter never leaks into the name. Returns null when there is no
 * usable name, so the caller keeps its own fallback.
 */
export function parseContentDispositionFilename(header: string | null | undefined): string | null {
  if (!header) return null;

  const params = new Map<string, string>();
  // name=value pairs; value is a quoted string (with \" escapes) or a token.
  const paramPattern = /;\s*([^\s=;]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)/g;
  for (const match of `;${header.replace(/^[^;]*/, '')}`.matchAll(paramPattern)) {
    const name = match[1].toLowerCase();
    let value = match[2].trim();
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
      value = value.slice(1, -1).replace(/\\(.)/g, '$1');
    }
    if (!params.has(name)) params.set(name, value);
  }

  const extended = params.get('filename*');
  if (extended) {
    const decoded = decodeExtendedValue(extended);
    if (decoded) return sanitizeFilename(decoded);
  }

  const plain = params.get('filename');
  if (plain) {
    const cleaned = sanitizeFilename(plain);
    if (cleaned) return cleaned;
  }
  return null;
}

function decodeExtendedValue(value: string): string | null {
  // charset'language'percent-encoded
  const match = /^([^']*)'[^']*'(.*)$/.exec(value);
  if (!match) return null;
  const charset = match[1].trim().toLowerCase();
  const encoded = match[2];
  try {
    if (charset === '' || charset === 'utf-8' || charset === 'utf8') {
      return decodeURIComponent(encoded);
    }
    if (charset === 'iso-8859-1' || charset === 'latin1') {
      return encoded.replace(/%([0-9a-f]{2})/gi, (_m, hex: string) => String.fromCharCode(parseInt(hex, 16)));
    }
  } catch {
    return null;
  }
  return null;
}

/** Drop path parts and control characters a header could smuggle into the name. */
function sanitizeFilename(name: string): string | null {
  // eslint-disable-next-line no-control-regex
  const base = name.split(/[\\/]/).pop()?.replace(/[\u0000-\u001f\u007f]/g, '').trim() ?? '';
  return base && base !== '.' && base !== '..' ? base : null;
}
