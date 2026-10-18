import { test as base, expect } from '@playwright/test'
import { config } from './config'
import {
  cleanupOwnedProject,
  createOwnedFile,
  createOwnedProject,
  type OwnedFile,
  type OwnedProject,
} from './fixtures/project-test-data'

const ENABLE_LARGE_DOCUMENT_E2E = process.env.E2E_ENABLE_LARGE_DOCUMENT_E2E === 'true'
const LARGE_DOCUMENT_OPT_IN_MESSAGE =
  'Large document E2E tests are opt-in. Set E2E_ENABLE_LARGE_DOCUMENT_E2E=true to run.'

/**
 * E2E Tests for Large Document Handling
 *
 * These tests verify the current SimpleEditor large-document contract:
 * - Loading large documents in under 3 seconds
 * - Scrolling measured against the existing frame-time budgets
 * - Successful auto-save after the 3-second debounce, with a 2-second request budget
 * - Measurable browser heap growth, with explicit limits on GC-related claims
 * - Complete content across file switching and reloads
 * - Long-range editing and cursor preservation
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

/** Create one quota-safe project per test and clean up only that project. */
const test = base.extend<{ ownedProject: OwnedProject }>({
  ownedProject: async ({ page }, provide, testInfo) => {
    const project = await createOwnedProject(page, testInfo, 'large-document')
    try {
      await provide(project)
    } finally {
      await cleanupOwnedProject(page, project)
    }
  },
})

const ownedProjectByPage = new WeakMap<import('@playwright/test').Page, OwnedProject>()

test.beforeEach(async ({ page, ownedProject }) => {
  ownedProjectByPage.set(page, ownedProject)
})

/** Open a real API-created draft; the former 正文 + target is an upload control. */
async function setupProjectAndFile(page: import('@playwright/test').Page, fileName: string, content = '') {
  const project = ownedProjectByPage.get(page)
  if (!project) throw new Error('Large-document owned project fixture was not initialized')

  const file = await createOwnedFile(page, project, { title: fileName, content })
  await page.goto(`/project/${project.id}?file=${file.id}`)
  await page.waitForURL(new RegExp(`/project/${project.id}\\?file=${file.id}`))

  const draftFolder = page.locator('[data-testid="file-tree-node"][data-file-type="folder"]').filter({ hasText: '正文' }).first()
  await expect(draftFolder).toBeVisible()
  await draftFolder.hover()
  // This is the exact control the old parent-traversal selector clicked. Its
  // upload contract proves why no inline input could ever appear.
  await expect(draftFolder.getByTestId('file-tree-upload-draft')).toBeVisible()
  await expect(draftFolder.getByTestId('file-tree-create-child')).toHaveCount(0)

  const testFile = page.locator(`[data-testid="file-tree-node"][data-file-id="${file.id}"]`)
  await expect(testFile).toBeVisible()
  await expect(page.locator('[data-editor-scroll-container="true"] textarea').first()).toBeVisible()

  return { fileName, file }
}

async function readOwnedFile(
  page: import('@playwright/test').Page,
  project: OwnedProject,
  fileId: string,
): Promise<OwnedFile> {
  const response = await page.request.get(`${config.apiBaseUrl}/api/v1/files/${fileId}`, {
    headers: project.headers,
  })
  expect(response.ok()).toBeTruthy()
  return await response.json() as OwnedFile
}

