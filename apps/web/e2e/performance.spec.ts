import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { TEST_USERS } from './config'
import { login } from './helpers/common'
import {
  cleanupOwnedProject,
  createOwnedProject,
  type OwnedProject,
} from './fixtures/project-test-data'

const ENABLE_PERFORMANCE_E2E = process.env.E2E_ENABLE_PERFORMANCE_E2E === 'true'
const PERFORMANCE_OPT_IN_MESSAGE =
  'Performance E2E tests are opt-in. Set E2E_ENABLE_PERFORMANCE_E2E=true to run.'

/**
 * Performance E2E Tests
 *
 * These tests verify speed targets and performance characteristics:
 * - Initial page load time
 * - File tree rendering with large datasets
 * - Editor handling of large documents
 * - Chat scroll performance with many messages
 * - Time to interactive metrics
 *
 * Note: Tests may fail initially if performance isn't optimized.
 * Document current performance and track improvements over time.
 */

// Performance thresholds (configurable)
const PERFORMANCE_THRESHOLDS = {
  INITIAL_PAGE_LOAD_MS: 3000, // 3 seconds
  TIME_TO_INTERACTIVE_MS: 5000, // 5 seconds
  LARGE_FILE_TREE_RENDER_MS: 2000, // 2 seconds for 100+ items
  EDITOR_LARGE_DOC_RESPONSE_MS: 500, // 500ms for 10k+ words
  CHAT_SCROLL_FPS: 30, // Minimum FPS for smooth scrolling
}

// Test credentials
const TEST_EMAIL = TEST_USERS.standard.email
const TEST_PASSWORD = TEST_USERS.standard.password
let performanceProject: OwnedProject | undefined

async function loginAndOpenDashboard(page: Page) {
  // The login page intentionally redirects existing users to their most
  // recent project. Using the API-backed helper avoids racing that async
  // redirect when a performance case specifically needs the dashboard.
  await login(page)
  await page.goto('/dashboard')
  await expect(page.getByTestId('dashboard-inspiration-input')).toBeVisible({ timeout: 15000 })
}

async function createPerformanceProject(page: Page, testInfo: TestInfo, name: string) {
  performanceProject = await createOwnedProject(page, testInfo, `E2E-perf-${name}`)
  return performanceProject.id
}

async function openPerformanceProject(page: Page, testInfo: TestInfo, name: string) {
  const projectId = await createPerformanceProject(page, testInfo, name)
  await page.goto(`/project/${projectId}`)
  await page.waitForURL(/\/project\//, { timeout: 15000 })
}

test.afterEach(async ({ page }) => {
  await cleanupOwnedProject(page, performanceProject)
  performanceProject = undefined
})

async function mockAgentStream(page: Page) {
  let responseNumber = 0
  await page.route('**/api/v1/agent/stream', async route => {
    responseNumber += 1
    const stream = [
      'event: content_start',
      'data: {}',
      '',
      'event: content',
      `data: ${JSON.stringify({ text: `性能测试模拟回复 ${responseNumber}` })}`,
      '',
      'event: content_end',
      'data: {}',
      '',
      'event: done',
      `data: ${JSON.stringify({ assistant_message_id: `performance-assistant-${responseNumber}`, session_id: 'performance-session' })}`,
      '',
    ].join('\n')
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: stream,
    })
  })
}

/**
 * Helper to generate large content for performance testing
 */
function generateLargeContent(wordCount: number): string {
  const words = [
    '故事', '主角', '冒险', '魔法', '世界', '旅程', '勇气', '友情',
    '挑战', '成长', '命运', '选择', '希望', '光明', '黑暗', '力量',
  ]
  const content: string[] = []
  for (let i = 0; i < wordCount; i++) {
    content.push(words[i % words.length])
  }
  return content.join(' ')
}

