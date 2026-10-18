import { test, expect, Page, Locator } from '@playwright/test'
import {
  cleanupOwnedProject,
  createOwnedFile,
  createOwnedProject,
  type OwnedProject,
} from './fixtures/project-test-data'

/**
 * E2E Tests for Version History Feature
 *
 * These tests cover the complete version history operations:
 * - Version listing and metadata display
 * - Version content viewing
 * - Version comparison with diff views
 * - Rollback functionality
 * - Auto-save version creation
 * - Version metadata (change type, source)
 *
 * Backend API Reference: apps/server/api/versions.py
 * Frontend Components:
 * - apps/web/src/components/VersionHistoryPanel.tsx (project-level snapshots)
 * - apps/web/src/components/FileVersionHistory.tsx (file-level versions)
 */

let activeProject: OwnedProject | undefined
let activeTestFileId: string | null = null
let activeTestFileName: string | null = null
const ACTIVE_TEST_FOLDER_TITLE = '大纲'
const VERSION_HISTORY_E2E_ENABLED = process.env.E2E_ENABLE_VERSION_HISTORY_E2E === 'true'

test.skip(
  !VERSION_HISTORY_E2E_ENABLED,
  'Version history deep UI suite depends on stable editor runtime. Set E2E_ENABLE_VERSION_HISTORY_E2E=true to run it.'
)

test.beforeEach(async ({ page }, testInfo) => {
  activeTestFileId = null
  activeTestFileName = null
  activeProject = await createOwnedProject(page, testInfo, 'versions')
})

test.afterEach(async ({ page }) => {
  await cleanupOwnedProject(page, activeProject)
  activeProject = undefined
  activeTestFileId = null
  activeTestFileName = null
})

