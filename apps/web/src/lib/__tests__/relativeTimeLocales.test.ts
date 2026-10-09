import i18next from 'i18next'
import { beforeAll, describe, expect, it } from 'vitest'

const locales = import.meta.glob<Record<string, unknown>>('../../../public/locales/*/*.json', {
  eager: true,
  import: 'default',
})

function* relativeTimeEntries(value: unknown, path: string[] = []): Generator<[string, string]> {
  if (!value || typeof value !== 'object') return
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (typeof child === 'string') {
      if (/Ago(?:_(?:zero|one|two|few|many|other|plural))?$/.test(key)) yield [[...path, key].join('.'), child]
    } else {
      yield* relativeTimeEntries(child, [...path, key])
    }
  }
}

describe('relative-time copy', () => {
  it('every "…Ago" string interpolates the count it is given', () => {
    const missing: string[] = []
    for (const [file, data] of Object.entries(locales)) {
      for (const [key, value] of relativeTimeEntries(data)) {
        if (value.includes('{{count}}')) continue
        // English singular forms may spell out the number ("1 minute ago").
        if (/_one$/.test(key) && /\b1\b/.test(value)) continue
        missing.push(`${file}: ${key} = ${value}`)
      }
    }
    expect(missing).toEqual([])
  })

  describe('editor save status', () => {
    const zh = i18next.createInstance()
    const en = i18next.createInstance()

    beforeAll(async () => {
      const zhEditor = locales['../../../public/locales/zh/editor.json']
      const enEditor = locales['../../../public/locales/en/editor.json']
      await zh.init({ lng: 'zh', resources: { zh: { editor: zhEditor } }, ns: ['editor'] })
      await en.init({ lng: 'en', resources: { en: { editor: enEditor } }, ns: ['editor'] })
    })

    it('shows the number of seconds and minutes since the last save', () => {
      expect(zh.t('editor:secondsAgo', { count: 25 })).toBe('25 秒前')
      expect(zh.t('editor:minutesAgo', { count: 3 })).toBe('3 分钟前')
      expect(en.t('editor:secondsAgo', { count: 25 })).toBe('25 seconds ago')
      expect(en.t('editor:minutesAgo', { count: 1 })).toBe('1 minute ago')
    })
  })
})
