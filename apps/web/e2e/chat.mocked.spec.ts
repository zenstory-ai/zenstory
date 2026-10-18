import { test as base, expect, Page } from '@playwright/test'
import { cleanupOwnedProject, createOwnedProject, type OwnedProject } from './fixtures/project-test-data'

/**
 * AI Chat E2E Tests with Mocked AI Responses
 *
 * These tests use Playwright routing to intercept AI API calls
 * and return pre-recorded responses. This makes tests:
 * - Fast: No 30-45 second waits for real AI responses
 * - Deterministic: Same input always produces same output
 * - Reliable: No flaky tests due to API rate limits or network issues
 */

const test = base.extend<{ ownedProject: OwnedProject }>({
  ownedProject: async ({ page }, provide, testInfo) => {
    const project = await createOwnedProject(page, testInfo, 'chat-mocked')
    try {
      await provide(project)
    } finally {
      await cleanupOwnedProject(page, project)
    }
  },
})

const ENABLE_MOCKED_CHAT_E2E = process.env.E2E_ENABLE_MOCKED_CHAT === 'true'
const MOCKED_CHAT_OPT_IN_MESSAGE = 'Mocked chat E2E tests are opt-in. Set E2E_ENABLE_MOCKED_CHAT=true to run.'

const getChatInput = (page: Page) =>
  page.locator('[data-testid="chat-input"], textarea[placeholder*="输入"], textarea[placeholder*="Type"]').first()

const getSendButton = (page: Page) =>
  page.locator('[data-testid="send-button"], button[type="submit"], button:has([class*="Send"])').last()

const pendingStreams = new WeakMap<Page, Set<() => void>>()

function holdStream(page: Page): Promise<void> {
  return new Promise((resolve) => {
    const releases = pendingStreams.get(page) ?? new Set<() => void>()
    const release = () => {
      releases.delete(release)
      resolve()
    }
    releases.add(release)
    pendingStreams.set(page, releases)
  })
}

function releaseStreams(page: Page) {
  for (const release of pendingStreams.get(page) ?? []) release()
}

async function mockAgentEndpoints(page: Page) {
  await page.route('**/api/v1/agent/stream', async (route) => {
    const request = route.request().postDataJSON() as { message?: string }
    const message = request.message ?? ''
    if (message === '写一段描述') {
      await holdStream(page)
    }
    if (route.request().failure()) return
    const responseText = message.includes('主角的信息')
      ? '主角张三正在开始新的冒险。'
      : '这是一个示例AI响应。我可以帮助您创作小说内容，包括人物对话、场景描述和情节发展。'
    const stream = [
      'event: thinking',
      'data: {"message":"正在分析您的请求..."}',
      '',
      'event: content_start',
      'data: {}',
      '',
      'event: content',
      `data: ${JSON.stringify({ text: responseText })}`,
      '',
      'event: content_end',
      'data: {}',
      '',
      'event: done',
      'data: {"assistant_message_id":"mock-assistant-1","session_id":"mock-session-1"}',
      '',
    ].join('\n')

    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      },
      body: stream,
    })
  })

  await page.route('**/api/v1/agent/suggest', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suggestions: ['创建角色', '写一段对话', '添加场景'],
      }),
    })
  })
}

// Project data is real and test-owned; only agent endpoints are mocked.
async function navigateToProject(page: Page, project: OwnedProject) {
  await page.goto(`/project/${project.id}`)
  await page.waitForURL(`/project/${project.id}`)
  await expect(getChatInput(page)).toBeVisible()
}

