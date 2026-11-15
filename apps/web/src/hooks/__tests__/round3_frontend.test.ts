/**
 * Round-3 回归：useAgentStream 的「非正常终止路径」。
 *
 * 缺陷 #12 / #18 同一根因——所有落地与清理逻辑都只挂在 SSE done 驱动的
 * onComplete 上，取消 / SSE error / 代理掐断（STREAM_CLOSED）三条路径
 * 没有任何等价钩子：
 *   #12 ProjectContext.streamingFileId 永不释放 → AI 新建文件被锁只读；
 *   #18 已流式渲染的正文永远进不了 messages，下一轮 onStart 把它整段抹掉。
 *
 * 缺陷 #24 的一半：steering 只能投递给本轮流已确认的 session，
 * 陈旧 session id 会稳定 404，进而让 MessageInput 误判「已送达」并清空输入。
 */
import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useAgentStream } from '../useAgentStream'
import type { MessageSegment, StreamCompletionMeta } from '../useAgentStream'
import * as agentApi from '@/lib/agentApi'

vi.mock('@/lib/agentApi', () => ({
  streamAgentRequest: vi.fn(),
  sendSteeringRequest: vi.fn(),
}))

function createMockStreamController() {
  let callbacks: Parameters<typeof agentApi.streamAgentRequest>[1] | null = null

  const mockAbortController = {
    abort: vi.fn(),
    signal: {} as AbortSignal,
  }

  vi.mocked(agentApi.streamAgentRequest).mockImplementation((_request, cbs) => {
    callbacks = cbs
    return mockAbortController as unknown as AbortController
  })

  return {
    mockAbortController,
    getCallbacks: () => callbacks!,
  }
}

type CompleteCall = [MessageSegment[], unknown, StreamCompletionMeta | undefined]

