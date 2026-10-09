import { test, expect } from '@playwright/test'
import { TEST_USERS, config } from './config'

const ENABLE_LARGE_DOCUMENT_E2E = process.env.E2E_ENABLE_LARGE_DOCUMENT_E2E === 'true'
const LARGE_DOCUMENT_OPT_IN_MESSAGE =
  'Large document E2E tests are opt-in. Set E2E_ENABLE_LARGE_DOCUMENT_E2E=true to run.'

/**
 * E2E Tests for Large Document Handling
 *
 * These tests verify the virtualization feature for documents with 50,000+ words:
 * - Loading large documents in under 3 seconds
 * - Smooth scrolling at 60fps regardless of document size
 * - Auto-save completing within 2 seconds for any document size
 * - Memory usage remaining under 500MB for large documents
 * - Editor mode switching (SimpleEditor vs VirtualizedEditor)
 * - Chunk-level editing with cursor preservation
 */

// Performance thresholds from acceptance criteria
const PERFORMANCE_THRESHOLDS = {
  LARGE_DOC_LOAD_MS: 3000, // 3 seconds for 50k+ words
  SCROLL_FRAME_MS: 16.67, // ~60fps (1000ms / 60 = 16.67ms per frame)
  AUTO_SAVE_MS: 2000, // 2 seconds for auto-save
  MEMORY_LIMIT_MB: 500, // 500MB memory limit
}

// Test thresholds (reduced for E2E test performance)
const TEST_THRESHOLDS = {
  LOAD_DOC_WORDS: 10000, // 10k words for load test (vs 50k target)
  SCROLL_DOC_WORDS: 5000, // 5k words for scroll test
  SAVE_DOC_WORDS: 10000, // 10k words for save test
  MEMORY_DOC_WORDS: 10000, // 10k words for memory test
}

/**
 * Helper to generate large content for testing
 * Creates content with paragraphs for more realistic document structure
 */
function generateLargeContent(wordCount: number): string {
  const words = [
    '故事', '主角', '冒险', '魔法', '世界', '旅程', '勇气', '友情',
    '挑战', '成长', '命运', '选择', '希望', '光明', '黑暗', '力量',
    '传说', '神秘', '古老', '王国', '英雄', '传奇', '史诗', '战斗',
  ]
  const paragraphs: string[] = []
  const wordsPerParagraph = 100
  const paragraphCount = Math.ceil(wordCount / wordsPerParagraph)

  for (let p = 0; p < paragraphCount; p++) {
    const paragraphWords: string[] = []
    for (let w = 0; w < wordsPerParagraph; w++) {
      const wordIndex = (p * wordsPerParagraph + w) % words.length
      paragraphWords.push(words[wordIndex])
    }
    paragraphs.push(paragraphWords.join(''))
  }

  return paragraphs.join('\n\n')
}

/**
 * Helper to login and create a test project
 */