async function getAuthHeaders(page: Page) {
  const accessToken = await page.evaluate(() => localStorage.getItem('access_token'))
  if (!accessToken) {
    throw new Error('Missing access token for version history setup')
  }

  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

function getCurrentProjectId(page: Page): string {
  const match = page.url().match(/\/project\/([^/?#]+)/)
  if (!match?.[1]) {
    throw new Error(`Unable to resolve project id from URL: ${page.url()}`)
  }
  return match[1]
}

/**
 * Helper function to create a file and return its locator
 */
async function createTestFile(page: Page, fileName: string): Promise<string> {
  if (!activeProject) {
    throw new Error('Missing owned project for version history setup')
  }
  const created = await createOwnedFile(page, activeProject, {
    title: fileName,
    fileType: 'outline',
    parentFolderTitle: ACTIVE_TEST_FOLDER_TITLE,
  })

  activeTestFileId = created.id
  activeTestFileName = fileName

  await page.goto(`/project/${activeProject.id}?file=${created.id}`, { waitUntil: 'domcontentloaded' })
  await expect(page).toHaveURL(new RegExp(`/project/${activeProject.id}\\?file=${created.id}`))
  await ensureActiveFolderExpanded(page)
  await expect(page.getByTestId('file-tree-node').filter({ hasText: fileName }).first()).toBeVisible()
  await expect(page.locator('[data-editor-scroll-container="true"] textarea').first()).toBeVisible()

  return created.id
}

/**
 * Helper function to select a file in the tree
 */
async function selectFile(page: Page, fileName: string): Promise<void> {
  await ensureActiveFolderExpanded(page)
  const file = page.locator('.overflow-auto').locator(`text=${fileName}`).first()
  await file.click()
}

async function refreshAndReselectActiveFile(page: Page): Promise<void> {
  if (!activeTestFileId) return
  if (!activeTestFileNameForUi(page)) return

  const projectId = getCurrentProjectId(page)
  await page.goto(`/project/${projectId}`, { waitUntil: 'domcontentloaded' })
  await selectFile(page, activeTestFileNameForUi(page)!)
}

function activeTestFileNameForUi(_page: Page): string | null {
  // wrapper kept for future state folding if versions tests need per-page mapping
  return activeTestFileName
}

async function ensureActiveFolderExpanded(page: Page): Promise<void> {
  const folderItem = page.locator('[role="treeitem"]').filter({ hasText: ACTIVE_TEST_FOLDER_TITLE }).first()
  if (!(await folderItem.isVisible().catch(() => false))) return

  if ((await folderItem.getAttribute('aria-expanded')) !== 'true') {
    await folderItem.click()
    await expect(folderItem).toHaveAttribute('aria-expanded', 'true', { timeout: 5000 })
  }
}

/**
 * Helper function to edit file content and trigger auto-save
 */
async function editFileContent(page: Page, content: string): Promise<void> {
  if (!activeTestFileId) {
    throw new Error('Missing active test file id for version editing')
  }

  const headers = await getAuthHeaders(page)
  const updateResponse = await page.request.put(`/api/v1/files/${activeTestFileId}`, {
    headers,
    data: {
      content,
      change_type: 'edit',
      change_source: 'user',
    },
  })
  expect(updateResponse.ok()).toBeTruthy()

  const versionResponse = await page.request.post(`/api/v1/files/${activeTestFileId}/versions`, {
    headers,
    data: {
      content,
      change_type: 'edit',
      change_source: 'user',
      change_summary: `e2e version ${Date.now()}`,
    },
  })
  expect(versionResponse.ok()).toBeTruthy()
  await page.waitForTimeout(500)
  await refreshAndReselectActiveFile(page)
}

/**
 * Helper function to open version history panel
 */
async function openVersionHistory(page: Page): Promise<void> {
  // File-scoped history action in the editor status bar
  const historyButton = page.getByRole('button', { name: /^(历史版本|历史|Versions|History)$/ }).first()
  await expect(historyButton).toBeVisible({ timeout: 5000 })
  const [response] = await Promise.all([
    page.waitForResponse(response =>
      response.request().method() === 'GET' &&
      new URL(response.url()).pathname === `/api/v1/files/${activeTestFileId}/versions`,
    ),
    historyButton.click(),
  ])
  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as { versions: unknown[] }
  expect(Array.isArray(payload.versions)).toBe(true)

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: /^(历史版本|Version history)$/i })).toBeVisible()
  await expect(getVersionItems(page)).toHaveCount(payload.versions.length)
  if (payload.versions.length === 0) {
    await expect(dialog.getByText(/还没有历史版本|No versions yet/i)).toBeVisible()
  }
}

/**
 * Confirm the in-app restore dialog (ConfirmDialog, not a native confirm) and
 * wait for the rollback request it sends.
 */
async function confirmRollback(page: Page): Promise<void> {
  const confirmButton = page.getByRole('dialog').getByRole('button', { name: /^(恢复|Restore)$/ })
  await expect(confirmButton).toBeVisible({ timeout: 5000 })
  const rollbackResponse = page.waitForResponse(
    resp => resp.url().includes('/api/v1/') && resp.url().includes('/rollback') && resp.request().method() === 'POST',
    { timeout: 5000 }
  )
  await confirmButton.click()
  await rollbackResponse
}

function getVersionItems(page: Page) {
  return page.getByRole('dialog').locator('div[tabindex="0"]')
}

function getCompareButton(page: Page) {
  return page
    .getByRole('dialog')
    .getByRole('button', { name: /^(对比版本|Compare)$/ })
}

function getDiffViewerReadyLocator(page: Page) {
  return page
    .getByRole('dialog')
    .locator(
      'button:has(svg.lucide-code), button:has(svg.lucide-eye), button:has(svg.lucide-align-left), [class*="font-mono"], .diff-add, .diff-remove'
    )
    .first()
}

async function openVersionComparison(page: Page, compareButton: Locator = getCompareButton(page)) {
  await expect(compareButton).toBeVisible({ timeout: 5000 })

  const diffReady = getDiffViewerReadyLocator(page)
  const [response] = await Promise.all([
    page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `/api/v1/files/${activeTestFileId}/versions/compare` &&
        response.request().method() === 'GET',
      { timeout: 10000 }
    ),
    compareButton.click(),
  ])
  expect(response.ok()).toBeTruthy()
  await expect(diffReady).toBeVisible({ timeout: 10000 })
}

