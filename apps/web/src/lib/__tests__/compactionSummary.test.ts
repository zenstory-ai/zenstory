import { describe, expect, it } from 'vitest'
import { formatCompactionSummary } from '../compactionSummary'

describe('formatCompactionSummary', () => {
  it('returns empty string for missing or blank summaries', () => {
    expect(formatCompactionSummary(undefined)).toBe('')
    expect(formatCompactionSummary('   \n  ')).toBe('')
  })

  it('strips bare, backticked, and parenthesized file or folder UUIDs while keeping fenced code', () => {
    const raw = [
      '## 目录与文件',
      '- 正文目录 `09ab1059-6f47-440c-8ef3-8a47bfb38943-draft-folder`',
      '- 第一章 雨夜（文件 id: de904a54-c7b1-4fa0-be3c-ee0d4033048d）：作者原稿，380 字',
      '- `de904a54-c7b1-4fa0-be3c-ee0d4033048d`',
      '```txt',
      'keep de904a54-c7b1-4fa0-be3c-ee0d4033048d inside code',
      '```',
    ].join('\n')

    const formatted = formatCompactionSummary(raw)
    expect(formatted).toContain('- 正文目录')
    expect(formatted).toContain('- 第一章 雨夜：作者原稿，380 字')
    expect(formatted).not.toContain('09ab1059-6f47-440c-8ef3-8a47bfb38943')
    expect(formatted).toContain('keep de904a54-c7b1-4fa0-be3c-ee0d4033048d inside code')
  })

  it('converts wide Markdown pipe tables into compact bullet items without ID columns', () => {
    const raw = [
      '| 标题 | 文件 ID | 类型 | 状态 |',
      '| :--- | :---: | --- | ---: |',
      '| **第1章 钟楼** | `de904a54-c7b1-4fa0-be3c-ee0d4033048d` | 正文 | 已保存 \\| 380 字 |',
      '| - | `09ab1059-6f47-440c-8ef3-8a47bfb38943` | - | — |',
      '| 人物卡 | `11111111-2222-3333-4444-555555555555` | - | - |',
    ].join('\n')

    expect(formatCompactionSummary(raw)).toBe(
      [
        '- **第1章 钟楼**：正文 · 已保存 | 380 字',
        '- **人物卡**',
      ].join('\n'),
    )
  })

  it('handles UUID/project_id labels, single-dash or pipeless GFM tables, partial bold, and trailing prose separation', () => {
    const raw = [
      '标题 | UUID',
      '- | -',
      '**第1章** 雨夜 | de904a54-c7b1-4fa0-be3c-ee0d4033048d',
      '下一段说明（UUID: 09ab1059-6f47-440c-8ef3-8a47bfb38943）与【project_id: 11111111-2222-3333-4444-555555555555】。',
    ].join('\n')

    expect(formatCompactionSummary(raw)).toBe(
      [
        '- **第1章** 雨夜',
        '',
        '下一段说明与。',
      ].join('\n'),
    )
  })
})
