import { describe, expect, it } from 'vitest'
import { formatHandoffMessage, sanitizeAgentText } from '../agentDisplayName'

const zh: Record<string, string> = {
  'chat:workflow.agents.writer': '内容创作者',
  'chat:workflow.agents.quality_reviewer': '质量审稿人',
}

const t = (key: string, options?: Record<string, unknown>) => {
  if (key === 'chat:workflow.charCount') return `${options?.count} 字`
  if (key === 'chat:workflow.handoffMessage') return `接下来由${options?.agent}继续：${options?.reason}`
  if (key === 'chat:workflow.handoffMessageShort') return `接下来由${options?.agent}继续`
  return zh[key] ?? key
}

describe('sanitizeAgentText', () => {
  it('drops a parenthesised id and rewrites word_count as an author-facing length', () => {
    expect(
      sanitizeAgentText(
        '第2章《他不是我男朋友》(id=68212e4b-fe8e-4238-ae5b-a57233195b69) 经核对 word_count=2434，低于作者要求的单章 2500 字以上。',
        t,
      ),
    ).toBe('第2章《他不是我男朋友》经核对 2434 字，低于作者要求的单章 2500 字以上。')
  })

  it('keeps the rest of a bracket when only the id is removed', () => {
    expect(
      sanitizeAgentText('第3章 报案人（id=b72bdec4-4706-472d-aaf9-1d3ae3379839，当前2996字）存在三处问题', t),
    ).toBe('第3章 报案人（当前2996字）存在三处问题')
  })

  it('removes bare UUIDs, *_id fields, content_length and bracketed tool names', () => {
    expect(
      sanitizeAgentText(
        '[query_files] 读取 7f1c2a9e-1111-4222-8333-944455556666 的全文，file_id: abc-123，content_length=512',
        t,
      ),
    ).toBe('读取的全文，512 字')
  })

  it('leaves ordinary words that merely end in "id" alone', () => {
    expect(sanitizeAgentText('As the author said: keep the ending', t)).toBe('As the author said: keep the ending')
  })

  it('returns plain reasons unchanged', () => {
    expect(sanitizeAgentText('第2章正文已完稿，需通读核对设定一致性、逻辑与钩子强度。', t)).toBe(
      '第2章正文已完稿，需通读核对设定一致性、逻辑与钩子强度。',
    )
  })
})

describe('formatHandoffMessage', () => {
  it('sanitizes the reason before showing it to the author', () => {
    expect(
      formatHandoffMessage(
        { target_agent: 'writer', reason: '第2章(id=68212e4b-fe8e-4238-ae5b-a57233195b69) word_count=2434，需补齐' },
        t,
      ),
    ).toBe('接下来由内容创作者继续：第2章 2434 字，需补齐')
  })

  it('falls back to the short message when nothing readable is left', () => {
    expect(
      formatHandoffMessage({ target_agent: 'quality_reviewer', reason: '(id=68212e4b-fe8e-4238-ae5b-a57233195b69)' }, t),
    ).toBe('接下来由质量审稿人继续')
  })
})
