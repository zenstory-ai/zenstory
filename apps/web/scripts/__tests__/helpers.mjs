/** Shared helpers for the site generator tests (not a test file itself). */

/** Chinese pages point organization links written as absolute zenstory.ai URLs at the /zh site (single-URL pages stay). */
export const zhLinks = (html) => html.replace(/href="https:\/\/zenstory\.ai(\/(?!zh(?:[/?#"])|docs(?:[/?#"])|privacy-policy|terms-of-service|llms\.txt)[^"]*)"/g, (m, path) => `href="${path === '/' ? '/zh' : `/zh${path}`}"`)
