import { describe, expect, it } from 'vitest'
import type { StreamRenderItem } from '../../hooks/useChatStreaming'
import { hasSubstantiveDisplayItems } from '../chatRoundEnd'

const timestamp = new Date()

describe('hasSubstantiveDisplayItems', () => {
  it('a round that only compacted history before failing leaves no bubble', () => {
    // 刷新后这一轮只有作者那条消息（服务端不为只有标记的一轮写回复）：现场也不留只有「对话已压缩」的气泡，
    // 作者消息仍是最后一条，「重试」照常可用。
    const items: StreamRenderItem[] = [
      { type: 'thinking_status', id: 'ctx', content: '正在组装上下文…', transient: true, timestamp },
      { type: 'history_compaction', id: 'compact', compactionState: 'done', content: '摘要', timestamp },
    ]
    expect(hasSubstantiveDisplayItems(items)).toBe(false)
  })

  it('the marker next to real output keeps the bubble', () => {
    const items: StreamRenderItem[] = [
      { type: 'history_compaction', id: 'compact', compactionState: 'done', content: '摘要', timestamp },
      { type: 'content', id: 'reply', content: '第二章写到一半', timestamp },
    ]
    expect(hasSubstantiveDisplayItems(items)).toBe(true)
  })
})