test.describe('Performance', () => {
  test.skip(!ENABLE_PERFORMANCE_E2E, PERFORMANCE_OPT_IN_MESSAGE)
  test('initial page load < 3 seconds', async ({ page }) => {
    const startTime = Date.now()
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    const loadTime = Date.now() - startTime

    console.log(`Initial page load time: ${loadTime}ms`)

    expect(loadTime).toBeLessThan(PERFORMANCE_THRESHOLDS.INITIAL_PAGE_LOAD_MS)
  })

  test('login page renders quickly', async ({ page }) => {
    await page.addInitScript(() => {
      const markVisible = () => {
        const input = document.querySelector<HTMLInputElement>('#identifier')
        if (!input || input.getClientRects().length === 0) return false
        const style = getComputedStyle(input)
        if (style.visibility === 'hidden' || style.display === 'none') return false
        ;(window as Window & { __loginInputVisibleAt?: number }).__loginInputVisibleAt = performance.now()
        return true
      }
      document.addEventListener('DOMContentLoaded', () => {
        if (markVisible()) return
        const observer = new MutationObserver(() => {
          requestAnimationFrame(() => {
            if (markVisible()) observer.disconnect()
          })
        })
        observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true })
      }, { once: true })
    })
    await page.goto('/login')
    await page.waitForSelector('#identifier')
    const renderTime = await page.evaluate(() =>
      (window as Window & { __loginInputVisibleAt?: number }).__loginInputVisibleAt ?? performance.now())

    console.log(`Login page render time: ${renderTime}ms`)

    // Login page should render faster than 2 seconds
    expect(renderTime).toBeLessThan(2000)
  })

  test('dashboard renders after login < 3 seconds', async ({ page }) => {
    // Login first
    await page.goto('/login')
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()

    const startTime = Date.now()
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 })
    await page.waitForLoadState('networkidle')
    const loadTime = Date.now() - startTime

    console.log(`Dashboard render time after login: ${loadTime}ms`)

    expect(loadTime).toBeLessThan(PERFORMANCE_THRESHOLDS.INITIAL_PAGE_LOAD_MS)
  })

  test('file tree remains responsive as items are added', async ({ page }, testInfo) => {
    await loginAndOpenDashboard(page)
    await openPerformanceProject(page, testInfo, 'tree')

    // Wait for file tree to load initially
    await page.waitForSelector('.overflow-auto', { timeout: 5000 })

    // Expand the outline folder
    const outlineFolder = page.locator('text=大纲').first()
    await outlineFolder.click()
    await expect(page.locator('.overflow-auto >> text=大纲')).toBeVisible()

    // Create multiple files to test rendering performance
    // Note: In a real test, you would create 100+ files via API for accuracy
    const fileCount = 10 // Reduced for E2E test performance
    const filePrefix = `性能文件-${Date.now()}-`
    const startTime = Date.now()

    for (let i = 0; i < fileCount; i++) {
      await outlineFolder.hover()
      const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first()
      await addButton.click({ force: true })

      const fileInput = page.locator('input[placeholder*="大纲"]')
      const title = `${filePrefix}${i}`
      const responsePromise = page.waitForResponse(response =>
        response.url().includes('/api/v1/projects/')
          && response.url().endsWith('/files')
          && response.request().method() === 'POST')
      await fileInput.fill(title)
      await fileInput.press('Enter')
      expect((await responsePromise).ok()).toBeTruthy()
      await expect(page.getByTestId('file-tree-node').filter({ hasText: title })).toBeVisible()
    }

    const renderTime = Date.now() - startTime
    console.log(`Time to create and render ${fileCount} files: ${renderTime}ms`)

    // Measure scroll performance in the file tree
    const scrollContainer = page.locator('.overflow-auto').first()
    const scrollStartTime = Date.now()

    // Scroll through the file tree
    for (let i = 0; i < 5; i++) {
      await scrollContainer.evaluate((el, scrollAmount) => {
        el.scrollTop += scrollAmount
      }, 200)
      // Wait for scroll frame (~60fps) - use requestAnimationFrame for proper frame timing
      await scrollContainer.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)))
    }

    const scrollTime = Date.now() - scrollStartTime
    console.log(`Scroll performance for ${fileCount} items: ${scrollTime}ms`)

    // Verify all files are visible
    await expect(page.getByTestId('file-tree-node').filter({ hasText: `${filePrefix}0` })).toBeVisible()
    await expect(page.getByTestId('file-tree-node').filter({ hasText: `${filePrefix}${fileCount - 1}` })).toBeVisible()

    // Each create-and-render cycle should remain below the large-tree render
    // budget. Measuring the average avoids pretending that ten API-backed
    // creates can complete inside one tenth of a single render budget.
    expect(renderTime / fileCount).toBeLessThan(PERFORMANCE_THRESHOLDS.LARGE_FILE_TREE_RENDER_MS)
  })

  test('editor handles 1k word documents', async ({ page }, testInfo) => {
    await loginAndOpenDashboard(page)
    await openPerformanceProject(page, testInfo, 'editor')

    // Create and select a file
    await page.waitForSelector('.overflow-auto', { timeout: 5000 })
    const outlineFolder = page.locator('text=大纲').first()
    await outlineFolder.click()
    await expect(page.locator('.overflow-auto >> text=大纲')).toBeVisible()
    await outlineFolder.hover()
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first()
    await addButton.click({ force: true })

    const fileTitle = `大文档测试 ${Date.now()}`
    const fileInput = page.locator('input[placeholder*="大纲"]')
    await fileInput.fill(fileTitle)
    await fileInput.press('Enter')
    // Wait for file to be created
    const testFile = page.getByTestId('file-tree-node').filter({ hasText: fileTitle }).first()
    await expect(testFile).toBeVisible()

    // Select the file
    await testFile.click()
    // Wait for editor to load
    const editor = page.getByPlaceholder(/开始你的创作|Start your story/i)
    await expect(editor).toBeVisible({ timeout: 5000 })

    // Generate large content (10000 words for full test, 1000 for E2E speed)
    const wordCount = 1000 // Reduced for E2E test performance
    const largeContent = generateLargeContent(wordCount)

    // Measure editor fill performance
    await expect(editor).toBeVisible()

    const fillStartTime = Date.now()
    await editor.fill(largeContent)
    const fillTime = Date.now() - fillStartTime

    console.log(`Time to fill editor with ${wordCount} words: ${fillTime}ms`)

    // Measure typing responsiveness (simulate typing at the end)
    const typeStartTime = Date.now()
    await editor.press('End')
    await editor.press('Enter')
    await editor.type('新段落测试', { delay: 10 })
    const typeTime = Date.now() - typeStartTime

    console.log(`Typing responsiveness with ${wordCount} words: ${typeTime}ms`)

    // Measure selection performance
    const selectTime = await editor.evaluate(async (element: HTMLTextAreaElement) => {
      element.focus()
      const start = performance.now()
      const selectionStart = Math.max(0, element.value.length - 20)
      element.setSelectionRange(selectionStart, element.value.length, 'forward')
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      return performance.now() - start
    })

    console.log(`Selection time with ${wordCount} words: ${selectTime}ms`)

    // Verify content
    const editorValue = await editor.inputValue()
    expect(editorValue.length).toBeGreaterThan(wordCount * 0.9) // Allow some margin

    // Performance assertions (scaled for word count)
    // For 10000 words, we'd expect < EDITOR_LARGE_DOC_RESPONSE_MS
    expect(fillTime).toBeLessThan(PERFORMANCE_THRESHOLDS.EDITOR_LARGE_DOC_RESPONSE_MS * (wordCount / 1000) * 5)
    expect(typeTime).toBeLessThan(PERFORMANCE_THRESHOLDS.EDITOR_LARGE_DOC_RESPONSE_MS * 3)
    expect(selectTime).toBeLessThan(200)
  })

  test('chat scroll remains smooth across 10 message rounds', async ({ page }, testInfo) => {
    await mockAgentStream(page)
    await loginAndOpenDashboard(page)

    await openPerformanceProject(page, testInfo, 'chat')

    const input = page.getByTestId('chat-input')
    await expect(input).toBeVisible()

    // Send multiple messages to test scroll performance
    // Note: Reduced to 10 messages for E2E test performance
    // In production, you would create 100+ messages via API
    const messageCount = 10

    for (let i = 0; i < messageCount; i++) {
      await input.fill(`测试消息 ${i}：${generateLargeContent(50)}`)
      await page.getByTestId('send-button').click()

      await expect(page.locator('.markdown-content').last()).toContainText(`性能测试模拟回复 ${i + 1}`)
      // Prose can render before done removes the activity row and swaps the
      // stop control back to send. Measure only completed exchanges.
      await expect(page.getByTestId('send-button')).toBeVisible()
      await expect(page.getByTestId('stop-button')).toHaveCount(0)
      await expect(page.getByTestId('stream-activity-line')).toHaveCount(0)
    }

    // ChatPanel's message-list is the actual scrolling surface; a missing or
    // non-scrollable container must fail rather than skip the measurement.
    const chatContainer = page.getByTestId('message-list')
    await expect(chatContainer).toBeVisible()
    const metrics = await chatContainer.evaluate(async (el) => {
      // Font loading is setup, not scroll rendering time.
      await document.fonts.ready
      const nextFrame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      const maxTop = el.scrollHeight - el.clientHeight
      if (maxTop <= 0) throw new Error('Chat messages must overflow before measuring scroll performance')
      const measureEdge = async (edge: 'top' | 'bottom') => {
        const before = el.scrollTop
        const beforeMaxTop = el.scrollHeight - el.clientHeight
        const target = edge === 'bottom' ? beforeMaxTop : 0
        const start = performance.now()
        el.scrollTop = target
        await nextFrame()
        // Reaching the bottom hides Jump to latest. Its removal can shrink
        // the range and correctly clamp scrollTop within this same frame.
        const after = el.scrollTop
        const afterMaxTop = el.scrollHeight - el.clientHeight
        return { edge, elapsed: performance.now() - start, before, after, target, beforeMaxTop, afterMaxTop }
      }
      el.scrollTop = 0
      await nextFrame()
      const frames = []
      for (let i = 0; i < 10; i++) {
        const currentMaxTop = el.scrollHeight - el.clientHeight
        frames.push(await measureEdge(el.scrollTop > currentMaxTop / 2 ? 'top' : 'bottom'))
      }

      el.scrollTop = el.scrollHeight
      await nextFrame()
      const top = await measureEdge('top')
      const bottom = await measureEdge('bottom')
      return { maxTop, frames, top, bottom }
    })
    console.log(`Chat scroll geometry: ${JSON.stringify(metrics)}`)
    await testInfo.attach('chat-scroll-geometry', { body: JSON.stringify(metrics), contentType: 'application/json' })
    expect(metrics.maxTop).toBeGreaterThan(0)
    for (const frame of [...metrics.frames, metrics.top, metrics.bottom]) {
      expect(frame.beforeMaxTop).toBeGreaterThan(0)
      expect(frame.afterMaxTop).toBeGreaterThan(0)
      expect(Math.abs(frame.after - frame.before), 'every timed frame must perform a real scroll').toBeGreaterThan(0)
      expect(frame.after, `${frame.edge} edge; range ${frame.beforeMaxTop} → ${frame.afterMaxTop}`).toBeCloseTo(
        frame.edge === 'bottom' ? frame.afterMaxTop : 0, 0,
      )
    }
    const avgScrollTime = metrics.frames.reduce((sum, frame) => sum + frame.elapsed, 0) / metrics.frames.length
    console.log(`Average scroll time per frame: ${avgScrollTime}ms`)
    expect(avgScrollTime).toBeLessThan(1000 / PERFORMANCE_THRESHOLDS.CHAT_SCROLL_FPS)
    expect(metrics.top.elapsed).toBeLessThan(100)
    expect(metrics.bottom.elapsed).toBeLessThan(100)

    // Verify messages are visible
    await expect(page.locator('text=测试消息 0')).toBeVisible()
    await expect(page.locator(`text=测试消息 ${messageCount - 1}`)).toBeVisible()
  })

  test('time to interactive < 5 seconds', async ({ page }) => {
    const startTime = Date.now()

    // Navigate to login
    await page.goto('/login')

    // Wait for form to be interactive (inputs are focusable)
    await page.waitForSelector('#identifier', { state: 'visible' })
    await page.waitForSelector('#password', { state: 'visible' })
    await page.waitForSelector('button[type="submit"]', { state: 'visible' })

    // Verify form is actually interactive by typing
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)

    const interactiveTime = Date.now() - startTime
    console.log(`Login page time to interactive: ${interactiveTime}ms`)

    expect(interactiveTime).toBeLessThan(PERFORMANCE_THRESHOLDS.TIME_TO_INTERACTIVE_MS)

    // Login and measure dashboard time to interactive
    await page.locator('button[type="submit"]').click()
    const dashboardStart = Date.now()

    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 })
    await page.waitForLoadState('networkidle')
    await page.goto('/dashboard')

    // Wait for interactive elements on dashboard
    await page.waitForSelector('[data-testid="project-card"], [data-testid="create-project-button"]', {
      state: 'visible',
      timeout: 10000,
    })

    // Verify dashboard is interactive
    const createButton = page.getByTestId('create-project-button')
    await expect(createButton).toBeEnabled()

    const dashboardInteractiveTime = Date.now() - dashboardStart
    console.log(`Dashboard time to interactive: ${dashboardInteractiveTime}ms`)

    expect(dashboardInteractiveTime).toBeLessThan(PERFORMANCE_THRESHOLDS.TIME_TO_INTERACTIVE_MS)
  })

  test('project page time to interactive', async ({ page }, testInfo) => {
    await loginAndOpenDashboard(page)

    const projectId = await createPerformanceProject(page, testInfo, 'tti')
    const projectStart = Date.now()
    await page.goto(`/project/${projectId}`)
    await page.waitForURL(/\/project\//, { timeout: 10000 })

    // Wait for key interactive elements
    await page.waitForSelector('.overflow-auto', { timeout: 5000 }) // File tree
    await page.waitForSelector('textarea', { timeout: 5000 }) // Editor or chat

    // Verify page is interactive
    const chatInput = page.getByTestId('chat-input')
    await expect(chatInput).toBeVisible()
    await expect(chatInput).toBeEnabled()

    const projectInteractiveTime = Date.now() - projectStart
    console.log(`Project page time to interactive: ${projectInteractiveTime}ms`)

    expect(projectInteractiveTime).toBeLessThan(PERFORMANCE_THRESHOLDS.TIME_TO_INTERACTIVE_MS)
  })

  test('memory usage remains stable during extended use', async ({ page }, testInfo) => {
    await loginAndOpenDashboard(page)
    const projectId = await createPerformanceProject(page, testInfo, 'memory')

    // Get initial memory metrics
    const initialMetrics = await page.evaluate(() => {
      if ('memory' in performance) {
        const memory = (performance as { memory: { usedJSHeapSize: number } }).memory
        return {
          usedJSHeapSize: memory.usedJSHeapSize,
        }
      }
      return null
    })

    if (initialMetrics) {
      console.log(`Initial memory usage: ${(initialMetrics.usedJSHeapSize / 1024 / 1024).toFixed(2)} MB`)

      // Perform multiple dashboard/project navigation cycles without
      // exhausting the regular user's project quota.
      for (let i = 0; i < 5; i++) {
        await page.goto(`/project/${projectId}`)
        await page.waitForURL(/\/project\//, { timeout: 15000 })

        // Interact with the page
        await page.waitForSelector('.overflow-auto', { timeout: 5000 })

        // Navigate back to dashboard
        await page.goto('/dashboard')
        await expect(page.getByTestId('dashboard-inspiration-input')).toBeVisible({ timeout: 15000 })
      }

      // Get final memory metrics
      const finalMetrics = await page.evaluate(() => {
        if ('memory' in performance) {
          const memory = (performance as { memory: { usedJSHeapSize: number } }).memory
          return {
            usedJSHeapSize: memory.usedJSHeapSize,
          }
        }
        return null
      })

      if (finalMetrics) {
        console.log(`Final memory usage: ${(finalMetrics.usedJSHeapSize / 1024 / 1024).toFixed(2)} MB`)

        const memoryIncrease = finalMetrics.usedJSHeapSize - initialMetrics.usedJSHeapSize
        console.log(`Memory increase: ${(memoryIncrease / 1024 / 1024).toFixed(2)} MB`)

        // Memory should not increase by more than 100MB during normal use
        expect(memoryIncrease).toBeLessThan(100 * 1024 * 1024)
      }
    } else {
      console.log('Memory metrics not available in this browser')
      test.skip()
    }
  })

  test('network request count is reasonable', async ({ page }) => {
    // Track network requests
    const requests: string[] = []
    page.on('request', (request) => {
      requests.push(request.url())
    })

    // Navigate to login and perform actions
    await page.goto('/login')
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 })
    await page.waitForLoadState('networkidle')

    // Count requests by type
    const apiRequests = requests.filter((r) => r.includes('/api/'))
    const staticRequests = requests.filter((r) => r.includes('/assets/') || r.endsWith('.js') || r.endsWith('.css'))

    console.log(`Total requests: ${requests.length}`)
    console.log(`API requests: ${apiRequests.length}`)
    console.log(`Static asset requests: ${staticRequests.length}`)

    // Initial load should not make excessive API calls
    expect(apiRequests.length).toBeLessThan(20)

    // Static assets should be cached (reasonable number)
    expect(staticRequests.length).toBeLessThan(50)
  })
})

test.describe('Performance - Stress Tests', () => {
  test.skip(!ENABLE_PERFORMANCE_E2E, PERFORMANCE_OPT_IN_MESSAGE)
  // These tests push the limits and may fail on slower systems

  test.skip('file tree handles 500+ items', async ({ page }) => {
    // This test requires API setup to create 500+ files
    // Skipping by default - enable manually for stress testing

    // Login
    await page.goto('/login')
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 })

    // Note: In production, create files via API before this test
    console.log('Stress test skipped - requires 500+ files to be created via API')
  })

  test.skip('editor handles 50k+ word documents', async ({ page }) => {
    // This test requires significant time - skipping by default

    // Login and setup
    await page.goto('/login')
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 })

    console.log('Stress test skipped - 50k word document test disabled by default')
  })

  test.skip('chat handles 500+ messages', async ({ page }) => {
    // This test requires significant time - skipping by default

    // Login and setup
    await page.goto('/login')
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 })

    console.log('Stress test skipped - 500 message test disabled by default')
  })
})