describe('round3 前端终止路径', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    vi.mocked(agentApi.sendSteeringRequest).mockResolvedValue({
      message_id: 'steer-1',
      queued: true,
    })
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  describe('#12/#18 取消路径必须补发终止回调', () => {
    it('cancel() 会以 partial 标记提交已流式渲染的正文', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('project-1', { onComplete, contentFlushIntervalMs: 0 })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: '写第三章，要 3000 字' })
      })

      const callbacks = controller.getCallbacks()
      act(() => {
        callbacks.onSessionStarted?.('session-cancel-1')
        callbacks.onContentStart?.()
        callbacks.onContent?.('夜色如墨，')
        callbacks.onContent?.('风从窗缝挤进来。')
      })

      // 用户点击停止按钮
      act(() => {
        result.current.stop()
      })

      expect(controller.mockAbortController.abort).toHaveBeenCalled()
      expect(onComplete).toHaveBeenCalledTimes(1)

      const [segments, applyAction, meta] = onComplete.mock.calls[0] as CompleteCall
      expect(applyAction).toBeNull()
      expect(meta?.partial).toBe(true)
      expect(meta?.sessionId).toBe('session-cancel-1')

      const text = segments
        .filter((s) => s.type === 'content')
        .map((s) => s.content ?? '')
        .join('')
      expect(text).toBe('夜色如墨，风从窗缝挤进来。')
      // 提交出去的 segment 不能还标着「流式中」
      expect(segments.every((s) => s.isStreaming !== true)).toBe(true)
    })

    it('cancel() 后即使没有 done，也不会因为后续 done 而重复提交', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('project-1', { onComplete, contentFlushIntervalMs: 0 })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })
      const callbacks = controller.getCallbacks()

      act(() => {
        callbacks.onContentStart?.()
        callbacks.onContent?.('片段')
        result.current.stop()
      })

      // abort 之后迟到的 done（isStaleEvent 本就会拦），再补一刀确认幂等
      act(() => {
        callbacks.onDone?.({})
      })

      expect(onComplete).toHaveBeenCalledTimes(1)
    })

    it('正常 done 之后再 cancel()，不会补发第二次 onComplete', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('project-1', { onComplete, contentFlushIntervalMs: 0 })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })
      const callbacks = controller.getCallbacks()

      act(() => {
        callbacks.onContentStart?.()
        callbacks.onContent?.('完整正文')
        callbacks.onDone?.({})
      })
      expect(onComplete).toHaveBeenCalledTimes(1)

      act(() => {
        result.current.stop()
      })
      expect(onComplete).toHaveBeenCalledTimes(1)
    })

    it('reset()（切项目路径）不会把上一个项目的残留正文提交出去', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('project-1', { onComplete, contentFlushIntervalMs: 0 })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })
      const callbacks = controller.getCallbacks()
      act(() => {
        callbacks.onContentStart?.()
        callbacks.onContent?.('旧项目的半截正文')
      })

      act(() => {
        result.current.reset()
      })

      expect(onComplete).not.toHaveBeenCalled()
    })
  })

  describe('#18 SSE error / 代理掐断路径必须补发终止回调', () => {
    it('onError 之后同样以 partial 标记提交正文', () => {
      const onComplete = vi.fn()
      const onError = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('project-1', { onComplete, onError, contentFlushIntervalMs: 0 })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })
      const callbacks = controller.getCallbacks()

      act(() => {
        callbacks.onSessionStarted?.('session-err-1')
        callbacks.onContentStart?.()
        callbacks.onContent?.('已经写出来的两段。')
        // agentApi 在反向代理掐断空闲 SSE 时发的就是这个
        callbacks.onError?.('Stream closed unexpectedly', 'STREAM_CLOSED', true)
      })

      expect(onError).toHaveBeenCalledWith('Stream closed unexpectedly', 'STREAM_CLOSED', true)
      expect(onComplete).toHaveBeenCalledTimes(1)

      const [segments, , meta] = onComplete.mock.calls[0] as CompleteCall
      expect(meta?.partial).toBe(true)
      expect(
        segments
          .filter((s) => s.type === 'content')
          .map((s) => s.content ?? '')
          .join('')
      ).toBe('已经写出来的两段。')
    })

    it('onError 先于 onComplete 触发，保证清理回调仍在正文落地前跑', () => {
      const order: string[] = []
      const { result } = renderHook(() =>
        useAgentStream('project-1', {
          contentFlushIntervalMs: 0,
          onComplete: () => order.push('complete'),
          onError: () => order.push('error'),
        })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      act(() => {
        controller.getCallbacks().onError?.('boom')
      })

      expect(order).toEqual(['error', 'complete'])
    })
  })

  describe('#24 steering 只认本轮流已确认的 session', () => {
    it('新一轮流在 session_started 之前不允许 steering（避免陈旧 id 必然 404）', async () => {
      const { result } = renderHook(() => useAgentStream('project-1'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: '第一轮' })
      })
      act(() => {
        controller.getCallbacks().onSessionStarted?.('session-old')
      })
      expect(result.current.sessionId).toBe('session-old')

      act(() => {
        controller.getCallbacks().onDone?.({})
      })

      // 第二轮：isStreaming 同步置 true，但后端还没注册 steering 队列
      act(() => {
        result.current.startStream({ message: '第二轮' })
      })

      // canSteer = isStreaming && !!sessionId —— 此刻必须为 false
      expect(result.current.sessionId).toBeNull()
      await expect(result.current.sendSteeringMessage('补充一句')).rejects.toThrow(
        'No active session for steering'
      )
      expect(agentApi.sendSteeringRequest).not.toHaveBeenCalled()

      // 收到本轮 session_started 之后才放行
      act(() => {
        controller.getCallbacks().onSessionStarted?.('session-new')
      })
      expect(result.current.sessionId).toBe('session-new')

      await act(async () => {
        await result.current.sendSteeringMessage('补充一句')
      })
      expect(agentApi.sendSteeringRequest).toHaveBeenCalledWith('session-new', '补充一句')
    })

    it('会话延续仍然生效：第二轮请求依旧带上一轮的 session_id', () => {
      const { result } = renderHook(() => useAgentStream('project-1'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: '第一轮' })
      })
      act(() => {
        controller.getCallbacks().onSessionStarted?.('session-keep')
        controller.getCallbacks().onDone?.({})
      })

      act(() => {
        result.current.startStream({ message: '第二轮' })
      })

      const [request] = vi.mocked(agentApi.streamAgentRequest).mock.calls.at(-1)!
      expect(request.session_id).toBe('session-keep')
    })
  })
})