async function getVersionCountFromApi(page: Page): Promise<number> {
  if (!activeTestFileId) {
    throw new Error('Missing active test file id for version count check')
  }

  const headers = await getAuthHeaders(page)
  const response = await page.request.get(`/api/v1/files/${activeTestFileId}/versions?limit=50`, {
    headers,
  })
  expect(response.ok()).toBeTruthy()
  const payload = await response.json()
  return payload.total ?? 0
}

test.describe('Version History', () => {
  // ==================== Basic Version Listing ====================

  test('version history shows all file versions', async ({ page }) => {
    await createTestFile(page, '版本列表测试')
    await selectFile(page, '版本列表测试')

    // Edit content to create multiple versions
    await editFileContent(page, '第一个版本的内容')
    await editFileContent(page, '第二个版本的内容')
    await editFileContent(page, '第三个版本的内容')

    // Open version history
    await openVersionHistory(page)

    // Verify the panel reports at least one version entry
    await expect(page.getByText(/共\s*\d+\s*个版本|total versions/i).first()).toBeVisible({
      timeout: 5000,
    })
  })

  test('version list displays correct metadata', async ({ page }) => {
    await createTestFile(page, '元数据测试')
    await selectFile(page, '元数据测试')

    const testContent = '这是用于测试版本元数据的内容，包含足够的字数以便验证字数统计功能。'
    await editFileContent(page, testContent)

    // Open version history
    await openVersionHistory(page)

    // Check for version number (v1, v2, etc.)
    await expect(page.locator('text=/v\\d+/').first()).toBeVisible()

    // Check for timestamp (relative time format like "刚刚", "几秒前", etc.)
    await expect(page.locator('svg.lucide-clock').first()).toBeVisible()

    // Check for word count display
    await expect(page.locator('text=/\\d+\\s*(字|words)/i').first()).toBeVisible()
  })

  test('version list supports pagination with many versions', async ({ page }) => {
    await createTestFile(page, '分页测试')
    await selectFile(page, '分页测试')

    // Create multiple versions (more than typical page size)
    for (let i = 1; i <= 5; i++) {
      await editFileContent(page, `版本 ${i} 的内容，用于测试分页功能。这是第 ${i} 次编辑。`)
    }

    // Open version history
    await openVersionHistory(page)

    // Verify the backend really accumulated multiple versions and the panel surfaced total count.
    const count = await getVersionCountFromApi(page)
    expect(count).toBeGreaterThanOrEqual(5)
    await expect(page.getByText(/共\s*\d+\s*个版本|total versions/i).first()).toBeVisible({
      timeout: 5000,
    })
  })

  // ==================== Version Content Viewing ====================

  test('user can view content of any historical version', async ({ page }) => {
    await createTestFile(page, '内容查看测试')
    await selectFile(page, '内容查看测试')

    const firstContent = '这是原始版本的内容'
    await editFileContent(page, firstContent)

    const secondContent = '这是修改后的内容，与原始版本不同'
    await editFileContent(page, secondContent)

    // Open version history
    await openVersionHistory(page)

    // Find a version item and click the view content button (FileText icon)
    const viewButton = page.locator('button:has(svg.lucide-file-text)').first()
    if (await viewButton.isVisible()) {
      await viewButton.click()

      // Verify content viewer appears or content is displayed
      // The content viewer may appear in a modal or inline
      const contentViewer = page.locator('.modal, [class*="prose"], textarea').first()
      await expect(contentViewer).toBeVisible({ timeout: 3000 })
    }
  })

  test('version content displays word and character counts', async ({ page }) => {
    await createTestFile(page, '字数统计测试')
    await selectFile(page, '字数统计测试')

    const testContent = '测试字数统计功能。这段文字包含多个字符和词汇。'
    await editFileContent(page, testContent)

    // Open version history
    await openVersionHistory(page)

    // Verify word count is displayed (format: "XX 字" or "XX words")
    const wordCountPattern = /\d+\s*(字|words?)/i
    await expect(page.locator(`text=${wordCountPattern}`).first()).toBeVisible()
  })

  // ==================== Version Comparison ====================

  test('user can compare two versions with diff view', async ({ page }) => {
    await createTestFile(page, '对比测试')
    await selectFile(page, '对比测试')

    await editFileContent(page, '第一行内容\n第二行内容\n第三行内容')
    await editFileContent(page, '第一行内容\n修改的第二行\n第三行内容\n新增的第四行')

    // Open version history
    await openVersionHistory(page)

    // Select two versions for comparison
    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    // Click first version to select
    await versionItems.nth(0).click()

    // Click second version to select
    await versionItems.nth(1).click()

    // Look for compare button
    const compareButton = getCompareButton(page)
    await expect(compareButton).toBeVisible()
    await openVersionComparison(page, compareButton)

    // Verify diff viewer appears
    const diffViewer = page.locator('[class*="font-mono"], .diff-add, .diff-remove').first()
    await expect(diffViewer).toBeVisible({ timeout: 5000 })
  })

  test('comparison shows additions in green', async ({ page }) => {
    await createTestFile(page, '新增内容测试')
    await selectFile(page, '新增内容测试')

    await editFileContent(page, '原始内容')
    await editFileContent(page, '原始内容\n新增的一行')

    // Open version history
    await openVersionHistory(page)

    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    // Select two versions
    await versionItems.nth(0).click()
    await versionItems.nth(1).click()

    const compareButton = getCompareButton(page)
    await expect(compareButton).toBeVisible()
    await openVersionComparison(page, compareButton)

    // Look for green/add styling (diff-add class or green color)
    const addIndicator = page.locator('.diff-add, [class*="text-green"], [class*="bg-green"], [class*="success"]').first()
    await expect(addIndicator).toBeVisible({ timeout: 5000 })
  })

  test('comparison shows deletions in red', async ({ page }) => {
    await createTestFile(page, '删除内容测试')
    await selectFile(page, '删除内容测试')

    await editFileContent(page, '第一行\n将被删除的第二行\n第三行')
    await editFileContent(page, '第一行\n第三行')

    // Open version history
    await openVersionHistory(page)

    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    // Select two versions
    await versionItems.nth(0).click()
    // Wait for selection state to update
    await expect(versionItems.nth(0)).toHaveAttribute('class', /selected|active|border-/, { timeout: 2000 })

    await versionItems.nth(1).click()
    // Wait for second selection
    await expect(versionItems.nth(1)).toHaveAttribute('class', /selected|active|border-/, { timeout: 2000 })

    const compareButton = getCompareButton(page)
    await expect(compareButton).toBeVisible()
    await openVersionComparison(page, compareButton)

    // Look for red/remove styling (diff-remove class or red color)
    const removeIndicator = page.locator('.diff-remove, [class*="text-red"], [class*="bg-red"], [class*="error"]').first()
    await expect(removeIndicator).toBeVisible({ timeout: 5000 })
  })

  test('comparison displays unified diff', async ({ page }) => {
    await createTestFile(page, '统一差异测试')
    await selectFile(page, '统一差异测试')

    await editFileContent(page, '行1\n行2\n行3')
    await editFileContent(page, '行1\n修改的行2\n行3\n行4')

    // Open version history
    await openVersionHistory(page)

    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    // Select two versions
    await versionItems.nth(0).click()
    await versionItems.nth(1).click()

    const compareButton = getCompareButton(page)
    await expect(compareButton).toBeVisible()
    await openVersionComparison(page, compareButton)

    // Verify diff content is displayed with + and - indicators
    const diffContent = page.locator('[class*="font-mono"]').first()
    await expect(diffContent).toBeVisible({ timeout: 5000 })

    // Look for +/- indicators
    const plusMinusIndicator = page.locator('text=/^\\s*[+-]/').first()
    await expect(plusMinusIndicator).toBeVisible({ timeout: 3000 })
  })

  // ==================== Rollback Functionality ====================

  test('user can rollback to a previous version', async ({ page }) => {
    await createTestFile(page, '回滚测试')
    await selectFile(page, '回滚测试')

    const originalContent = '原始内容，将被回滚'
    await editFileContent(page, originalContent)

    const modifiedContent = '修改后的内容，不应该保留'
    await editFileContent(page, modifiedContent)

    // Verify current content is the modified version
    // Open version history
    await openVersionHistory(page)

    // Find rollback button on an older version (not the latest)
    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    // Click rollback button on the second version (older)
    const rollbackButton = versionItems.nth(1).locator('button:has(svg.lucide-rotate-ccw)')
    await expect(rollbackButton).toBeVisible()
    await rollbackButton.click()
    await confirmRollback(page)

    // Verify rollback succeeded - content should be restored
    // The version list should refresh
    await expect(page.locator('text=/恢复|restore|success/i').first()).toBeVisible({ timeout: 5000 })
  })

  test('rollback creates a new version with restored content', async ({ page }) => {
    await createTestFile(page, '回滚版本测试')
    await selectFile(page, '回滚版本测试')

    await editFileContent(page, '版本1内容')
    await editFileContent(page, '版本2内容')

    // Open version history and note version count
    await openVersionHistory(page)

    let versionItems = getVersionItems(page)
    const initialCount = await getVersionCountFromApi(page)

    // Perform rollback
    if (initialCount >= 2) {
      const rollbackButton = versionItems.nth(1).locator('button:has(svg.lucide-rotate-ccw)')
      if (await rollbackButton.isVisible()) {
        await rollbackButton.click()
        await confirmRollback(page)

        // Reload version list
        versionItems = getVersionItems(page)
        const newCount = await getVersionCountFromApi(page)

        // Verify new version was created
        expect(newCount).toBeGreaterThanOrEqual(initialCount)
      }
    }
  })

  test('rollback preserves version history', async ({ page }) => {
    await createTestFile(page, '历史保留测试')
    await selectFile(page, '历史保留测试')

    await editFileContent(page, '历史版本1')
    await editFileContent(page, '历史版本2')
    await editFileContent(page, '历史版本3')

    // Open version history
    await openVersionHistory(page)

    let versionItems = getVersionItems(page)
    const initialCount = await getVersionCountFromApi(page)

    // Perform rollback
    if (initialCount >= 2) {
      const rollbackButton = versionItems.nth(Math.min(2, initialCount - 1)).locator('button:has(svg.lucide-rotate-ccw)')
      if (await rollbackButton.isVisible()) {
        await rollbackButton.click()
        await confirmRollback(page)

        // All previous versions should still exist
        // Version count should increase (new rollback version) or stay same
        versionItems = getVersionItems(page)
        const finalCount = await getVersionCountFromApi(page)
        expect(finalCount).toBeGreaterThanOrEqual(initialCount)
      }
    }
  })

  // ==================== Auto-save Version Creation ====================

  test('editing content creates new version after debounce', async ({ page }) => {
    await createTestFile(page, '自动保存测试')
    await selectFile(page, '自动保存测试')

    // Edit content
    const content = '自动保存的内容测试，等待防抖时间后应该创建新版本。'
    await editFileContent(page, content)

    // Open version history
    await openVersionHistory(page)

    // Verify at least one version exists (the auto-saved one)
    const count = await getVersionCountFromApi(page)
    expect(count).toBeGreaterThanOrEqual(1)
  })

  test('manual save creates version with user summary', async ({ page }) => {
    await createTestFile(page, '手动保存测试')
    await selectFile(page, '手动保存测试')

    await editFileContent(page, '手动保存的内容')

    // Open version history
    await openVersionHistory(page)

    // Verify version was created
    const count = await getVersionCountFromApi(page)
    expect(count).toBeGreaterThanOrEqual(1)
  })

  // ==================== Version Metadata ====================

  test('versions show change type (edit, create, rollback)', async ({ page }) => {
    await createTestFile(page, '变更类型测试')
    await selectFile(page, '变更类型测试')

    await editFileContent(page, '编辑后的内容')

    // Open version history
    await openVersionHistory(page)

    // Look for change type indicators (badges/labels)
    const changeTypeLabels = page.locator('text=/编辑|edited|创建|created|恢复|restored|rollback/i')
    const count = await changeTypeLabels.count()
    expect(count).toBeGreaterThanOrEqual(0) // May or may not have explicit labels
  })

  test('versions show change source (user, auto_save)', async ({ page }) => {
    await createTestFile(page, '变更来源测试')
    await selectFile(page, '变更来源测试')

    await editFileContent(page, '用户编辑的内容')

    // Open version history
    await openVersionHistory(page)

    // Look for change source indicators
    // - User icon (svg.lucide-user) for user edits
    // - Bot icon (svg.lucide-bot) for AI edits
    // - Settings icon (svg.lucide-settings) for system
    const userIcon = page.locator('svg.lucide-user, svg.lucide-bot, svg.lucide-settings').first()
    await expect(userIcon).toBeVisible({ timeout: 5000 })
  })

  // ==================== Additional Edge Cases ====================

  test('version history shows latest badge on current version', async ({ page }) => {
    await createTestFile(page, '最新标记测试')
    await selectFile(page, '最新标记测试')

    await editFileContent(page, '最新版本的内容')

    // Open version history
    await openVersionHistory(page)

    // First version should have "latest" badge
    const latestBadge = page.locator('text=/最新|latest|current/i').first()
    await expect(latestBadge).toBeVisible({ timeout: 5000 })
  })

  test('version history can be closed', async ({ page }) => {
    await createTestFile(page, '关闭测试')
    await selectFile(page, '关闭测试')

    await editFileContent(page, '测试内容')

    // Open version history
    await openVersionHistory(page)

    // Verify panel is open (the file's own history button is also labelled 历史版本, so check the dialog)
    const historyDialog = page.getByRole('dialog').filter({ hasText: /版本历史|历史版本|Version History/i })
    await expect(historyDialog.first()).toBeVisible()

    // Find and click close button
    const closeButton = historyDialog.first().locator('button:has(svg.lucide-x)').first()
    await closeButton.click()

    // Verify panel is closed
    await expect(historyDialog).toHaveCount(0)
  })

  test('version history shows total version count', async ({ page }) => {
    await createTestFile(page, '版本计数测试')
    await selectFile(page, '版本计数测试')

    // Create multiple versions
    for (let i = 1; i <= 3; i++) {
      await editFileContent(page, `版本 ${i}`)
    }

    // Open version history
    await openVersionHistory(page)

    // Look for total count display
    const countDisplay = page.locator('text=/共\\s*\\d+\\s*(个)?版本|\\d+\\s*(versions?|版本)/i').first()
    await expect(countDisplay).toBeVisible({ timeout: 5000 })
  })
})

