import { describe, expect, it } from 'vitest'
import { describeVersionSummary } from '../versionSummary'
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
  ['Snapshot baseline version', '拍项目快照时自动保存'],
  ['Snapshot synchronized live content', '拍项目快照时自动保存'],
  ['AI 对话完成 - 文件已修改', 'AI 修改后自动存档'],
  ['AI Chat Finished - files modified', 'AI 修改后自动存档'],
  ['AI conversation completed - files changed', 'AI 修改后自动存档'],
  ['Restored from snapshot snap-42', '从项目快照恢复'],
  ['Created via Agent API', '通过 Agent API 创建'],
  ['Updated via Agent API', '通过 Agent API 更新'],
  ['AI 编辑: 替换', 'AI 改了 1 处'],
  ['AI 编辑：前置、删除', 'AI 改了 2 处'],
  ['AI 编辑: 替换、替换、替换', 'AI 改了 3 处'],
  ['AI 编辑: 替换, 追加, 插入 等 5 处修改', 'AI 改了 5 处'],
]

/** System summaries that only repeat the type badge (创建 / 编辑 / AI 编辑). */
const REDUNDANT = ['File updated', 'Initial version', '创建文件', 'AI 更新文件内容', 'AI 编辑']

describe('describeVersionSummary', () => {
  it.each(CASES)('maps %j to the Chinese label', (summary, expected) => {
    expect(describeVersionSummary(summary, zh)).toBe(expected)
  })

  it.each(CASES)('keeps the inline fallback for %j identical to the zh locale', (summary, expected) => {
    expect(describeVersionSummary(summary, fallbackTranslator)).toBe(expected)
  })

  it('resolves every mapped label in the English locale too', () => {
    expect(describeVersionSummary('Restored to version 2', en)).toBe('Restored to version 2')
    expect(describeVersionSummary('Before restoring version 2', en)).toBe('Automatic backup before restore')
    expect(describeVersionSummary('AI 编辑: 替换, 追加, 插入 等 5 处修改', en)).toBe('AI changed 5 places')
    for (const [summary] of CASES) {
      expect(describeVersionSummary(summary, en)).not.toMatch(/[一-鿿]/)
    }
  })

  it('returns unknown summaries unchanged except for UUIDs', () => {
    expect(describeVersionSummary('修改第三章开头', zh)).toBe('修改第三章开头')
    expect(
      describeVersionSummary('Merged from 0F8FAD5B-D9CB-469F-A165-70867728950E and 7c9e6679-7425-40de-944b-e07fc1f90ae7', zh),
    ).toBe('Merged from … and …')
  })

  it.each(REDUNDANT)('hides %j because the type badge already says it', (summary) => {
    expect(describeVersionSummary(summary, zh)).toBeNull()
  })

  it('counts the places changed instead of listing operation names, even unfamiliar ones', () => {
    const summary = describeVersionSummary('AI 编辑: 替换, 重写', zh)
    expect(summary).toBe('AI 改了 2 处')
    expect(summary).not.toMatch(/替换|重写/)
  })

  it('treats empty summaries as no summary', () => {
    expect(describeVersionSummary(undefined, zh)).toBeNull()
    expect(describeVersionSummary(null, zh)).toBeNull()
    expect(describeVersionSummary('   ', zh)).toBeNull()
  })

  it('keeps zh and en summary keys in parity', () => {
    const keysOf = (node: unknown, prefix = ''): string[] =>
      Object.entries(node as Record<string, unknown>).flatMap(([key, value]) =>
        typeof value === 'string' ? [`${prefix}${key}`] : keysOf(value, `${prefix}${key}.`),
      )
    expect(keysOf(loadLocale('en').summary)).toEqual(keysOf(loadLocale('zh').summary))
  })
})
