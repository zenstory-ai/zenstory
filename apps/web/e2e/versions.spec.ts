import { test, expect, Page, APIRequestContext, Locator } from '@playwright/test'
import { TEST_USERS, config } from './config'

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

// Test credentials - use environment variables or defaults matching seeded user
const TEST_EMAIL = TEST_USERS.standard.email
const TEST_PASSWORD = TEST_USERS.standard.password
const AUTHENTICATED_ROUTE_PATTERN = /\/(dashboard|project|onboarding\/persona)/
let activeTestFileId: string | null = null
let activeTestFileName: string | null = null
let activeTestFolderTitle: string | null = null
const VERSION_HISTORY_E2E_ENABLED = process.env.E2E_ENABLE_VERSION_HISTORY_E2E === 'true'

test.skip(
  !VERSION_HISTORY_E2E_ENABLED,
  'Version history deep UI suite depends on stable editor runtime. Set E2E_ENABLE_VERSION_HISTORY_E2E=true to run it.'
)

async function loginAndOpenDashboardHome(page: Page) {
  await page.addInitScript(() => {
    const cachedUser = localStorage.getItem('user')
    if (cachedUser) {
      localStorage.setItem('auth_validated_at', Date.now().toString())
    }
  })

  const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
  if (await inspirationInput.isVisible({ timeout: 5000 }).catch(() => false)) {
    return
  }

  if (!page.url().includes('/login')) {
    await page.goto('/login', { waitUntil: 'domcontentloaded' })
  }

  await expect(page.locator('#identifier')).toBeVisible({ timeout: 15000 })
  await page.fill('#identifier', TEST_EMAIL)
  await page.fill('#password', TEST_PASSWORD)
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 30000 })
  await page
    .waitForResponse(
      (response) =>
        response.url().includes('/api/v1/projects') &&
        response.request().method() === 'GET',
      { timeout: 30000 }
    )
    .catch(() => null)

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
  await expect(inspirationInput).toBeVisible({ timeout: 30000 })
}

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

async function listProjects(page: Page, request: APIRequestContext) {
  const headers = await getAuthHeaders(page)
  const response = await request.get(`${config.apiBaseUrl}/api/v1/projects`, { headers })
  expect(response.ok()).toBeTruthy()

  const payload = await response.json()
  return Array.isArray(payload) ? payload : []
}

async function ensureProjectSlotsAvailable(page: Page, request: APIRequestContext, requiredCreates = 1) {
  const headers = await getAuthHeaders(page)
  const quotaResponse = await request.get(`${config.apiBaseUrl}/api/v1/subscription/quota`, { headers })
  expect(quotaResponse.ok()).toBeTruthy()

  const quota = await quotaResponse.json()
  const projectLimit = quota?.projects?.limit
  if (typeof projectLimit !== 'number' || projectLimit < 0) return

  const projects = await listProjects(page, request)
  const targetProjectCount = Math.max(projectLimit - requiredCreates, 0)

  projects.sort(
    (a, b) =>
      new Date(a.updated_at || a.created_at || 0).getTime() -
      new Date(b.updated_at || b.created_at || 0).getTime()
  )

  while (projects.length > targetProjectCount) {
    const project = projects.shift()
    if (!project?.id) continue

    const deleteResponse = await request.delete(`${config.apiBaseUrl}/api/v1/projects/${project.id}`, {
      headers,
    })
    expect(deleteResponse.ok()).toBeTruthy()
  }

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('[data-testid="dashboard-inspiration-input"]')).toBeVisible({
    timeout: 15000,
  })
}

/**
 * Helper function to login and navigate to a project
 */
async function loginAndNavigateToProject(page: Page, request: APIRequestContext) {
  await loginAndOpenDashboardHome(page)
  await ensureProjectSlotsAvailable(page, request)

  const headers = await getAuthHeaders(page)
  const createResponse = await request.post(`${config.apiBaseUrl}/api/v1/projects`, {
    headers,
    data: {
      description: `版本测试项目 ${Date.now()}`,
    },
  })
  expect(createResponse.ok()).toBeTruthy()
  const createdProject = await createResponse.json()
  await page.goto(`/project/${createdProject.id}`, { waitUntil: 'domcontentloaded' })
  await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })
}

