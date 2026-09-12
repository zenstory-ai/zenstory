import assert from 'node:assert/strict'
import test from 'node:test'
import { JSDOM } from 'jsdom'
import { micromark } from 'micromark'
import { gfm, gfmHtml } from 'micromark-extension-gfm'
import { normalizeDocsHref, isExternalHref, getDocsDirectory } from '../../src/lib/docs-links.mjs'
import { renderDocsMarkdown } from '../render-docs-markdown.mjs'

const route = '/docs/getting-started/quick-start'
const parse = html => new JSDOM(html).window.document
const baseline = markdown => micromark(markdown, { extensions: [gfm()], htmlExtensions: [gfmHtml()] })

test('shared docs resolver retains paths, complete suffixes and non-doc URL boundaries', () => {
  for (const [href, current, expected] of [
    ['getting-started/quick-start.md', '/docs', route],
    ['getting-started/quick-start.md', '/docs/', route],
    ['./installation.md', route, '/docs/getting-started/installation'],
    ['../reference/faq.md', route, '/docs/reference/faq'],
    ['/reference/faq.MD', route, '/docs/reference/faq'],
    ['/docs/reference/faq.md', route, '/docs/reference/faq'],
    ['/docs', route, '/docs'],
    ['installation.md?q=one?two&step=2#part#detail', route, '/docs/getting-started/installation?q=one?two&step=2#part#detail'],
    ['installation.md?#', route, '/docs/getting-started/installation?#'],
    ['installation.md#part?step=1', route, '/docs/getting-started/installation#part?step=1'],
  ]) assert.equal(normalizeDocsHref(href, current), expected, href)
  for (const href of ['', '#part', '?q=1', '/pricing', '/docs-images/image.png', '/docstore',
    'https://example.com/guide.md?a=1&b=2', 'http://example.com', '//example.com/guide.md',
    'mailto:hello@example.com', 'tel:+123', 'ftp://example.com/guide.md', 'custom:guide.md']) {
    assert.equal(normalizeDocsHref(href, route), null, href)
  }
  assert.equal(getDocsDirectory('/docs'), '/docs/')
  assert.equal(getDocsDirectory(route), '/docs/getting-started/')
  assert.equal(getDocsDirectory('quick-start'), '/docs/')
  assert.equal(isExternalHref('//example.com'), true)
  assert.equal(isExternalHref('https://example.com'), true)
  assert.equal(isExternalHref('installation.md'), false)
})

test('prerender normalizes inline, reference and image-wrapping anchors without changing media', () => {
  const markdown = `[inline](./installation.md "Read next")
[full][target]
[collapsed][]
[shortcut]
[![image](../picture.png "Keep image")](../reference/faq.md)

[target]: ../reference/faq.md
[collapsed]: /reference/glossary.md
[shortcut]: ./first-project.md
`
  const document = parse(renderDocsMarkdown(markdown, route))
  assert.deepEqual([...document.querySelectorAll('a')].map(a => a.getAttribute('href')), [
    '/docs/getting-started/installation', '/docs/reference/faq', '/docs/reference/glossary',
    '/docs/getting-started/first-project', '/docs/reference/faq',
  ])
  assert.equal(document.querySelector('a').getAttribute('title'), 'Read next')
  assert.equal(document.querySelector('img').outerHTML, parse(baseline(markdown)).querySelector('img').outerHTML)
})

test('compiled anchor entities are decoded once and escaped without losing query or fragment data', () => {
  const markdown = `[query](./installation.md?one=1&amp;two=2&literal=&amp;amp;&quote=&quot;#part?x=1#two)
[path](./a&amp;b.md?q=%22%3E%3Cscript%3E)
[numeric](./installation.md?one=1&#38;two=2)
[literal](./installation.md?literal=&amp;#35;)
`
  const before = parse(baseline(markdown))
  const after = parse(renderDocsMarkdown(markdown, route))
  const original = [...before.querySelectorAll('a')].map(a => a.getAttribute('href'))
  const normalized = [...after.querySelectorAll('a')].map(a => a.getAttribute('href'))
  assert.deepEqual(normalized, original.map(href => normalizeDocsHref(href, route)))
  assert.equal(normalized[0], '/docs/getting-started/installation?one=1&two=2&literal=&amp;&quote=%22#part?x=1#two')
  assert.equal(normalized[1], '/docs/getting-started/a&b?q=%22%3E%3Cscript%3E')
  assert.equal(after.querySelectorAll('script, [onerror], [onclick]').length, 0)
})

test('external links, all autolinks, fragments, code and escaped author HTML stay byte-for-byte unchanged', () => {
  const markdown = `[external](https://example.com/a.md?a=1&b=2)
[network](//example.com/a.md?a=1&b=2)
[fragment](#section)
[query](?a=1&b=2)
[mail](mailto:hello@example.com)
<https://example.com/reference.md?a=1&b=2>
<hello@example.com>
https://example.com/literal.md?a=1&b=2
www.example.com/guide.md

![image](./picture.md)

\`[code](./installation.md)\`

\`\`\`html
<a href="./installation.md">code</a>
\`\`\`

<a href="./installation.md">author HTML</a>
`
  assert.equal(renderDocsMarkdown(markdown, route), baseline(markdown))
})

test('postprocessing does not resurrect unsafe protocols or raw HTML', () => {
  const markdown = `[unsafe](javascript:alert%281%29)
[encoded](javascript&#58;alert%281%29)
[data](data:text/html,test)
![image](javascript:alert%281%29)
<script>alert(1)</script>
<img src=x onerror=alert(1)>
`
  const html = renderDocsMarkdown(markdown, route)
  assert.equal(html, baseline(markdown))
  const document = parse(html)
  assert.equal(document.querySelectorAll('script, [onerror]').length, 0)
  for (const anchor of document.querySelectorAll('a')) assert.equal(anchor.getAttribute('href'), '')
  assert.equal(document.querySelector('img').getAttribute('src'), '')
})