test.describe('AI Chat (Mocked)', () => {
  test.skip(!ENABLE_MOCKED_CHAT_E2E, MOCKED_CHAT_OPT_IN_MESSAGE)

  test.beforeEach(async ({ page, ownedProject }) => {
    await mockAgentEndpoints(page)
    await navigateToProject(page, ownedProject)
  })
  test.afterEach(async ({ page }) => releaseStreams(page))

  test('user can send message to AI', async ({ page }) => {
    // Find the chat input textarea
    const input = getChatInput(page)
    await expect(input).toBeVisible()

    // Type a message
    await input.fill('帮我创建一个角色')
    await expect(input).toHaveValue('帮我创建一个角色')

    // Click send button
    await getSendButton(page).click()

    // Verify user message appears in the chat
    await expect(page.locator('text=帮我创建一个角色')).toBeVisible({ timeout: 5000 })
  })

  test('completed mocked SSE response renders correctly', async ({ page }) => {
    const input = getChatInput(page)
    await expect(input).toBeVisible()

    // Send a message that will trigger a response
    await input.fill('写一段简短的描述')
    await getSendButton(page).click()

    await expect(page.locator('.markdown-content').last()).toContainText('这是一个示例AI响应', { timeout: 5000 })
  })

  test('user can cancel streaming response', async ({ page }) => {
    const input = getChatInput(page)
    await expect(input).toBeVisible()

    await page.evaluate(() => {
      const encoder = new TextEncoder()
      let controller: ReadableStreamDefaultController<Uint8Array> | null = null
      const realFetch = window.fetch.bind(window)
      const state = {
        opened: () => controller !== null,
        push: (event: string, data: unknown) => controller?.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
        ),
        close: () => { controller?.close(); controller = null },
      }
      ;(window as unknown as { __cancelStream: typeof state }).__cancelStream = state
      window.fetch = (requestInput: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof requestInput === 'string'
          ? requestInput
          : requestInput instanceof URL ? requestInput.href : requestInput.url
        if (!url.includes('/api/v1/agent/stream')) return realFetch(requestInput, init)
        const body = new ReadableStream<Uint8Array>({ start(streamController) { controller = streamController } })
        return Promise.resolve(new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream', 'X-Agent-Run-ID': 'cancel-run-1' },
        }))
      }
    })
    await page.route('**/api/v1/agent/stop', (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ stop_requested: true }),
    }))

    // Send a message that will trigger a response
    await input.fill('写一个很长的故事，包含很多细节')
    await getSendButton(page).click()
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __cancelStream?: { opened: () => boolean } }).__cancelStream?.opened() ?? false,
    )).toBe(true)
    await page.evaluate(() => {
      ;(window as unknown as { __cancelStream: { push: (event: string, data: unknown) => void } })
        .__cancelStream.push('session_started', { session_id: 'cancel-session-1' })
    })

    const cancelButton = page.getByTestId('stop-button')
    await expect(cancelButton).toBeVisible()
    await expect(cancelButton).toHaveAttribute('aria-disabled', 'false')
    const stopRequest = page.waitForRequest((request) =>
      request.url().includes('/api/v1/agent/stop') && request.method() === 'POST',
    )
    await cancelButton.click()
    const request = await stopRequest
    expect(request.postDataJSON()).toMatchObject({ agent_run_id: 'cancel-run-1' })
    await page.evaluate(() => {
      const stream = (window as unknown as { __cancelStream: {
        push: (event: string, data: unknown) => void
        close: () => void
      } }).__cancelStream
      stream.push('workflow_stopped', { reason: 'user_stopped' })
      stream.push('done', { assistant_message_id: 'cancelled-assistant', session_id: 'cancel-session-1' })
      stream.close()
    })
    await expect(cancelButton).not.toBeVisible()
    await expect(input).toBeEnabled()
  })

  test('new session clears chat history', async ({ page }) => {
    const input = getChatInput(page)
    await expect(input).toBeVisible()

    // Send a message
    await input.fill('这是一条测试消息')
    await getSendButton(page).click()

    // Wait for message to appear
    await expect(page.locator('text=这是一条测试消息')).toBeVisible({ timeout: 5000 })

    // Click new session button (+ icon)
    await page.locator('button[title="新对话"], button[title="New chat"]').first().click()

    // Verify chat is cleared
    await expect(page.locator('text=这是一条测试消息')).not.toBeVisible({ timeout: 5000 })
  })

  test('suggestions endpoint is mocked', async ({ page }) => {
    const input = getChatInput(page)
    await expect(input).toBeVisible()

    // Send initial message
    await input.fill('创建一个角色')
    await getSendButton(page).click()

    await expect(page.locator('.markdown-content').last()).toContainText('这是一个示例AI响应', { timeout: 5000 })

    // Wait for idle timeout for suggestions to appear (10 seconds in app + buffer)
    // This wait is intentional to test the suggestion feature - use clock mocking for reliability
    await page.clock.runFor(12000)

    // Check for suggestion buttons from mocked response
    const suggestions = page.locator('button:has-text("创建角色"), button:has-text("写一段对话"), button:has-text("添加场景")')

    await expect(suggestions.first()).toBeVisible()
  })
})

test.describe('AI Chat - Multiple Messages (Mocked)', () => {
  test.skip(!ENABLE_MOCKED_CHAT_E2E, MOCKED_CHAT_OPT_IN_MESSAGE)

  test.beforeEach(async ({ page, ownedProject }) => {
    await mockAgentEndpoints(page)
    await navigateToProject(page, ownedProject)
  })

  test('successive mocked exchanges retain earlier messages', async ({ page }) => {
    const input = getChatInput(page)
    await expect(input).toBeVisible()

    // The mock returns fixed text; this checks UI history, not model memory.
    await input.fill('我的主角叫张三')
    await getSendButton(page).click()

    await expect(page.getByTestId('message-list')).toContainText('我的主角叫张三', { timeout: 5000 })
    await expect(page.getByTestId('message-list')).toContainText('这是一个示例AI响应')
    await expect(page.getByTestId('send-button')).toBeVisible()

    // Send the next exchange after the first stream has finished.
    await input.fill('告诉我关于主角的信息')
    await getSendButton(page).click()

    await expect(page.getByTestId('message-list')).toContainText('告诉我关于主角的信息', { timeout: 5000 })
    await expect(page.getByTestId('message-list')).toContainText('主角张三', { timeout: 5000 })
    await expect(page.getByTestId('message-list')).toContainText('我的主角叫张三')
    await expect(page.getByTestId('message-list')).toContainText('这是一个示例AI响应')
  })
})

test.describe('AI Chat - Streaming Controls (Mocked)', () => {
  test.skip(!ENABLE_MOCKED_CHAT_E2E, MOCKED_CHAT_OPT_IN_MESSAGE)

  test.beforeEach(async ({ page, ownedProject }) => {
    await mockAgentEndpoints(page)
    await navigateToProject(page, ownedProject)
  })
  test.afterEach(async ({ page }) => releaseStreams(page))

  test('chat input stays editable while send changes to streaming controls', async ({ page }) => {
    const input = getChatInput(page)
    await expect(input).toBeVisible()

    // Send a message
    await input.fill('写一段描述')
    await getSendButton(page).click()

    await expect(input).toBeEnabled()
    await expect(page.getByTestId('send-button')).toHaveCount(0)
    await expect(page.getByTestId('stop-button')).toBeVisible()
    releaseStreams(page)
    await expect(input).toBeEnabled()
    await expect(page.getByTestId('message-list')).toContainText('这是一个示例AI响应', { timeout: 5000 })
  })
})