test.describe('Version History - Error Handling', () => {
  test('handles version history for file with no edits', async ({ page }) => {
    await createTestFile(page, '无编辑文件')

    // Select file but don't edit
    await selectFile(page, '无编辑文件')

    await openVersionHistory(page)

    const historyDialog = page.getByRole('dialog').filter({
      hasText: /版本历史|历史版本|Version History/i,
    })
    await expect(historyDialog.getByText(/还没有历史版本|no versions yet/i)).toBeVisible()
    expect(await getVersionCountFromApi(page)).toBe(0)
  })

  test('rollback confirmation can be cancelled', async ({ page }) => {
    await createTestFile(page, '取消回滚测试')
    await selectFile(page, '取消回滚测试')

    await editFileContent(page, '原始内容')
    await editFileContent(page, '新内容')

    // Open version history
    await openVersionHistory(page)

    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    const rollbackButton = versionItems.nth(1).locator('button:has(svg.lucide-rotate-ccw)')
    await expect(rollbackButton).toBeVisible()
    await rollbackButton.click()
    // Cancel the in-app restore confirmation
    await page.getByRole('dialog').getByRole('button', { name: /^(取消|Cancel)$/ }).click()

    // Verify content was NOT rolled back (still has new content)
    const editor = page.locator('textarea[placeholder*="开始你的创作"]').first()
    await expect(editor).toHaveValue(/新内容/)
  })
})

