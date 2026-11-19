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

  it('strips persisted MiniMax or XML tool-call protocol leaks while preserving inline, fenced, and prose mentions', () => {
    const rawOnlyLeak =
      ']<]minimax[>[<tool_call> ]<]minimax[>[</invoke:query_files"> <parameter name="query">all]<]minimax[>[parameter></invoke>'
    expect(formatCompactionSummary(rawOnlyLeak)).toBe('')

    const mixed = [
      '## 已完成进度',
      '- 第1章已定稿，作者要求避免 `<tool_call>` 字面标签与 `` `<invoke>` ``。',
      '- 原话：「禁止输出 <tool_call>」，且满足 a < parameter and b > c。',
      '~~~xml',
      '<tool_call><invoke name="example" /></tool_call>',
      '~~~',
      ']<]minimax[>[<tool_call>',
      '<invoke name="query_files"><parameter name="query">all</parameter></invoke>',
      '</tool_call>',
    ].join('\r\n')

    expect(formatCompactionSummary(mixed)).toBe(
      [
        '## 已完成进度',
        '- 第1章已定稿，作者要求避免 `<tool_call>` 字面标签与 `` `<invoke>` ``。',
        '- 原话：「禁止输出 <tool_call>」，且满足 a < parameter and b > c。',
        '~~~xml',
        '<tool_call><invoke name="example" /></tool_call>',
        '~~~',
      ].join('\n'),
    )
  })
})
