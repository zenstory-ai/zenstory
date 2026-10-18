import { test as base, expect, type Page } from '@playwright/test'
import {
  cleanupOwnedProject,
  createOwnedFile,
  createOwnedProject,
  type OwnedProject,
} from './fixtures/project-test-data'

/**
 * Real project/session APIs plus a page-side deterministic SSE stream. The
 * stream override is fail-closed: no chat test can reach a model provider.
 */
const ENABLE_CHAT_E2E = process.env.E2E_ENABLE_CHAT_E2E === 'true'
const CHAT_OPT_IN_MESSAGE = 'Chat E2E tests are opt-in. Set E2E_ENABLE_CHAT_E2E=true to run.'

type StreamControl = {
  opened: () => boolean
  requests: () => Array<Record<string, unknown>>
  push: (event: string, data: unknown) => void
  close: () => void
}

declare global {
  interface Window { __chatE2EStream?: StreamControl }
}

const test = base.extend<{ ownedProject: OwnedProject }>({
  ownedProject: async ({ page }, provide, testInfo) => {
    const project = await createOwnedProject(page, testInfo, 'chat')
    try {
      await provide(project)
    } finally {
      await cleanupOwnedProject(page, project)
    }
  },
})

async function installDeterministicStream(page: Page) {
  await page.addInitScript(() => {
    const encoder = new TextEncoder()
    let controller: ReadableStreamDefaultController<Uint8Array> | null = null
    const requests: Array<Record<string, unknown>> = []
    const realFetch = window.fetch.bind(window)
    window.__chatE2EStream = {
      opened: () => controller !== null,
      requests: () => requests,
      push: (event, data) => controller?.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
      close: () => { controller?.close(); controller = null },
    }
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.includes('/api/v1/agent/stream')) return realFetch(input, init)
      if (typeof init?.body === 'string') {
        requests.push(JSON.parse(init.body) as Record<string, unknown>)
      }
      const body = new ReadableStream<Uint8Array>({
        start(streamController) {
          controller = streamController
          init?.signal?.addEventListener('abort', () => {
            try { streamController.close() } catch { /* already closed */ }
            controller = null
          }, { once: true })
        },
      })
      return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
    }
  })
}

async function openChat(page: Page, project: OwnedProject) {
  await installDeterministicStream(page)
  await page.goto(`/project/${project.id}`)
  await expect(page.getByTestId('chat-input')).toBeVisible()
  await expect(page.getByTestId('message-list')).toBeVisible()
}

async function send(page: Page, message: string) {
  await page.getByTestId('chat-input').fill(message)
  await page.getByTestId('send-button').click()
  await expect.poll(() => page.evaluate(() => window.__chatE2EStream?.opened() ?? false)).toBe(true)
  await expect(page.getByTestId('message-list')).toContainText(message)
}

async function push(page: Page, event: string, data: unknown) {
  await page.evaluate(([eventName, eventData]) => window.__chatE2EStream?.push(eventName, eventData), [event, data] as const)
}

async function complete(page: Page, content: string, sessionId = 'deterministic-session') {
  await push(page, 'session_started', { session_id: sessionId })
  await push(page, 'content_start', {})
  await push(page, 'content', { text: content })
  await push(page, 'content_end', {})
  await push(page, 'done', { assistant_message_id: `assistant-${Date.now()}`, session_id: sessionId })
  await page.evaluate(() => window.__chatE2EStream?.close())
  await expect(page.getByTestId('message-list')).toContainText(content)
  await expect(page.getByTestId('chat-input')).toBeEnabled()
}

async function emitTool(page: Page, name: string, result: unknown, args: Record<string, unknown> = {}) {
  const id = `tool-${Date.now()}`
  await push(page, 'tool_call', { tool_use_id: id, tool_name: name, arguments: args })
  await push(page, 'tool_result', { tool_use_id: id, tool_name: name, status: 'success', data: result })
}

