import { describe, expect, it } from 'vitest'
import { formatVersionSummary } from '../versionSummary'
import enVersions from '../../../public/locales/en/versions.json'
import zhVersions from '../../../public/locales/zh/versions.json'

type Options = Record<string, unknown>

const loadLocale = (locale: 'zh' | 'en') =>
  (locale === 'zh' ? zhVersions : enVersions) as Record<string, unknown>

const interpolate = (template: string, options?: Options) =>
  template.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options?.[name] ?? ''))

/** Minimal i18next stand-in that resolves `versions:` keys from a real locale file. */
const localeTranslator = (locale: 'zh' | 'en') => {
  const messages = loadLocale(locale)
  return (key: string, options?: Options) => {
    const [, path] = key.split(':')
    const value = path.split('.').reduce<unknown>(
      (node, part) => (node as Record<string, unknown> | undefined)?.[part],
      messages,
    )
    if (typeof value !== 'string') throw new Error(`missing ${locale} key ${key}`)
    return interpolate(value, options)
  }
}

/** Translator that only knows the inline fallbacks, i.e. what users see before locales load. */
const fallbackTranslator = (_key: string, options?: Options) =>
  interpolate(String(options?.defaultValue ?? ''), options)

const zh = localeTranslator('zh')
const en = localeTranslator('en')

const CASES: Array<[string, string]> = [
  ['Restored to version 7', '恢复到版本 7'],
  ['Before restoring version 3', '恢复前自动备份'],
  ['Before rollback to snapshot 0f8fad5b-d9cb-469f-a165-70867728950e', '恢复前自动备份'],
  ['Restored from snapshot 0f8fad5b-d9cb-469f-a165-70867728950e', '从项目快照恢复'],
  ['Before AI edit', 'AI 修改前自动备份'],
  ['AI edit (reviewed)', 'AI 修改（已审阅）'],
  ['File updated', '手动编辑'],
  ['Initial version', '初始版本'],
  ['创建文件', '初始版本'],
  ['Snapshot baseline version', '拍项目快照时自动保存'],
  ['Snapshot synchronized live content', '拍项目快照时自动保存'],
  ['AI 对话完成 - 文件已修改', 'AI 修改后自动存档'],
  ['AI Chat Finished - files modified', 'AI 修改后自动存档'],
  ['AI conversation completed - files changed', 'AI 修改后自动存档'],
]

describe('formatVersionSummary', () => {
  it.each(CASES)('maps %j to the Chinese label', (summary, expected) => {
    expect(formatVersionSummary(summary, zh)).toBe(expected)
  })

  it.each(CASES)('keeps the inline fallback for %j identical to the zh locale', (summary, expected) => {
    expect(formatVersionSummary(summary, fallbackTranslator)).toBe(expected)
  })

  it('resolves every mapped label in the English locale too', () => {
    expect(formatVersionSummary('Restored to version 2', en)).toBe('Restored to version 2')
    expect(formatVersionSummary('Before restoring version 2', en)).toBe('Automatic backup before restore')
    for (const [summary] of CASES) {
      expect(formatVersionSummary(summary, en)).not.toMatch(/[一-鿿]/)
    }
  })

  it('returns unknown summaries unchanged except for UUIDs', () => {
    expect(formatVersionSummary('修改第三章开头', zh)).toBe('修改第三章开头')
    expect(
      formatVersionSummary('Merged from 0F8FAD5B-D9CB-469F-A165-70867728950E and 7c9e6679-7425-40de-944b-e07fc1f90ae7', zh),
    ).toBe('Merged from … and …')
  })

  it('treats empty summaries as no summary', () => {
    expect(formatVersionSummary(undefined, zh)).toBe('')
    expect(formatVersionSummary(null, zh)).toBe('')
    expect(formatVersionSummary('   ', zh)).toBe('')
  })
})