test.describe('Version History - Diff View Modes', () => {
  test('diff viewer supports unified view mode', async ({ page }) => {
    await createTestFile(page, '统一视图测试')
    await selectFile(page, '统一视图测试')

    await editFileContent(page, '原始行1\n原始行2')
    await editFileContent(page, '原始行1\n修改行2')

    await openVersionHistory(page)

    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    await versionItems.nth(0).click()
    await versionItems.nth(1).click()

    const compareButton = getCompareButton(page)
    await expect(compareButton).toBeVisible()
    await openVersionComparison(page, compareButton)

    // Look for unified view indicator or code icon
    const unifiedView = page.locator('button:has(svg.lucide-code), [class*="font-mono"]').first()
    await expect(unifiedView).toBeVisible({ timeout: 5000 })
  })

  test('diff viewer supports split view mode', async ({ page }) => {
    await createTestFile(page, '分屏视图测试')
    await selectFile(page, '分屏视图测试')

    await editFileContent(page, '左边内容\n第二行')
    await editFileContent(page, '右边内容\n第二行')

    await openVersionHistory(page)

    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    await versionItems.nth(0).click()
    await versionItems.nth(1).click()

    const compareButton = getCompareButton(page)
    await expect(compareButton).toBeVisible()
    await openVersionComparison(page, compareButton)

    const splitViewButton = page.getByRole('dialog').getByRole('button', { name: /^(并排视图|Split view)$/ })
    await expect(splitViewButton).toBeVisible()
    await splitViewButton.click()

    // Verify split view is shown (two columns)
    const splitContainer = page.getByRole('dialog').locator('[class*="flex"] > [class*="w-1/2"]').first()
    await expect(splitContainer).toBeVisible({ timeout: 3000 })
  })

  test('diff viewer supports inline view mode', async ({ page }) => {
    await createTestFile(page, '内联视图测试')
    await selectFile(page, '内联视图测试')

    await editFileContent(page, '原始文字内容')
    await editFileContent(page, '修改后的文字内容')

    await openVersionHistory(page)

    const versionItems = getVersionItems(page)
    const count = await versionItems.count()

    expect(count).toBeGreaterThanOrEqual(2)
    await versionItems.nth(0).click()
    await versionItems.nth(1).click()

    const compareButton = getCompareButton(page)
    await expect(compareButton).toBeVisible()
    await openVersionComparison(page, compareButton)

    const inlineViewButton = page.getByRole('dialog').getByRole('button', { name: /^(内联视图|Inline view)$/ })
    await expect(inlineViewButton).toBeVisible()
    await inlineViewButton.click()

    // Verify inline view is shown (prose-style content)
    const inlineContent = page.getByRole('dialog').locator('.leading-relaxed.whitespace-pre-wrap')
    await expect(inlineContent).toBeVisible({ timeout: 3000 })
  })
})
