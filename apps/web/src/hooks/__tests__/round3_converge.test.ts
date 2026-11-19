/**
 * Round-3 收敛阶段回归：前端侧的跨组遗留项。
 *
 * 1) version-quota #1（前端半边）——PUT /files/{id} 的乐观并发：
 *    编辑器提交的是「整篇正文快照」，光靠服务端加锁挡不住丢更新。后端已经支持
 *    base_updated_at + 409(stale_write)，但前端不带这个字段 = 这条防线完全不生效。
 *    另外 409 的 error_detail 是个对象（current_content / current_updated_at），
 *    ApiError 只保留字符串就没法用它刷新编辑器。
 *
 * 2) stream #4（前端半边）——file_edit_end 新增的 failed_count / warnings /
 *    partial_success / all_failed 必须被解析出来，否则「3 条改动只成功 1 条」
 *    在界面上仍然是纯成功。
 *
 * 3) rank32（前端半边）——parallel_start 的 dropped_count 必须被解析出来，
 *    否则用户不知道模型请求的任务有几个根本没执行。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// 静态 import：下面 fileApi 那组用例会 vi.doMock('@/lib/apiClient') + resetModules，
// 之后再动态 import agentApi 会拿到被替换掉的 apiClient（缺 tryRefreshToken 等导出）。
// 在任何 doMock 之前把真实模块绑定进来，就不受它影响。
import { streamAgentRequest } from '@/lib/agentApi'
import { ApiError } from '@/lib/apiClient'

// ---------------------------------------------------------------------------
// 1) ApiError 必须保留结构化的 error_detail
// ---------------------------------------------------------------------------

describe('ApiError 结构化 detail', () => {
  it('保留 error_detail 对象，供 409 冲突恢复使用', () => {
    const error = new ApiError(409, 'ERR_RESOURCE_CONFLICT', {
      reason: 'stale_write',
      current_content: '服务端最新正文',
      current_updated_at: '2026-07-27T00:00:00Z',
    })

    expect(error.status).toBe(409)
    expect(error.errorCode).toBe('ERR_RESOURCE_CONFLICT')
    expect(error.details?.reason).toBe('stale_write')
    expect(error.details?.current_content).toBe('服务端最新正文')
  })

  it('没有结构化 detail 时 details 为 undefined，不影响既有调用方', () => {
    const error = new ApiError(404, 'ERR_FILE_NOT_FOUND')
    expect(error.details).toBeUndefined()
  })
})

describe('apiCall 解析 409 的 error_detail', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    localStorage.setItem('access_token', 'token')
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('把 error_detail 对象挂到 ApiError.details 上', async () => {
    const { apiCall } = await import('@/lib/apiClient')

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        detail: 'ERR_RESOURCE_CONFLICT',
        error_code: 'ERR_RESOURCE_CONFLICT',
        error_detail: {
          reason: 'stale_write',
          current_content: 'AI 刚写完的正文',
          current_updated_at: '2026-07-27T01:02:03Z',
        },
      }),
    }) as unknown as typeof fetch

    await expect(apiCall('/api/v1/files/f1', { method: 'PUT' })).rejects.toMatchObject({
      status: 409,
      details: {
        reason: 'stale_write',
        current_content: 'AI 刚写完的正文',
      },
    })
  })

  it('error_detail 是字符串时不当成结构化 detail', async () => {
    const { apiCall } = await import('@/lib/apiClient')

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({
        detail: 'ERR_VALIDATION_ERROR',
        error_code: 'ERR_VALIDATION_ERROR',
        error_detail: 'bad input',
      }),
    }) as unknown as typeof fetch

    try {
      await apiCall('/api/v1/files/f1', { method: 'PUT' })
      throw new Error('should have thrown')
    } catch (err) {
      expect((err as ApiError).details).toBeUndefined()
    }
  })
})

// ---------------------------------------------------------------------------
// 2) fileApi.update 必须能带上 base_updated_at
// ---------------------------------------------------------------------------

describe('fileApi.update 的乐观并发令牌', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.doUnmock('@/lib/apiClient')
    vi.doUnmock('@/lib/analytics')
    vi.resetModules()
  })

  it('把 base_updated_at 透传到 PUT body', async () => {
    vi.resetModules()
    const put = vi.fn().mockResolvedValue({
      id: 'f1',
      project_id: 'p1',
      file_type: 'draft',
      updated_at: '2026-07-27T02:00:00Z',
    })
    vi.doMock('@/lib/apiClient', () => ({
      api: { get: vi.fn(), post: vi.fn(), put, patch: vi.fn(), delete: vi.fn() },
      ApiError: class extends Error {},
      apiCall: vi.fn(),
      getApiBase: () => '',
    }))
    vi.doMock('@/lib/analytics', () => ({ trackEvent: vi.fn(), captureException: vi.fn() }))

    const { fileApi } = await import('@/lib/api')
    await fileApi.update('f1', {
      content: '新正文',
      base_updated_at: '2026-07-27T01:00:00Z',
    })

    expect(put).toHaveBeenCalledWith(
      '/api/v1/files/f1',
      expect.objectContaining({ base_updated_at: '2026-07-27T01:00:00Z' }),
    )
  })
})

// ---------------------------------------------------------------------------
// 3) SSE 解析：file_edit_end 的失败字段 / parallel_start 的 dropped_count
// ---------------------------------------------------------------------------

describe('agentApi SSE 事件解析', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    localStorage.clear()
    vi.restoreAllMocks()
  })

  function sse(type: string, data: unknown): string {
    // 事件类型取自 "event:" 行，不是 data.type —— 只发 data 会被当成 content。
    return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`
  }

  /**
   * 跑完一次流。done 事件在真实链路上还依赖别的状态才会触发 onComplete，
   * 这里只关心「事件被解析成回调参数」，所以用微小延时等 reader 排空即可。
   */
  async function runStream(
    callbacks: Parameters<typeof streamAgentRequest>[1],
  ): Promise<void> {
    streamAgentRequest({ project_id: 'p1', message: 'hi' }, callbacks)
    await new Promise((resolve) => setTimeout(resolve, 50))
  }

  function mockSseResponse(lines: string[]) {
    const encoder = new TextEncoder()
    const chunks = lines.map((line) => encoder.encode(line))
    let index = 0
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () =>
            index < chunks.length
              ? { done: false, value: chunks[index++] }
              : { done: true, value: undefined },
          releaseLock: () => undefined,
          cancel: async () => undefined,
        }),
      },
    }) as unknown as typeof fetch
  }

  it('file_edit_end 解析出 failedCount / warnings / partialSuccess', async () => {
    localStorage.setItem('access_token', 'token')

    mockSseResponse([
      sse('file_edit_end', {
        file_id: 'f1',
        edits_applied: 1,
        new_length: 100,
        title: '第1章',
        failed_count: 2,
        partial_success: true,
        all_failed: false,
        warnings: ['锚点匹配到多处'],
      }),
      sse('done', {}),
    ])

    const onFileEditEnd = vi.fn()
    await runStream({ onFileEditEnd })

    expect(onFileEditEnd).toHaveBeenCalled()
    const outcome = onFileEditEnd.mock.calls[0][6]
    expect(outcome).toEqual({
      failedCount: 2,
      partialSuccess: true,
      allFailed: false,
      warnings: ['锚点匹配到多处'],
    })
  })

  it('file_edit_end 缺少新字段时给出安全默认值', async () => {
    localStorage.setItem('access_token', 'token')

    mockSseResponse([
      sse('file_edit_end', { file_id: 'f1', edits_applied: 3 }),
      sse('done', {}),
    ])

    const onFileEditEnd = vi.fn()
    await runStream({ onFileEditEnd })

    expect(onFileEditEnd.mock.calls[0][6]).toEqual({
      failedCount: 0,
      partialSuccess: false,
      allFailed: false,
      warnings: [],
    })
  })
})