test.describe('AI Chat', () => {
  test.skip(!ENABLE_CHAT_E2E, CHAT_OPT_IN_MESSAGE)

  test.beforeEach(async ({ page, ownedProject }) => openChat(page, ownedProject))

  test('user can send message to AI', async ({ page }) => {
    await send(page, '帮我创建一个角色')
    await complete(page, '角色创建建议')
    await expect(page.getByTestId('message-list')).toContainText('帮我创建一个角色')
  })

  test('AI response streams correctly', async ({ page }) => {
    await send(page, '写一段简短的描述')
    await push(page, 'content_start', {})
    await push(page, 'content', { text: '第一段流式内容' })
    await expect(page.getByTestId('message-list')).toContainText('第一段流式内容')
    await push(page, 'content', { text: '，第二段内容' })
    await push(page, 'content_end', {})
    await push(page, 'done', { assistant_message_id: 'stream-assistant', session_id: 'stream-session' })
    await page.evaluate(() => window.__chatE2EStream?.close())
    await expect(page.getByTestId('message-list')).toContainText('第一段流式内容，第二段内容')
    await expect(page.getByTestId('chat-input')).toBeEnabled()
  })

  test('tool calls display in UI', async ({ page }) => {
    await send(page, '创建一个大纲文件，标题为“测试大纲”')
    await emitTool(page, 'create_file', { id: 'tool-file', title: '测试大纲', file_type: 'outline' }, { title: '测试大纲' })
    await expect(page.getByTestId('message-list')).toContainText(/create_file|创建文件|测试大纲/i)
    await complete(page, '大纲已创建')
  })

  test('user can cancel streaming response', async ({ page }) => {
    await send(page, '写一个很长的故事，包含很多细节')
    await push(page, 'content_start', {})
    await push(page, 'content', { text: '尚未完成的长故事' })
    await expect(page.getByTestId('stop-button')).toBeVisible()
    await expect(page.getByTestId('stop-button')).toHaveAttribute('aria-disabled', 'false')
    await page.getByTestId('stop-button').click()
    await expect(page.getByTestId('stop-button')).not.toBeVisible()
    await expect(page.getByTestId('chat-input')).toBeEnabled()
  })

  test('file_created event refreshes the tree for an API-persisted file', async ({ page, ownedProject }) => {
    const uniqueTitle = `测试大纲_${Date.now()}`
    await send(page, `创建一个名为“${uniqueTitle}”的大纲文件`)
    const file = await createOwnedFile(page, ownedProject, {
      title: uniqueTitle,
      fileType: 'outline',
      parentFolderTitle: '大纲',
    })
    await emitTool(page, 'create_file', file, { title: uniqueTitle })
    await push(page, 'file_created', { file_id: file.id, file_type: 'outline', title: uniqueTitle })
    await complete(page, `${uniqueTitle}已创建`)
    await expect(page.getByTestId('message-list')).toContainText(/已创建/)
    await expect(page.getByText(uniqueTitle, { exact: true }).first()).toBeVisible()
  })

  test('error displays on AI failure', async ({ page }) => {
    await send(page, '执行一个不存在的命令xyz123')
    await push(page, 'error', { message: '确定性测试错误', code: 'ERR_TEST', retryable: false })
    await page.evaluate(() => window.__chatE2EStream?.close())
    await expect(page.getByTestId('message-list')).toContainText(/这次生成中断了|generation.*interrupted/i)
    await expect(page.getByTestId('message-list')).toContainText(/操作没有完成|did not complete/i)
  })

  test('subsequent messages send the started session id', async ({ page, ownedProject }) => {
    await send(page, '我的主角叫张三')
    await complete(page, '我记住了，主角叫张三', 'context-session')
    await send(page, '告诉我关于主角的信息')
    const requests = await page.evaluate(() => window.__chatE2EStream?.requests() ?? [])
    expect(requests).toHaveLength(2)
    expect(requests[0]).toMatchObject({
      project_id: ownedProject.id,
      message: '我的主角叫张三',
    })
    expect(requests[0]).not.toHaveProperty('session_id')
    expect(requests[1]).toMatchObject({
      project_id: ownedProject.id,
      message: '告诉我关于主角的信息',
      session_id: 'context-session',
    })
    await complete(page, '第二轮已收到', 'context-session')
  })

  test('suggestions appear after inactivity', async ({ page }) => {
    await page.route('**/api/v1/agent/suggest', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ suggestions: ['创建角色', '写一段对话', '添加场景'] }),
    }))
    await send(page, '创建一个角色')
    await complete(page, '角色已建立')
    await page.clock.runFor(12000)
    await expect(page.getByRole('button', { name: '创建角色' })).toBeVisible()
  })

  test('new session clears chat history', async ({ page }) => {
    await send(page, '这是一条测试消息')
    await complete(page, '测试回复')
    await page.getByTitle(/新对话|New chat/i).click()
    await expect(page.getByTestId('message-list')).not.toContainText('这是一条测试消息')
  })
})

test.describe('AI Chat - Tool Results', () => {
  test.skip(!ENABLE_CHAT_E2E, CHAT_OPT_IN_MESSAGE)
  test.beforeEach(async ({ page, ownedProject }) => openChat(page, ownedProject))

  test('create_file tool result shows success', async ({ page }) => {
    await send(page, '创建一个新角色文件')
    await emitTool(page, 'create_file', { id: 'character-1', title: '新角色', file_type: 'character' })
    await complete(page, '已创建新角色文件')
    await expect(page.getByTestId('message-list')).toContainText(/create_file|创建文件|已创建/i)
  })

  test('edit_file tool result shows success status', async ({ page }) => {
    await send(page, '将文件中的原始内容改为修改后的内容')
    await emitTool(page, 'edit_file', { id: 'draft-1', new_length: 6 }, { old_text: '原始内容', new_text: '修改后的内容' })
    await expect(page.getByTestId('message-list')).toContainText(/修改完成|edit done/i)
    await complete(page, '修改完成')
  })

  test('query_files tool shows results list', async ({ page }) => {
    await send(page, '查询所有角色文件')
    await emitTool(page, 'query_files', [{ id: 'character-1', title: '角色甲', file_type: 'character' }])
    await expect(page.getByTestId('message-list')).toContainText(/找到 1 个文件|found 1 files/i)
    await expect(page.getByTestId('message-list')).toContainText('角色甲')
    await complete(page, '查询完成')
  })
})
