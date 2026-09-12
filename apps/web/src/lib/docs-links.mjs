// Shared by the React docs renderer and the Node prerenderer. This resolves
// documentation links, not arbitrary URLs; Markdown renderers own sanitization.
const DOCS_BASE_PATH = '/docs';

export function isExternalHref(href) {
  return /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(href);
}

export function getDocsDirectory(pathname) {
  if (pathname === DOCS_BASE_PATH || pathname === `${DOCS_BASE_PATH}/`) {
    return `${DOCS_BASE_PATH}/`;
  }
  const lastSlashIndex = pathname.lastIndexOf('/');
  return lastSlashIndex <= 0 ? `${DOCS_BASE_PATH}/` : pathname.slice(0, lastSlashIndex + 1);
}

export function normalizeDocsHref(href, currentPathname) {
  if (!href || href.startsWith('#') || isExternalHref(href)) return null;

  // Keep the entire suffix verbatim, including additional ?/# and empty values.
  const suffixIndex = href.search(/[?#]/);
  const rawPath = suffixIndex < 0 ? href : href.slice(0, suffixIndex);
  const suffix = suffixIndex < 0 ? '' : href.slice(suffixIndex);
  if (!rawPath) return null;

  let resolvedPath;
  if (rawPath.startsWith('/')) {
    if (rawPath === DOCS_BASE_PATH || rawPath.startsWith(`${DOCS_BASE_PATH}/`)) {
      resolvedPath = rawPath;
    } else if (/\.md$/i.test(rawPath)) {
      // Historical absolute Markdown paths are relative to the docs root.
      resolvedPath = `${DOCS_BASE_PATH}${rawPath}`;
    } else {
      return null;
    }
  } else {
    resolvedPath = new URL(rawPath, `https://zenstory.local${getDocsDirectory(currentPathname)}`).pathname;
    if (resolvedPath !== DOCS_BASE_PATH && !resolvedPath.startsWith(`${DOCS_BASE_PATH}/`)) {
      resolvedPath = `${DOCS_BASE_PATH}${resolvedPath}`;
    }
  }
  return resolvedPath.replace(/\.md$/i, '') + suffix;
}