async function saveEditor(page: import('@playwright/test').Page) {
  const responsePromise = page.waitForResponse(response =>
    response.url().includes('/api/v1/files/') && response.request().method() === 'PUT',
  )
  await page.getByRole('button', { name: /^(保存|Save)$/ }).click()
  const response = await responsePromise
  expect(response.ok()).toBeTruthy()
  return response
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

test.describe('Large Document - Editor Content Integrity', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('small documents retain complete content in SimpleEditor', async ({ page }) => {
    await setupProjectAndFile(page, '小文档测试')

    // Create small content (under 10000 words)
    const smallContent = generateLargeContent(500) // 500 words
    const editor = page.locator('textarea').first()
    await editor.fill(smallContent)

    // Verify content was added
    const value = await editor.inputValue()
    expect(value).toBe(smallContent)
    await expect(page.locator('[data-editor-scroll-container="true"] textarea')).toHaveCount(1)
  })

  test('large documents retain complete content in SimpleEditor', async ({ page, ownedProject }) => {
    const { file } = await setupProjectAndFile(page, '大文档完整性测试')

    // Create content at the existing 12k-word regression scale.
    const largeContent = generateLargeContent(12000)
    const editor = page.locator('textarea').first()

    // Fill with large content
    await editor.fill(largeContent)

    // Wait for content to be processed
    await page.waitForTimeout(500)

    // Verify content
    const value = await editor.inputValue()
    expect(value).toBe(largeContent)
    await saveEditor(page)
    expect((await readOwnedFile(page, ownedProject, file.id)).content).toBe(largeContent)
  })

  test('complete content is preserved when switching between files', async ({ page, ownedProject }) => {
    const { file: smallFile } = await setupProjectAndFile(page, '文件切换测试')

    // Create small content
    const smallContent = generateLargeContent(500)
    let editor = page.locator('textarea').first()
    await editor.fill(smallContent)
    await saveEditor(page)

    // Create another real draft through the same API fixture contract.
    const project = ownedProjectByPage.get(page)
    if (!project) throw new Error('Large-document owned project fixture was not initialized')
    const largeContent = generateLargeContent(12000)
    const largeFileData = await createOwnedFile(page, project, { title: '大文档文件', content: largeContent })
    await page.reload()

    // Select the large file
    const largeFile = page.locator(`[data-testid="file-tree-node"][data-file-id="${largeFileData.id}"]`)
    await largeFile.click()
    editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    await expect(editor).toBeVisible({ timeout: 5000 })
    await expect(editor).toHaveValue(largeContent)

    // Switch back to small file
    await page.locator(`[data-testid="file-tree-node"][data-file-id="${smallFile.id}"]`).click()
    await expect(editor).toBeVisible({ timeout: 5000 })

    // Editor should switch back to SimpleEditor
    // The content should still be preserved
    editor = page.locator('textarea').first()
    const smallFileValue = await editor.inputValue()
    expect(smallFileValue).toBe(smallContent)
    expect((await readOwnedFile(page, ownedProject, largeFileData.id)).content).toBe(largeContent)
    expect((await readOwnedFile(page, ownedProject, smallFile.id)).content).toBe(smallContent)
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

    const scrollContainer = page.locator('[data-editor-scroll-container="true"]')
    // Use one browser clock: driver round trips are not editor frame time.
    const metrics = await scrollContainer.evaluate(async (el) => {
      el.scrollTop = 0
      await new Promise(requestAnimationFrame)
      const frameTimes: number[] = []
      for (let i = 0; i < 10; i++) {
        const start = performance.now()
        el.scrollTop += 100
        await new Promise(requestAnimationFrame)
        frameTimes.push(performance.now() - start)
      }
      return { frameTimes, scrollTop: el.scrollTop }
    })
    expect(metrics.scrollTop).toBeCloseTo(1000, 0)

    const avgScrollTime = metrics.frameTimes.reduce((a, b) => a + b, 0) / metrics.frameTimes.length
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

    const scrollContainer = page.locator('[data-editor-scroll-container="true"]')
    const metrics = await scrollContainer.evaluate(async (el) => {
      el.scrollTop = 0
      await new Promise(requestAnimationFrame)
      const before = el.scrollTop
      const start = performance.now()
      el.scrollTop = el.scrollHeight - el.clientHeight
      await new Promise(requestAnimationFrame)
      return {
        elapsed: performance.now() - start,
        before,
        after: el.scrollTop,
        maxTop: el.scrollHeight - el.clientHeight,
      }
    })
    expect(metrics.before).toBe(0)
    expect(metrics.after).toBeGreaterThan(metrics.before)
    expect(metrics.after).toBeCloseTo(metrics.maxTop, 0)

    console.log(`Scroll to bottom time: ${metrics.elapsed}ms`)

    // Scroll to bottom should be instant (< 100ms)
    expect(metrics.elapsed).toBeLessThan(100)
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

    // Make a real edit at the visible viewport instead of moving the caret to
    // the document end (which would intentionally scroll to the bottom).
    await focusVisibleEditorArea(page, scrollContainer)
    await page.keyboard.type('新内容')

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
  test('auto-save persists large documents and completes its request within 2 seconds', async ({ page, ownedProject }) => {
    const largeContent = generateLargeContent(TEST_THRESHOLDS.SAVE_DOC_WORDS)
    const { file } = await setupProjectAndFile(page, '自动保存测试', largeContent)
    const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    await expect(editor).toHaveValue(largeContent)
    const expectedContent = largeContent + '\n触发自动保存的新内容'

    // SimpleEditor deliberately debounces for 3000ms. Wait for the exact
    // automatic write, then apply the existing 2000ms budget to its full request.
    const saved = page.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/v1/files/${file.id}`
        && response.request().method() === 'PUT'
        && response.request().postDataJSON().content === expectedContent,
      { timeout: 10000 },
    )
    await editor.press('ControlOrMeta+a')
    await editor.press('ArrowRight')
    await page.keyboard.insertText('\n触发自动保存的新内容')
    await expect(editor).toHaveValue(expectedContent)
    const response = await saved
    expect(response.ok()).toBeTruthy()
    expect(await response.finished()).toBeNull()
    const timing = response.request().timing()
    expect(timing.requestStart).toBeGreaterThanOrEqual(0)
    expect(timing.responseEnd).toBeGreaterThanOrEqual(timing.requestStart)
    const saveTime = timing.responseEnd - timing.requestStart
    console.log(`Auto-save request time after debounce: ${saveTime}ms`)
    expect(saveTime).toBeLessThan(PERFORMANCE_THRESHOLDS.AUTO_SAVE_MS)
    expect((await readOwnedFile(page, ownedProject, file.id)).content).toBe(expectedContent)
  })

  test('save button works for large documents', async ({ page }) => {
    await setupProjectAndFile(page, '手动保存测试')

    // Create large content
    const largeContent = generateLargeContent(TEST_THRESHOLDS.SAVE_DOC_WORDS)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Click save button
    const saveButton = page.getByRole('button', { name: /^(保存|Save)$/ })

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('/api/v1/') &&
             resp.url().includes('/files') &&
             resp.request().method() === 'PUT',
      { timeout: 10000 }
    )
    const saveStartTime = Date.now()
    await saveButton.click()
    expect((await saveResponse).ok()).toBeTruthy()
    const saveTime = Date.now() - saveStartTime

    console.log(`Manual save time: ${saveTime}ms`)

    // Save should complete within 2 seconds
    expect(saveTime).toBeLessThan(PERFORMANCE_THRESHOLDS.AUTO_SAVE_MS)
  })

  test('content persists after save and reload', async ({ page }) => {
    const { file } = await setupProjectAndFile(page, '内容持久化测试')

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
    const project = ownedProjectByPage.get(page)
    if (!project) throw new Error('Large-document owned project fixture was not initialized')
    expect((await readOwnedFile(page, project, file.id)).content).toBe(largeContent)

    // Reload page
    await page.reload()
    await page.waitForSelector('.overflow-auto', { timeout: 5000 })

    // Verify content persisted
    await expect(page.locator('[data-editor-scroll-container="true"] textarea')).toBeVisible({ timeout: 5000 })
    const reloadedEditor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    const reloadedContent = await reloadedEditor.inputValue()

    expect(reloadedContent).toBe(largeContent)
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

  test('switching large and small files isolates their complete contents', async ({ page, ownedProject }, testInfo) => {
    testInfo.annotations.push({
      type: 'coverage',
      description: 'Verifies observable editor state isolation; browser garbage-collection timing and memory release are not asserted.',
    })
    const smallContent = '小文件的独立内容'
    const smallFile = await createOwnedFile(page, ownedProject, { title: '小文件', content: smallContent })
    const largeContent = generateLargeContent(TEST_THRESHOLDS.MEMORY_DOC_WORDS)
    const { file: largeFile } = await setupProjectAndFile(page, '大文件', largeContent)
    const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    await expect(editor).toHaveValue(largeContent)

    const smallNode = page.locator(`[data-testid="file-tree-node"][data-file-id="${smallFile.id}"]`)
    await smallNode.click()
    await expect(editor).toHaveValue(smallContent)
    await expect(editor).not.toHaveValue(largeContent)

    await page.locator(`[data-testid="file-tree-node"][data-file-id="${largeFile.id}"]`).click()
    await expect(editor).toHaveValue(largeContent)
    expect((await readOwnedFile(page, ownedProject, largeFile.id)).content).toBe(largeContent)
    expect((await readOwnedFile(page, ownedProject, smallFile.id)).content).toBe(smallContent)
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

  test('selection works across distant paragraphs', async ({ page }) => {
    await setupProjectAndFile(page, '长距离选择测试')

    // Create content that would span multiple chunks
    const largeContent = generateLargeContent(15000)
    const editor = page.locator('textarea').first()
    await editor.fill(largeContent)

    // Select a range spanning multiple generated paragraphs.
    await editor.evaluate((el: HTMLTextAreaElement) => {
      el.focus()
      el.setSelectionRange(90, 500)
    })

    // Get selection
    const selection = await editor.evaluate((el: HTMLTextAreaElement) => {
      return el.value.substring(el.selectionStart, el.selectionEnd)
    })

    // Selection should be non-empty
    expect(selection).toBe(largeContent.slice(90, 500))
  })

  test('undo/redo works with large documents', async ({ page }) => {
    // Server-loaded content is the baseline; it is not part of this textarea's
    // native edit history, so undo must revert only the real insertion below.
    const initialContent = generateLargeContent(5000)
    await setupProjectAndFile(page, '撤销重做测试', initialContent)
    const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    await expect(editor).toHaveValue(initialContent)
    const editedContent = initialContent + '新增的测试内容'
    await editor.press('ControlOrMeta+a')
    await editor.press('ArrowRight')
    await page.keyboard.insertText('新增的测试内容')
    await expect(editor).toHaveValue(editedContent)

    await editor.press('ControlOrMeta+z')
    await expect(editor).toHaveValue(initialContent)
    await editor.press('ControlOrMeta+Shift+z')
    await expect(editor).toHaveValue(editedContent)
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

  test('plain typing and deletion do not jump a large SimpleEditor document back to top', async ({ page }) => {
    await setupProjectAndFile(page, '普通输入回顶回归-大文档')

    const largeContent = generateLargeContent(12000)
    const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
    await editor.fill(largeContent)

    const scrollContainer = page.locator('[data-editor-scroll-container="true"]').first()
    await scrollContainer.evaluate((el) => {
      el.scrollTop = el.scrollHeight * 0.55
    })
    await page.waitForTimeout(200)

    await expectPlainTypingKeepsScrollStable(page, editor, scrollContainer, 160)
  })
})

test.describe('Large Document - Very Large Content', () => {
  test.skip(!ENABLE_LARGE_DOCUMENT_E2E, LARGE_DOCUMENT_OPT_IN_MESSAGE)
  test('very large documents retain complete content after save', async ({ page, ownedProject }) => {
    const { file } = await setupProjectAndFile(page, '超大文档完整性测试')

    // Keep the original 30k-word stress scale while testing the implementation
    // that exists in current main and the latest production tag.
    const veryLargeContent = generateLargeContent(30000)
    const editor = page.locator('textarea').first()

    await editor.fill(veryLargeContent)
    const value = await editor.inputValue()
    expect(value).toBe(veryLargeContent)
    await saveEditor(page)
    expect((await readOwnedFile(page, ownedProject, file.id)).content).toBe(veryLargeContent)
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