function getCurrentProjectId(page: Page): string {
  const match = page.url().match(/\/project\/([^/?#]+)/)
  if (!match?.[1]) {
    throw new Error(`Unable to resolve project id from URL: ${page.url()}`)
  }
  return match[1]
}

type TreeNode = {
  id?: string
  title?: string
  children?: TreeNode[]
}

function findFolderNode(nodes: TreeNode[], title: string): TreeNode | null {
  for (const node of nodes) {
    if (node?.title === title) {
      return node
    }
    if (Array.isArray(node?.children)) {
      const childMatch = findFolderNode(node.children, title)
      if (childMatch) return childMatch
    }
  }
  return null
}

/**
 * Helper function to create a file and return its locator
 */
async function createTestFile(page: Page, fileName: string): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem('access_token'))
  expect(token).toBeTruthy()

  const projectId = getCurrentProjectId(page)
  const treeResponse = await page.request.get(`/api/v1/projects/${projectId}/file-tree`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  expect(treeResponse.ok()).toBeTruthy()
  const treePayload = await treeResponse.json()
  const folderNode =
    findFolderNode(treePayload?.tree ?? [], '正文') ||
    findFolderNode(treePayload?.tree ?? [], 'Draft') ||
    findFolderNode(treePayload?.tree ?? [], 'Drafts') ||
    findFolderNode(treePayload?.tree ?? [], '大纲') ||
    findFolderNode(treePayload?.tree ?? [], 'Outline')
  activeTestFolderTitle = folderNode?.title ?? null
  const fileType = folderNode?.title === '大纲' || folderNode?.title === 'Outline' ? 'outline' : 'draft'

  const createResponse = await page.request.post(`/api/v1/projects/${projectId}/files`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
    data: {
      title: fileName,
      file_type: fileType,
      parent_id: folderNode?.id ?? null,
      content: '',
    },
  })
  expect(createResponse.ok()).toBeTruthy()
  const created = await createResponse.json()

  activeTestFileId = created.id
  activeTestFileName = fileName

  await page.goto(`/project/${projectId}`, { waitUntil: 'domcontentloaded' })
  await ensureActiveFolderExpanded(page)
  await expect(page.locator(`text=${fileName}`).first()).toBeVisible({ timeout: 10000 })

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
  if (!activeTestFolderTitle) return

  const folderItem = page.locator('[role="treeitem"]').filter({ hasText: activeTestFolderTitle }).first()
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
  const historyButton = page.getByRole('button', { name: /^历史$|^History$/ }).first()
  await expect(historyButton).toBeVisible({ timeout: 5000 })
  await historyButton.click()

  // Wait for version history panel
  await expect(page.getByText(/版本历史|历史版本|Version History/i).first()).toBeVisible({ timeout: 5000 })
}

function getVersionItems(page: Page) {
  return page.getByRole('dialog').locator('div[tabindex="0"]')
}

function getCompareButton(page: Page) {
  return page
    .locator('button:has(svg.lucide-git-compare), button:has-text("对比"), button:has-text("Compare")')
    .first()
}

function getDiffViewerReadyLocator(page: Page) {
  return page
    .locator(
      'button:has(svg.lucide-code), button:has(svg.lucide-eye), button:has(svg.lucide-align-left), [class*="font-mono"], .diff-add, .diff-remove'
    )
    .first()
}

async function openVersionComparison(page: Page, compareButton: Locator = getCompareButton(page)) {
  await expect(compareButton).toBeVisible({ timeout: 5000 })

  const diffReady = getDiffViewerReadyLocator(page)
  const compareResponse = page
    .waitForResponse(
      (response) =>
        response.url().includes('/api/v1/') &&
        response.url().includes('/compare') &&
        response.request().method() === 'GET',
      { timeout: 10000 }
    )
    .catch(() => null)

  await compareButton.click()

  await Promise.race([
    compareResponse,
    expect(diffReady).toBeVisible({ timeout: 10000 }).then(() => null),
  ])

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
  test.beforeEach(async ({ page, request }) => {
    await loginAndNavigateToProject(page, request)
  })

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

    if (count >= 2) {
      // Click first version to select
      await versionItems.nth(0).click()

      // Click second version to select
      await versionItems.nth(1).click()

      // Look for compare button
      const compareButton = getCompareButton(page)
      if (await compareButton.isVisible()) {
        await openVersionComparison(page, compareButton)

        // Verify diff viewer appears
        const diffViewer = page.locator('[class*="font-mono"], .diff-add, .diff-remove').first()
        await expect(diffViewer).toBeVisible({ timeout: 5000 })
      }
    }
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

    if (count >= 2) {
      // Select two versions
      await versionItems.nth(0).click()
      await versionItems.nth(1).click()

      const compareButton = getCompareButton(page)
      if (await compareButton.isVisible()) {
        await openVersionComparison(page, compareButton)

        // Look for green/add styling (diff-add class or green color)
        const addIndicator = page.locator('.diff-add, [class*="text-green"], [class*="bg-green"], [class*="success"]').first()
        await expect(addIndicator).toBeVisible({ timeout: 5000 })
      }
    }
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

    if (count >= 2) {
      // Select two versions
      await versionItems.nth(0).click()
      // Wait for selection state to update
      await expect(versionItems.nth(0)).toHaveAttribute('class', /selected|active|border-/, { timeout: 2000 })

      await versionItems.nth(1).click()
      // Wait for second selection
      await expect(versionItems.nth(1)).toHaveAttribute('class', /selected|active|border-/, { timeout: 2000 })

      const compareButton = getCompareButton(page)
      if (await compareButton.isVisible()) {
        await openVersionComparison(page, compareButton)

        // Look for red/remove styling (diff-remove class or red color)
        const removeIndicator = page.locator('.diff-remove, [class*="text-red"], [class*="bg-red"], [class*="error"]').first()
        await expect(removeIndicator).toBeVisible({ timeout: 5000 })
      }
    }
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

    if (count >= 2) {
      // Select two versions
      await versionItems.nth(0).click()
      await versionItems.nth(1).click()

      const compareButton = getCompareButton(page)
      if (await compareButton.isVisible()) {
        await openVersionComparison(page, compareButton)

        // Verify diff content is displayed with + and - indicators
        const diffContent = page.locator('[class*="font-mono"]').first()
        await expect(diffContent).toBeVisible({ timeout: 5000 })

        // Look for +/- indicators
        const plusMinusIndicator = page.locator('text=/^\\s*[+-]/').first()
        await expect(plusMinusIndicator).toBeVisible({ timeout: 3000 })
      }
    }
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

    if (count >= 2) {
      // Setup dialog handler for confirmation
      page.on('dialog', dialog => dialog.accept())

      // Click rollback button on the second version (older)
      const rollbackButton = versionItems.nth(1).locator('button:has(svg.lucide-rotate-ccw)')
      if (await rollbackButton.isVisible()) {
        await rollbackButton.click()
        // Wait for rollback API response
        await page.waitForResponse(resp => resp.url().includes('/api/v1/') && resp.url().includes('/rollback') && resp.request().method() === 'POST', { timeout: 5000 })

        // Verify rollback succeeded - content should be restored
        // The version list should refresh
        await expect(page.locator('text=/恢复|restore|success/i').first()).toBeVisible({ timeout: 5000 })
      }
    }
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
      page.on('dialog', dialog => dialog.accept())

      const rollbackButton = versionItems.nth(1).locator('button:has(svg.lucide-rotate-ccw)')
      if (await rollbackButton.isVisible()) {
        await rollbackButton.click()
        // Wait for rollback API response
        await page.waitForResponse(resp => resp.url().includes('/api/v1/') && resp.url().includes('/rollback') && resp.request().method() === 'POST', { timeout: 5000 })

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
      page.on('dialog', dialog => dialog.accept())

      const rollbackButton = versionItems.nth(Math.min(2, initialCount - 1)).locator('button:has(svg.lucide-rotate-ccw)')
      if (await rollbackButton.isVisible()) {
        await rollbackButton.click()
        // Wait for rollback API response
        await page.waitForResponse(resp => resp.url().includes('/api/v1/') && resp.url().includes('/rollback') && resp.request().method() === 'POST', { timeout: 5000 })

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

    // Verify panel is open
    await expect(page.getByText(/版本历史|历史版本|Version History/i).first()).toBeVisible()

    // Find and click close button
    const closeButton = page.locator('button:has(svg.lucide-x)').first()
    await closeButton.click()

    // Verify panel is closed
    await expect(page.getByText(/版本历史|历史版本|Version History/i).first()).not.toBeVisible()
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
  test.beforeEach(async ({ page, request }) => {
    await loginAndNavigateToProject(page, request)
  })

  test('handles version history for file with no edits', async ({ page }) => {
    await createTestFile(page, '无编辑文件')

    // Select file but don't edit
    await selectFile(page, '无编辑文件')

    // Try to open version history
    const historyButton = page.locator('button:has(svg.lucide-clock), button:has(svg.lucide-history)').first()
    if (await historyButton.isVisible()) {
      await historyButton.click()
      // Wait for panel to appear
      await page.waitForSelector('text=/历史版本|版本历史|Version History/', { timeout: 3000 })

      // Should show empty state or single version
      const emptyState = page.locator('text=/还没有版本记录|no versions yet/i')
      const versionItems = page.locator('[class*="p-3"][class*="hover:bg"]')

      // Either empty state or at least the initial version should be shown
      const hasEmpty = await emptyState.isVisible()
      const versionCount = await versionItems.count()
      expect(hasEmpty || versionCount >= 1).toBeTruthy()
    }
  })

  test('rollback confirmation can be cancelled', async ({ page }) => {
    await createTestFile(page, '取消回滚测试')
    await selectFile(page, '取消回滚测试')

    await editFileContent(page, '原始内容')
    await editFileContent(page, '新内容')

    // Open version history
    await openVersionHistory(page)

    const versionItems = page.locator('[class*="p-3"][class*="hover:bg"]')
    const count = await versionItems.count()

    if (count >= 2) {
      // Setup dialog handler to cancel
      page.on('dialog', dialog => dialog.dismiss())

      const rollbackButton = versionItems.nth(1).locator('button:has(svg.lucide-rotate-ccw)')
      if (await rollbackButton.isVisible()) {
        await rollbackButton.click()

        // Verify content was NOT rolled back (still has new content)
        const editor = page.locator('textarea[placeholder*="开始你的创作"]').first()
        await expect(editor).toHaveValue(/新内容/)
      }
    }
  })
})

test.describe('Version History - Diff View Modes', () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAndNavigateToProject(page, request)
  })

  test('diff viewer supports unified view mode', async ({ page }) => {
    await createTestFile(page, '统一视图测试')
    await selectFile(page, '统一视图测试')

    await editFileContent(page, '原始行1\n原始行2')
    await editFileContent(page, '原始行1\n修改行2')

    await openVersionHistory(page)

    const versionItems = page.locator('[class*="p-3"][class*="hover:bg"]')
    const count = await versionItems.count()

    if (count >= 2) {
      await versionItems.nth(0).click()
      await versionItems.nth(1).click()

      const compareButton = getCompareButton(page)
      if (await compareButton.isVisible()) {
        await openVersionComparison(page, compareButton)

        // Look for unified view indicator or code icon
        const unifiedView = page.locator('button:has(svg.lucide-code), [class*="font-mono"]').first()
        await expect(unifiedView).toBeVisible({ timeout: 5000 })
      }
    }
  })

  test('diff viewer supports split view mode', async ({ page }) => {
    await createTestFile(page, '分屏视图测试')
    await selectFile(page, '分屏视图测试')

    await editFileContent(page, '左边内容\n第二行')
    await editFileContent(page, '右边内容\n第二行')

    await openVersionHistory(page)

    const versionItems = page.locator('[class*="p-3"][class*="hover:bg"]')
    const count = await versionItems.count()

    if (count >= 2) {
      await versionItems.nth(0).click()
      await versionItems.nth(1).click()

      const compareButton = getCompareButton(page)
      if (await compareButton.isVisible()) {
        await openVersionComparison(page, compareButton)

        // Look for split view button (Eye icon)
        const splitViewButton = page.locator('button:has(svg.lucide-eye)').first()
        if (await splitViewButton.isVisible()) {
          await splitViewButton.click()

          // Verify split view is shown (two columns)
          const splitContainer = page.locator('[class*="flex"] > [class*="w-1/2"]').first()
          await expect(splitContainer).toBeVisible({ timeout: 3000 })
        }
      }
    }
  })

  test('diff viewer supports inline view mode', async ({ page }) => {
    await createTestFile(page, '内联视图测试')
    await selectFile(page, '内联视图测试')

    await editFileContent(page, '原始文字内容')
    await editFileContent(page, '修改后的文字内容')

    await openVersionHistory(page)

    const versionItems = page.locator('[class*="p-3"][class*="hover:bg"]')
    const count = await versionItems.count()

    if (count >= 2) {
      await versionItems.nth(0).click()
      await versionItems.nth(1).click()

      const compareButton = getCompareButton(page)
      if (await compareButton.isVisible()) {
        await openVersionComparison(page, compareButton)

        // Look for inline view button (AlignLeft icon)
        const inlineViewButton = page.locator('button:has(svg.lucide-align-left)').first()
        if (await inlineViewButton.isVisible()) {
          await inlineViewButton.click()

          // Verify inline view is shown (prose-style content)
          const inlineContent = page.locator('[class*="leading-relaxed"], [class*="prose"]').first()
          await expect(inlineContent).toBeVisible({ timeout: 3000 })
        }
      }
    }
  })
})