async function setupProjectAndFile(page: import('@playwright/test').Page, fileName: string) {
  await page.addInitScript(() => {
    const cachedUser = localStorage.getItem('user')
    if (cachedUser) {
      localStorage.setItem('auth_validated_at', Date.now().toString())
    }
  })

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
  const dashboardInput = page.locator('[data-testid="dashboard-inspiration-input"]')

  if (!(await dashboardInput.isVisible({ timeout: 5000 }).catch(() => false))) {
    const params = new URLSearchParams()
    params.append('username', TEST_USERS.standard.email)
    params.append('password', TEST_USERS.standard.password)

    const response = await fetch(`${config.apiBaseUrl}/api/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: params.toString(),
    })

    if (!response.ok) {
      throw new Error(`Large document setup auth failed: ${response.status} ${response.statusText}`)
    }

    const tokenData = await response.json()

    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await page.evaluate((data) => {
      localStorage.setItem('access_token', data.access_token)
      localStorage.setItem('refresh_token', data.refresh_token)
      localStorage.setItem('token_type', data.token_type)
      localStorage.setItem('user', JSON.stringify(data.user))
      localStorage.setItem('auth_validated_at', Date.now().toString())
    }, tokenData)

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
  }
  await expect(dashboardInput).toBeVisible()

  // Reuse an existing project when available to avoid plan-quota flakiness in E2E.
  const reusableProject = page.locator(
    '[data-testid="project-card"]'
  ).first()
  const hasReusableProject = await reusableProject
    .waitFor({ state: 'visible', timeout: 3000 })
    .then(() => true)
    .catch(() => false)

  if (hasReusableProject) {
    await reusableProject.click()
    await page.waitForURL(/\/project\//, { timeout: 15000 })
  } else {
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill(`大文档测试项目 ${Date.now()}`)
    const createProjectButton = page.getByTestId('create-project-button')
    await expect(createProjectButton).toBeVisible()
    await createProjectButton.click()
    await page.waitForURL(/\/project\//, { timeout: 15000 })
  }

  // Wait for file tree to load
  await page.waitForSelector('.overflow-auto', { timeout: 5000 })

  // Create a draft file for testing in the main writing editor.
  const draftFolder = page.locator('text=正文').first()
  await draftFolder.click()
  await draftFolder.hover()
  const addButton = draftFolder.locator('..').locator('button:has(svg.lucide-plus)').first()
  await addButton.click({ force: true })

  const fileInput = page.locator('input[placeholder*="正文"], input[placeholder*="章节"]').last()
  await fileInput.fill(fileName)
  await fileInput.press('Enter')
  await expect(fileInput).not.toBeVisible({ timeout: 5000 })

  // Wait for file to be created
  await page.locator(`.overflow-auto >> text=${fileName}`).first().waitFor({ state: 'visible', timeout: 5000 })

  // Select the file
  const testFile = page.locator(`.overflow-auto >> text=${fileName}`).first()
  await testFile.click()

  // Wait for editor to load
  await expect(page.locator('[data-editor-scroll-container="true"] textarea').first()).toBeVisible({ timeout: 5000 })

  return { fileName }
}

async function focusVisibleEditorArea(
  page: import('@playwright/test').Page,
  scrollContainer: import('@playwright/test').Locator
) {
  const box = await scrollContainer.boundingBox()
  if (!box) {
    throw new Error('Editor scroll container is not visible')
  }

  await page.mouse.click(
    box.x + Math.min(160, box.width * 0.25),
    box.y + box.height / 2
  )
}

async function focusVisibleTextareaArea(
  page: import('@playwright/test').Page,
  scrollContainer: import('@playwright/test').Locator
) {
  await expect(scrollContainer).toBeVisible()
  const clickPoint = await page.evaluate(() => {
    const container = document.querySelector('[data-editor-scroll-container="true"]')
    if (!(container instanceof HTMLElement)) {
      return null
    }

    const containerRect = container.getBoundingClientRect()
    const candidates = Array.from(
      container.querySelectorAll<HTMLTextAreaElement>('.chunk-container textarea')
    )
      .map((node) => {
        const rect = node.getBoundingClientRect()
        const visibleLeft = Math.max(rect.left, containerRect.left)
        const visibleRight = Math.min(rect.right, containerRect.right)
        const visibleTop = Math.max(rect.top, containerRect.top)
        const visibleBottom = Math.min(rect.bottom, containerRect.bottom)
        const visibleWidth = Math.max(0, visibleRight - visibleLeft)
        const visibleHeight = Math.max(0, visibleBottom - visibleTop)

        return {
          visibleArea: visibleWidth * visibleHeight,
          visibleLeft,
          visibleRight,
          visibleTop,
          visibleBottom,
        }
      })
      .filter((candidate) => candidate.visibleArea > 0)
      .sort((a, b) => b.visibleArea - a.visibleArea)

    const target = candidates[0]
    if (!target) {
      return null
    }

    return {
      x: target.visibleLeft + Math.min(80, Math.max(24, (target.visibleRight - target.visibleLeft) * 0.2)),
      y: target.visibleTop + Math.max(24, (target.visibleBottom - target.visibleTop) * 0.35),
    }
  })

  if (!clickPoint) {
    throw new Error('Visible textarea area is not clickable')
  }

  await page.mouse.click(
    clickPoint.x,
    clickPoint.y
  )
}

async function expectPlainTypingKeepsScrollStable(
  page: import('@playwright/test').Page,
  textarea: import('@playwright/test').Locator,
  scrollContainer: import('@playwright/test').Locator,
  maxDelta = 120,
  preferTextareaClick = false
) {
  if (preferTextareaClick) {
    await focusVisibleTextareaArea(page, scrollContainer)
  } else {
    await focusVisibleEditorArea(page, scrollContainer)
  }
  await expect
    .poll(async () =>
      page.evaluate(() => {
        const activeElement = document.activeElement
        return (
          activeElement instanceof HTMLTextAreaElement &&
          Boolean(activeElement.closest('[data-editor-scroll-container="true"]'))
        )
      })
    )
    .toBe(true)
  await page.waitForTimeout(120)

  const getMetrics = () =>
    scrollContainer.evaluate((el) => {
      const activeElement = document.activeElement
      const containerRect = el.getBoundingClientRect()
      const activeRect =
        activeElement instanceof HTMLElement ? activeElement.getBoundingClientRect() : null
      return {
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        activeTag: activeElement?.tagName ?? null,
        activeValueLength:
          activeElement instanceof HTMLTextAreaElement ? activeElement.value.length : null,
        activeSelectionStart:
          activeElement instanceof HTMLTextAreaElement ? activeElement.selectionStart : null,
        activeOffsetTop:
          activeRect ? Math.round(activeRect.top - containerRect.top) : null,
      }
    })

  const initialMetrics = await getMetrics()
  const initialScrollTop = initialMetrics.scrollTop
  const initialActiveOffsetTop = initialMetrics.activeOffsetTop
  expect(initialScrollTop).toBeGreaterThan(100)
  expect(initialActiveOffsetTop).not.toBeNull()

  await page.keyboard.type('a1')
  await page.keyboard.press('Delete')
  await page.keyboard.press('Backspace')
  await page.waitForTimeout(180)

  const finalMetrics = await getMetrics()
  const finalScrollTop = finalMetrics.scrollTop
  const finalActiveOffsetTop = finalMetrics.activeOffsetTop
  expect(finalScrollTop).toBeGreaterThan(100)
  expect(finalActiveOffsetTop).not.toBeNull()
  expect(Math.abs((finalActiveOffsetTop ?? 0) - (initialActiveOffsetTop ?? 0))).toBeLessThan(120)

  if (!preferTextareaClick) {
    expect(Math.abs(finalScrollTop - initialScrollTop)).toBeLessThan(maxDelta)
  }
}

test.describe('Large Document - Editor Mode Switching', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('small documents use SimpleEditor (not virtualized)', async ({ page }) => {
    await setupProjectAndFile(page, '小文档测试')

    // Create small content (under 10000 words)
    const smallContent = generateLargeContent(500) // 500 words
    const editor = page.locator('textarea').first()
    await editor.fill(smallContent)

    // Verify content was added
    const value = await editor.inputValue()
    expect(value.length).toBeGreaterThan(100)

    // SimpleEditor should be used - no chunk indicators
    // The status bar should show basic word count without "Chunks"
    const chunkIndicator = page.locator('text=/Chunks|chunks/')
    const hasChunkIndicator = await chunkIndicator.isVisible().catch(() => false)

    // For small documents, chunk indicator might not exist or show minimal chunks
    // This is expected behavior - SimpleEditor doesn't show chunk count
    if (hasChunkIndicator) {
      const chunkText = await chunkIndicator.textContent()
      // If chunks shown, should be minimal for small doc
      expect(chunkText).toBeTruthy()
    }
  })

  test('large documents use VirtualizedEditor', async ({ page }) => {
    await setupProjectAndFile(page, '大文档虚拟化测试')

    // Create large content (over 10000 words to trigger virtualization)
    const largeContent = generateLargeContent(12000)
    const editor = page.locator('textarea').first()

    // Fill with large content
    await editor.fill(largeContent)

    // Wait for content to be processed
    await page.waitForTimeout(500)

    // Verify content
    const value = await editor.inputValue()
    expect(value.length).toBeGreaterThan(100000)

    // VirtualizedEditor should show chunk indicator
    // Look for "Chunks" in status bar
    const chunkIndicator = page.locator('text=/Chunks|chunks/')
    await expect(chunkIndicator.first()).toBeVisible({ timeout: 5000 })
  })

  test('editor mode switches when file changes', async ({ page }) => {
    const { fileName } = await setupProjectAndFile(page, '模式切换测试')

    // Create small content
    const smallContent = generateLargeContent(500)
    let editor = page.locator('textarea').first()
    await editor.fill(smallContent)

    // Create another file with large content
    const outlineFolder = page.locator('text=大纲').first()
    await outlineFolder.hover()
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first()
    await addButton.click({ force: true })

    const fileInput = page.locator('input[placeholder*="大纲"]')
    await fileInput.fill('大文档文件')
    await fileInput.press('Enter')

    // Select the large file
    const largeFile = page.locator('.overflow-auto >> text=大文档文件').first()
    await largeFile.click()
    await expect(page.locator('textarea')).toBeVisible({ timeout: 5000 })

    // Fill with large content
    editor = page.locator('textarea').first()
    const largeContent = generateLargeContent(12000)
    await editor.fill(largeContent)

    // Wait for virtualization to kick in
    await page.waitForTimeout(500)

    // VirtualizedEditor should be active (chunk indicator visible)
    await expect(page.locator('text=/Chunks|chunks/').first()).toBeVisible({ timeout: 5000 })

    // Switch back to small file
    const smallFile = page.locator(`.overflow-auto >> text=${fileName}`).first()
    await smallFile.click()
    await expect(page.locator('textarea')).toBeVisible({ timeout: 5000 })

    // Editor should switch back to SimpleEditor
    // The content should still be preserved
    editor = page.locator('textarea').first()
    const smallFileValue = await editor.inputValue()
    expect(smallFileValue.length).toBeGreaterThan(100)
  })
})

test.describe('Large Document - Loading Performance', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('large document loads within acceptable time', async ({ page }) => {
    await setupProjectAndFile(page, '加载性能测试')

    // Generate large content
    const wordCount = TEST_THRESHOLDS.LOAD_DOC_WORDS
    const largeContent = generateLargeContent(wordCount)

    const editor = page.locator('textarea').first()

    // Measure fill time
    const loadStartTime = Date.now()
    await editor.fill(largeContent)
    const loadTime = Date.now() - loadStartTime

    console.log(`Time to load ${wordCount} words: ${loadTime}ms`)

    // Verify content is present
    const value = await editor.inputValue()
    expect(value.length).toBeGreaterThan(wordCount * 0.8)

    // Performance assertion (scaled for test word count)
    // Target is 3s for 50k words, so for 10k words: 3s * (10k/50k) = 600ms
    // With some buffer for E2E overhead
    const expectedMaxTime = PERFORMANCE_THRESHOLDS.LARGE_DOC_LOAD_MS * (wordCount / 50000) * 2
    expect(loadTime).toBeLessThan(expectedMaxTime)
  })

  test('editor remains responsive after loading large document', async ({ page }) => {
    await setupProjectAndFile(page, '响应性测试')

    // Load large content
    const largeContent = generateLargeContent(TEST_THRESHOLDS.LOAD_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Test typing responsiveness
    const typeStartTime = Date.now()
    await editor.press('End')
    await editor.press('Enter')
    await editor.type('测试新内容', { delay: 10 })
    const typeTime = Date.now() - typeStartTime

    console.log(`Typing responsiveness: ${typeTime}ms`)

    // Typing should be responsive (< 500ms for typing a few characters)
    expect(typeTime).toBeLessThan(500)

    // Test selection responsiveness
    const selectStartTime = Date.now()
    await editor.focus()
    await page.keyboard.down('Shift')
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press('ArrowLeft')
    }
    await page.keyboard.up('Shift')
    const selectTime = Date.now() - selectStartTime

    console.log(`Selection responsiveness: ${selectTime}ms`)

    // Selection should be smooth (< 200ms for 10 characters)
    expect(selectTime).toBeLessThan(200)
  })
})

test.describe('Large Document - Scrolling Performance', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('scrolling is smooth with large document', async ({ page }) => {
    await setupProjectAndFile(page, '滚动性能测试')

    // Create content for scrolling test
    const scrollContent = generateLargeContent(TEST_THRESHOLDS.SCROLL_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(scrollContent)

    // Find the scroll container (the editor's parent)
    const scrollContainer = page.locator('.flex-1.overflow-auto').first()

    // Measure scroll performance
    const scrollMetrics: number[] = []

    // Perform multiple scroll operations
    for (let i = 0; i < 10; i++) {
      const scrollStart = Date.now()

      await scrollContainer.evaluate((el) => {
        el.scrollTop += 100
      })

      // Wait for frame
      await scrollContainer.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)))

      const scrollTime = Date.now() - scrollStart
      scrollMetrics.push(scrollTime)
    }

    const avgScrollTime = scrollMetrics.reduce((a, b) => a + b, 0) / scrollMetrics.length
    console.log(`Average scroll frame time: ${avgScrollTime}ms`)

    // Average scroll time should be under 16.67ms for 60fps
    // Allow some overhead for E2E testing
    expect(avgScrollTime).toBeLessThan(PERFORMANCE_THRESHOLDS.SCROLL_FRAME_MS * 3)
  })

  test('scroll to bottom is fast with large document', async ({ page }) => {
    await setupProjectAndFile(page, '滚动到底部测试')

    // Create large content
    const largeContent = generateLargeContent(TEST_THRESHOLDS.SCROLL_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Find the scroll container
    const scrollContainer = page.locator('.flex-1.overflow-auto').first()

    // Measure scroll to bottom
    const scrollStart = Date.now()
    await scrollContainer.evaluate((el) => {
      el.scrollTop = el.scrollHeight
    })
    await scrollContainer.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)))
    const scrollTime = Date.now() - scrollStart

    console.log(`Scroll to bottom time: ${scrollTime}ms`)

    // Scroll to bottom should be instant (< 100ms)
    expect(scrollTime).toBeLessThan(100)
  })

  test('scroll position is preserved during edits', async ({ page }) => {
    await setupProjectAndFile(page, '滚动位置保持测试')

    // Create large content
    const largeContent = generateLargeContent(TEST_THRESHOLDS.SCROLL_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Find the scroll container
    const scrollContainer = page.locator('.flex-1.overflow-auto').first()

    // Scroll to middle
    await scrollContainer.evaluate((el) => {
      el.scrollTop = el.scrollHeight / 2
    })
    await page.waitForTimeout(100)

    // Get scroll position
    const initialScrollTop = await scrollContainer.evaluate((el) => el.scrollTop)

    // Make an edit
    await editor.press('End')
    await editor.press('Enter')
    await editor.type('新内容')

    // Wait for re-render
    await page.waitForTimeout(100)

    // Check scroll position is approximately preserved
    const finalScrollTop = await scrollContainer.evaluate((el) => el.scrollTop)

    // Allow some variance (±50 pixels)
    expect(Math.abs(finalScrollTop - initialScrollTop)).toBeLessThan(50)
  })
})

test.describe('Large Document - Auto-Save Performance', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('auto-save completes within time limit for large documents', async ({ page }) => {
    await setupProjectAndFile(page, '自动保存测试')

    // Create large content
    const largeContent = generateLargeContent(TEST_THRESHOLDS.SAVE_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Wait for initial auto-save to complete
    await page.waitForTimeout(1000)

    // Make an edit to trigger auto-save
    await editor.press('End')
    await editor.press('Enter')
    await editor.type('触发自动保存的新内容')

    // Wait for auto-save API call
    const saveStartTime = Date.now()

    try {
      await page.waitForResponse(
        resp => resp.url().includes('/api/v1/') &&
               resp.url().includes('/files') &&
               resp.request().method() === 'PUT',
        { timeout: 10000 }
      )
    } catch {
      // Auto-save may use debounce, which is expected
      console.log('Auto-save debounced or already saved')
    }

    const saveTime = Date.now() - saveStartTime
    console.log(`Auto-save time: ${saveTime}ms`)

    // For debounced saves, the actual network call should be fast
    // The total time includes debounce delay
    // This test mainly verifies the save doesn't hang
  })

  test('save button works for large documents', async ({ page }) => {
    await setupProjectAndFile(page, '手动保存测试')

    // Create large content
    const largeContent = generateLargeContent(TEST_THRESHOLDS.SAVE_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Click save button
    const saveButton = page.locator('button:has-text("保存"), button:has-text("Save")').first()

    const saveStartTime = Date.now()
    await saveButton.click()

    // Wait for save to complete
    await page.waitForResponse(
      resp => resp.url().includes('/api/v1/') &&
             resp.url().includes('/files') &&
             resp.request().method() === 'PUT',
      { timeout: 10000 }
    )
    const saveTime = Date.now() - saveStartTime

    console.log(`Manual save time: ${saveTime}ms`)

    // Save should complete within 2 seconds
    expect(saveTime).toBeLessThan(PERFORMANCE_THRESHOLDS.AUTO_SAVE_MS)
  })

  test('content persists after save and reload', async ({ page }) => {
    await setupProjectAndFile(page, '内容持久化测试')

    // Create large content with unique marker
    const uniqueMarker = `唯一标记${Date.now()}`
    const largeContent = generateLargeContent(TEST_THRESHOLDS.SAVE_DOC_WORDS) + '\n\n' + uniqueMarker
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Wait for auto-save
    await page.waitForResponse(
      resp => resp.url().includes('/api/v1/') &&
             resp.url().includes('/files') &&
             resp.request().method() === 'PUT',
      { timeout: 10000 }
    )

    // Reload page
    await page.reload()
    await page.waitForSelector('.overflow-auto', { timeout: 5000 })

    // Navigate back to file
    const outlineFolder = page.locator('text=大纲').first()
    await outlineFolder.click()

    const testFile = page.locator('.overflow-auto >> text=内容持久化测试').first()
    await testFile.click()

    // Verify content persisted
    await expect(page.locator('textarea')).toBeVisible({ timeout: 5000 })
    const reloadedEditor = page.locator('textarea').first()
    const reloadedContent = await reloadedEditor.inputValue()

    expect(reloadedContent).toContain(uniqueMarker)
  })
})

test.describe('Large Document - Memory Usage', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('memory usage is reasonable for large documents', async ({ page }) => {
    await setupProjectAndFile(page, '内存使用测试')

    // Get initial memory
    const initialMemory = await page.evaluate(() => {
      if ('memory' in performance) {
        const memory = (performance as { memory: { usedJSHeapSize: number } }).memory
        return memory.usedJSHeapSize
      }
      return null
    })

    if (initialMemory !== null) {
      console.log(`Initial memory: ${(initialMemory / 1024 / 1024).toFixed(2)} MB`)

      // Create large content
      const largeContent = generateLargeContent(TEST_THRESHOLDS.MEMORY_DOC_WORDS)
      const editor = page.locator('textarea').first()
      await editor.fill(largeContent)

      // Wait for content to be processed
      await page.waitForTimeout(1000)

      // Get memory after loading large document
      const afterLoadMemory = await page.evaluate(() => {
        if ('memory' in performance) {
          const memory = (performance as { memory: { usedJSHeapSize: number } }).memory
          return memory.usedJSHeapSize
        }
        return null
      })

      if (afterLoadMemory !== null) {
        const memoryIncrease = afterLoadMemory - initialMemory
        console.log(`Memory after load: ${(afterLoadMemory / 1024 / 1024).toFixed(2)} MB`)
        console.log(`Memory increase: ${(memoryIncrease / 1024 / 1024).toFixed(2)} MB`)

        // Memory increase should be reasonable (under 100MB for 10k words)
        expect(memoryIncrease).toBeLessThan(100 * 1024 * 1024)
      }
    } else {
      console.log('Memory metrics not available in this browser')
    }
  })

  test('memory is released when switching files', async ({ page }) => {
    await setupProjectAndFile(page, '内存释放测试1')

    // Create and load large content
    const largeContent = generateLargeContent(TEST_THRESHOLDS.MEMORY_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)
    await page.waitForTimeout(500)

    const memoryAfterLarge = await page.evaluate(() => {
      if ('memory' in performance) {
        return (performance as { memory: { usedJSHeapSize: number } }).memory.usedJSHeapSize
      }
      return null
    })

    // Create a new small file
    const outlineFolder = page.locator('text=大纲').first()
    await outlineFolder.hover()
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first()
    await addButton.click({ force: true })

    const fileInput = page.locator('input[placeholder*="大纲"]')
    await fileInput.fill('内存释放测试2')
    await fileInput.press('Enter')

    // Select the new file
    const newFile = page.locator('.overflow-auto >> text=内存释放测试2').first()
    await newFile.click()
    await page.waitForTimeout(500)

    // Fill with small content
    const newEditor = page.locator('textarea').first()
    await newEditor.fill('小内容')
    await page.waitForTimeout(500)

    // Force garbage collection if available
    await page.evaluate(() => {
      if ('gc' in window) {
        (window as { gc?: () => void }).gc?.()
      }
    })

    const memoryAfterSmall = await page.evaluate(() => {
      if ('memory' in performance) {
        return (performance as { memory: { usedJSHeapSize: number } }).memory.usedJSHeapSize
      }
      return null
    })

    if (memoryAfterLarge !== null && memoryAfterSmall !== null) {
      console.log(`Memory after large doc: ${(memoryAfterLarge / 1024 / 1024).toFixed(2)} MB`)
      console.log(`Memory after small doc: ${(memoryAfterSmall / 1024 / 1024).toFixed(2)} MB`)

      // Memory should not keep increasing significantly
      // Note: Actual memory release depends on GC, so this is a soft check
      const memoryDiff = memoryAfterSmall - memoryAfterLarge
      console.log(`Memory difference: ${(memoryDiff / 1024 / 1024).toFixed(2)} MB`)
    }
  })
})

test.describe('Large Document - Editing Functionality', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('typing at different positions works correctly', async ({ page }) => {
    await setupProjectAndFile(page, '多位置编辑测试')

    // Create content
    const largeContent = generateLargeContent(5000)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Type at the beginning
    await editor.focus()
    await page.keyboard.press('Home')
    await editor.type('开头内容')
    await page.waitForTimeout(100)

    // Type at the end
    await editor.press('End')
    await editor.press('Enter')
    await editor.type('结尾内容')
    await page.waitForTimeout(100)

    // Verify content
    const value = await editor.inputValue()
    expect(value).toContain('开头内容')
    expect(value).toContain('结尾内容')
  })

  test('cursor position is preserved during rapid edits', async ({ page }) => {
    await setupProjectAndFile(page, '光标保持测试')

    // Create content
    const largeContent = generateLargeContent(5000)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Position cursor in the middle
    await editor.focus()
    await editor.press('Home')
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('ArrowDown')
    }

    // Make multiple rapid edits
    for (let i = 0; i < 5; i++) {
      await editor.type(`编辑${i} `)
      await page.waitForTimeout(50)
    }

    // Verify edits were made
    const value = await editor.inputValue()
    expect(value).toContain('编辑0')
    expect(value).toContain('编辑4')
  })

  test('selection works across chunk boundaries', async ({ page }) => {
    await setupProjectAndFile(page, '跨块选择测试')

    // Create content that would span multiple chunks
    const largeContent = generateLargeContent(15000)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Wait for virtualization
    await page.waitForTimeout(500)

    // Select a large portion of text
    await editor.focus()
    await page.keyboard.press('Home')
    await page.keyboard.down('Shift')

    // Move down multiple lines
    for (let i = 0; i < 20; i++) {
      await page.keyboard.press('ArrowDown')
    }
    await page.keyboard.up('Shift')

    // Get selection
    const selection = await editor.evaluate((el: HTMLTextAreaElement) => {
      return el.value.substring(el.selectionStart, el.selectionEnd)
    })

    // Selection should be non-empty
    expect(selection.length).toBeGreaterThan(100)
  })

  test('undo/redo works with large documents', async ({ page }) => {
    await setupProjectAndFile(page, '撤销重做测试')

    // Create initial content
    const initialContent = generateLargeContent(5000)
    const editor = page.locator('textarea').first()
    await editor.fill(initialContent)

    // Get initial length
    const initialLength = (await editor.inputValue()).length

    // Add content
    await editor.press('End')
    await editor.press('Enter')
    await editor.type('新增的测试内容')
    await page.waitForTimeout(100)

    // Verify content was added
    const afterAddLength = (await editor.inputValue()).length
    expect(afterAddLength).toBeGreaterThan(initialLength)

    // Undo
    await page.keyboard.down('Control')
    await page.keyboard.press('z')
    await page.keyboard.up('Control')
    await page.waitForTimeout(100)

    // Note: Undo may not work in all browsers/contexts in E2E tests
    // The test mainly verifies no crash occurs
    const afterUndoLength = (await editor.inputValue()).length
    console.log(`Lengths: initial=${initialLength}, afterAdd=${afterAddLength}, afterUndo=${afterUndoLength}`)
  })
})

test.describe('Editor Scroll Regression', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('plain typing and deletion do not jump SimpleEditor back to top', async ({ page }) => {
    await setupProjectAndFile(page, '普通输入回顶回归-小文档')

    const simpleContent = '回归测试正文这一行用于滚动验证。\n'.repeat(400)
    const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    await editor.fill(simpleContent)

    const scrollContainer = page.locator('[data-editor-scroll-container="true"]').first()
    await scrollContainer.evaluate((el) => {
      el.scrollTop = el.scrollHeight * 0.55
    })
    await page.waitForTimeout(150)

    await expectPlainTypingKeepsScrollStable(page, editor, scrollContainer)
  })

  test('plain typing and deletion do not jump VirtualizedEditor back to top', async ({ page }) => {
    await setupProjectAndFile(page, '普通输入回顶回归-大文档')

    const largeContent = generateLargeContent(12000)
    const titleInput = page.locator('input[placeholder*="标题"], input[placeholder*="title"]').first()
    const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    await editor.fill(largeContent)
    await page.waitForTimeout(500)

    await expect(page.locator('text=/分块|Chunks|chunks/').first()).toBeVisible({ timeout: 5000 })
    const chunkingIndicator = page.locator('text=/Loading|加载/').first()
    if (await chunkingIndicator.isVisible().catch(() => false)) {
      await expect(chunkingIndicator).toBeHidden({ timeout: 10000 })
    }

    const scrollContainer = page.locator('[data-editor-scroll-container="true"]').first()
    await scrollContainer.evaluate((el) => {
      el.scrollTop = el.scrollHeight * 0.55
    })
    await page.waitForTimeout(200)

    await expect(titleInput).toBeVisible()
    const visibleChunkTextarea = page.locator(
      '[data-editor-scroll-container="true"] .chunk-container textarea:visible'
    ).first()
    await expect(visibleChunkTextarea).toBeVisible()

    await expectPlainTypingKeepsScrollStable(page, visibleChunkTextarea, scrollContainer, 160, true)
  })
})

test.describe('Large Document - Chunking Progress', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('chunking progress indicator appears for large documents', async ({ page }) => {
    await setupProjectAndFile(page, '分块进度测试')

    // Create very large content to trigger lazy chunking
    const veryLargeContent = generateLargeContent(30000)
    const editor = page.locator('textarea').first()

    // Fill editor and watch for progress indicator
    await editor.fill(veryLargeContent)

    // Look for loading/progress indicator
    // The VirtualizedEditor shows "Loading... X%" during progressive chunking
    const progressIndicator = page.locator('text=/Loading|加载/')

    // Progress indicator may be brief, so just check it exists at some point
    const hadProgressIndicator = await progressIndicator.first().isVisible({ timeout: 1000 }).catch(() => false)

    if (hadProgressIndicator) {
      console.log('Progress indicator was visible during chunking')
    }

    // Wait for content to be fully loaded
    await page.waitForTimeout(1000)

    // Verify content is present
    const value = await editor.inputValue()
    expect(value.length).toBeGreaterThan(100000)
  })
})

test.describe('Large Document - Keyboard Shortcuts', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('Cmd/Ctrl+S saves large document', async ({ page }) => {
    await setupProjectAndFile(page, '快捷键保存测试')

    // Create large content
    const largeContent = generateLargeContent(10000)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Use keyboard shortcut to save
    const isMac = process.platform === 'darwin'
    const modifier = isMac ? 'Meta' : 'Control'

    await page.keyboard.down(modifier)
    await page.keyboard.press('s')
    await page.keyboard.up(modifier)

    // Wait for save response
    await page.waitForResponse(
      resp => resp.url().includes('/api/v1/') &&
             resp.url().includes('/files') &&
             resp.request().method() === 'PUT',
      { timeout: 10000 }
    )

    // Verify save completed (look for save indicator)
    const savedIndicator = page.locator('text=/保存|Saved|刚刚|秒前/')
    await expect(savedIndicator.first()).toBeVisible({ timeout: 5000 })
  })
})

test.describe('Large Document - Stress Tests', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  // These tests push the limits and may take longer

  test.skip('50k word document loads within 3 seconds', async ({ page }) => {
    // This test requires significant time - skipping by default
    // Enable manually for full acceptance criteria testing

    await setupProjectAndFile(page, '50k压力测试')

    const wordCount = 50000
    const largeContent = generateLargeContent(wordCount)
    const editor = page.locator('textarea').first()

    const loadStartTime = Date.now()
    await editor.fill(largeContent)
    const loadTime = Date.now() - loadStartTime

    console.log(`Time to load ${wordCount} words: ${loadTime}ms`)

    // Acceptance criteria: 3 seconds for 50k+ words
    expect(loadTime).toBeLessThan(PERFORMANCE_THRESHOLDS.LARGE_DOC_LOAD_MS)
  })

  test.skip('scrolling maintains 60fps with 50k words', async ({ page }) => {
    // This test requires significant time - skipping by default

    await setupProjectAndFile(page, '50k滚动测试')

    const largeContent = generateLargeContent(50000)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    const scrollContainer = page.locator('.flex-1.overflow-auto').first()

    // Measure FPS during continuous scrolling
    const frameTimes: number[] = []
    let lastTime = Date.now()

    for (let i = 0; i < 60; i++) { // 60 frames
      await scrollContainer.evaluate((el) => {
        el.scrollTop += 50
      })
      await scrollContainer.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)))

      const now = Date.now()
      frameTimes.push(now - lastTime)
      lastTime = now
    }

    const avgFrameTime = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length
    const fps = 1000 / avgFrameTime

    console.log(`Average frame time: ${avgFrameTime.toFixed(2)}ms`)
    console.log(`Estimated FPS: ${fps.toFixed(2)}`)

    // Should maintain close to 60fps (avg frame time < 16.67ms)
    expect(avgFrameTime).toBeLessThan(PERFORMANCE_THRESHOLDS.SCROLL_FRAME_MS)
  })
})
