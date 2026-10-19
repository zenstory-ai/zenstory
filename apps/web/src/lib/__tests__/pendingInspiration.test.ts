import { describe, it, expect, beforeEach, vi } from 'vitest'
import { hasPendingInspiration, requestChatOnOpen, takeChatOnOpen } from '../pendingInspiration'

const NOW = 1_800_000_000_000

describe('takeChatOnOpen', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('answers yes once for a fresh request', () => {
    requestChatOnOpen('p1', NOW)
    expect(takeChatOnOpen('p1', NOW + 1000)).toBe(true)
    expect(takeChatOnOpen('p1', NOW + 2000)).toBe(false)
  })

  it('clears a stale or unreadable request without opening the chat', () => {
    requestChatOnOpen('p1', NOW)
    expect(takeChatOnOpen('p1', NOW + 6 * 60 * 1000)).toBe(false)
    localStorage.setItem('zenstory_open_chat_p2', 'not-a-time')
    expect(takeChatOnOpen('p2', NOW)).toBe(false)
    expect(localStorage.getItem('zenstory_open_chat_p2')).toBeNull()
    expect(takeChatOnOpen(null, NOW)).toBe(false)
  })

  it('treats unavailable storage as no request', () => {
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => requestChatOnOpen('p1', NOW)).not.toThrow()
    expect(takeChatOnOpen('p1', NOW)).toBe(false)
    expect(hasPendingInspiration('p1', NOW)).toBe(false)
  })
})
