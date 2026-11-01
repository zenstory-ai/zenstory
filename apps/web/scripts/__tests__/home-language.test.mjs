import assert from 'node:assert/strict'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { Window } from 'happy-dom'
import * as shell from '../site-shell.mjs'

// Parse the owned fixture as inert HTML; this is not an HTML sanitizer.
const window = new Window()
const template = window.document.createElement('template')
template.innerHTML = shell.homeLanguageScript
const source = template.content.querySelector('script[data-home-language]')?.textContent
assert.equal(typeof source, 'string', 'the root entry needs an executable language selector')
window.close()

const visit = ({ languages, language = 'en-US', cookie = '', pathname = '/', search = '', hash = '' } = {}) => {
  const redirects = []
  const writes = []
  const document = {}
  Object.defineProperty(document, 'cookie', { get: () => cookie, set: value => writes.push(value) })
  runInNewContext(source, {
    document,
    navigator: { languages, language },
    URLSearchParams,
    location: { pathname, search, hash, replace: destination => redirects.push(destination) },
  })
  assert.deepEqual(writes, [], 'automatic detection must not claim a manual preference')
  return redirects
}

test('root entry chooses the first supported browser language and preserves navigation context', () => {
  assert.deepEqual(visit({ languages: ['zh-CN', 'en-US'], search: '?source=guide&q=a%20b', hash: '#examples-h' }), ['/zh?source=guide&q=a%20b#examples-h'])
  assert.deepEqual(visit({ languages: ['en-GB', 'zh-CN'] }), [])
  assert.deepEqual(visit({ languages: ['fr-FR', 'zh-Hant', 'en'] }), ['/zh'])
  assert.deepEqual(visit({ languages: ['fr-FR', 'en-US', 'zh'] }), [])
  assert.deepEqual(visit({ languages: ['fr-FR', 'de-DE'] }), [])
})

test('a manual cookie wins over browser language, and unrelated or unsupported cookie values do not', () => {
  assert.deepEqual(visit({ languages: ['zh-CN'], cookie: 'session=x; zs_lang=en; other=y' }), [])
  assert.deepEqual(visit({ languages: ['en-US'], cookie: 'zs_lang=zh' }), ['/zh'])
  assert.deepEqual(visit({ languages: ['zh-CN'], cookie: 'zs_lang=fr' }), ['/zh'])
  assert.deepEqual(visit({ languages: ['zh-CN'], cookie: 'not_zs_lang=en' }), ['/zh'])
})

test('the explicit English home link works without cookies and does not override a stored choice', () => {
  assert.deepEqual(visit({ languages: ['zh-CN'], search: '?lang=en' }), [])
  assert.deepEqual(visit({ languages: ['zh-CN'], search: '?lang=en', hash: '#examples-h' }), [])
  assert.deepEqual(visit({ languages: ['en-US'], cookie: 'zs_lang=zh', search: '?lang=en' }), ['/zh?lang=en'])
  assert.deepEqual(visit({ languages: ['zh-CN'], search: '?lang=fr' }), ['/zh?lang=fr'])
})

test('the standard navigator.language API covers browsers without a language list', () => {
  assert.deepEqual(visit({ languages: undefined, language: 'zh-TW' }), ['/zh'])
  assert.deepEqual(visit({ languages: [], language: 'zh-CN' }), ['/zh'])
  assert.deepEqual(visit({ languages: [], language: 'en-US' }), [])
})

test('explicit language pages, deep links and internal aliases never use browser detection', () => {
  for (const pathname of ['/zh', '/zh/', '/projects', '/zh/projects', '/docs', '/org-home/']) {
    assert.deepEqual(visit({ pathname, languages: ['zh-CN'], cookie: 'zs_lang=zh' }), [], pathname)
  }
})
