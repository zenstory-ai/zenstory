import { micromark } from 'micromark'
import { gfm, gfmHtml } from 'micromark-extension-gfm'
import { normalizeDocsHref } from '../src/lib/docs-links.mjs'

// micromark serializes these four characters in attributes. Decode exactly
// once before URL resolution, then escape again to avoid double-encoding &.
const entities = { amp: '&', quot: '"', lt: '<', gt: '>' }
const decodeAttribute = value => value.replace(/&(amp|quot|lt|gt);/g, (_, name) => entities[name])
const escapeAttribute = value => value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export function renderDocsMarkdown(markdown, currentPathname) {
  const html = micromark(markdown, {
    extensions: [gfm()],
    htmlExtensions: [gfmHtml()],
    allowDangerousHtml: false,
    allowDangerousProtocol: false,
  })
  // This is compiler output, NOT an arbitrary-HTML sanitizer. Safe micromark
  // emits anchors in this exact form and escapes author HTML/code. Rewriting
  // after compilation covers inline/reference/GFM links without replacing its
  // private media handlers or touching image src, code, title or link text.
  return html.replace(/<a href="([^"]*)"/g, (anchor, encodedHref) => {
    const href = normalizeDocsHref(decodeAttribute(encodedHref), currentPathname)
    return href === null ? anchor : `<a href="${escapeAttribute(href)}"`
  })
}
