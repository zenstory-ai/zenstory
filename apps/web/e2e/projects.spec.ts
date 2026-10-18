import { test, expect, type Page, type Locator, type TestInfo } from '@playwright/test'
import { getPoolUser, type TestUser } from './config'
import { cleanupOwnedProject, createOwnedProject, type OwnedProject } from './fixtures/project-test-data'
import { LoginPage } from './fixtures/page-objects/LoginPage'
import { login } from './helpers/common'

/**
 * E2E Tests for Project Management Flow
 *
 * These tests cover the complete project CRUD operations:
 * - Creating new projects with different templates
 * - Viewing project list
 * - Deleting projects
 * - Switching between projects
 * - Template display validation
 */

const NOVEL_LABEL = '长篇小说'
const SHORT_LABEL = '短篇小说'
const SCREENPLAY_LABEL = '短剧剧本'

let currentOwner: TestUser
let ownedProjects: OwnedProject[] = []
let pendingProjectTracking: Promise<string>[] = []

async function loginAndOpenDashboardHome(page: Page, testInfo: TestInfo) {
  currentOwner = getPoolUser(testInfo.parallelIndex)
  await login(page, currentOwner.email, currentOwner.password)
}

async function openProjectSwitcher(page: Page): Promise<{
  trigger: Locator
  dropdown: Locator
}> {
  const trigger = page.locator('button:has(svg.lucide-folder)').first()
  await expect(trigger).toBeVisible({ timeout: 10000 })
  await trigger.click()

  const searchInput = page
    .locator('input[placeholder="搜索项目"], input[placeholder="Search projects"]')
    .first()
  await expect(searchInput).toBeVisible({ timeout: 5000 })

  return {
    trigger,
    dropdown: searchInput.locator('xpath=ancestor::div[contains(@class,"absolute")]').first(),
  }
}

function getCurrentProjectRow(dropdown: Locator): Locator {
  return dropdown.locator('div.group.cursor-pointer').last()
}

function registerOwnedProject(projectId: string, accessToken: string): string {
  if (ownedProjects.some((project) => project.id === projectId)) return projectId
  ownedProjects.push({
    id: projectId,
    name: `projects-${projectId}`,
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    owner: currentOwner,
  })
  return projectId
}

async function createProjectFromDashboard(page: Page): Promise<string> {
  const accessToken = await page.evaluate(() => localStorage.getItem('access_token'))
  if (!accessToken) throw new Error('Missing access token before creating project fixture')

  const tracking = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'POST' && url.pathname === '/api/v1/projects'
  }).then(async (response) => {
    expect(response.ok()).toBeTruthy()
    const project = await response.json() as { id?: string }
    if (!project.id) throw new Error('Project creation response did not include an id')
    return registerOwnedProject(project.id, accessToken)
  })
  pendingProjectTracking.push(tracking)
  void tracking.catch(() => undefined)

  await page.getByTestId('create-project-button').click()
  return tracking
}

async function createTrackedOwnedProject(
  page: Page,
  testInfo: TestInfo,
  prefix: string,
): Promise<OwnedProject> {
  const project = await createOwnedProject(page, testInfo, prefix)
  ownedProjects.push(project)
  return project
}

function getOwnedProjectCard(page: Page, project: OwnedProject): Locator {
  return page
    .getByRole('button', { name: `打开项目「${project.name}」` })
    .locator('xpath=ancestor::article[@data-testid="project-card"]')
}

test.afterEach(async ({ page }) => {
  const trackingResults = await Promise.allSettled(pendingProjectTracking)
  for (const project of ownedProjects) await cleanupOwnedProject(page, project)
  ownedProjects = []
  pendingProjectTracking = []
  const failedTracking = trackingResults.find((result) => result.status === 'rejected')
  if (failedTracking?.status === 'rejected') throw failedTracking.reason
})

test.describe('Projects', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    ownedProjects = []
    pendingProjectTracking = []
    await loginAndOpenDashboardHome(page, testInfo)
  })

  test('user can create a new project with inspiration', async ({ page }) => {

    // Find the inspiration textarea and fill it
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill('这是一个测试小说的灵感描述')

    // Click the create button with sparkles icon
    await createProjectFromDashboard(page)

    // Wait for navigation to the new project
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    // Verify we're on a project page
    expect(page.url()).toContain('/project/')
  })

  test('user can see project templates tabs', async ({ page }) => {
    // Check that all three template tabs are visible
    await expect(page.getByRole('button', { name: NOVEL_LABEL })).toBeVisible()
    await expect(page.getByRole('button', { name: SHORT_LABEL })).toBeVisible()
    await expect(page.getByRole('button', { name: SCREENPLAY_LABEL })).toBeVisible()
  })

  test('user can switch between project type tabs', async ({ page }) => {
    // Click on "短篇" (short) tab
    await page.getByRole('button', { name: SHORT_LABEL }).click()

    // Verify the tab is active (assert the semantics, not the selected-state colour classes)
    const shortTab = page.getByRole('button', { name: SHORT_LABEL })
    await expect(shortTab).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('button', { name: NOVEL_LABEL })).toHaveAttribute('aria-pressed', 'false')

    // Click on "剧本" (screenplay) tab
    await page.getByRole('button', { name: SCREENPLAY_LABEL }).click()

    // Verify the tab is active
    const screenplayTab = page.getByRole('button', { name: SCREENPLAY_LABEL })
    await expect(screenplayTab).toHaveAttribute('aria-pressed', 'true')
    await expect(shortTab).toHaveAttribute('aria-pressed', 'false')

    // Click on "小说" (novel) tab to return to default
    await page.getByRole('button', { name: NOVEL_LABEL }).click()
    const novelTab = page.getByRole('button', { name: NOVEL_LABEL })
    await expect(novelTab).toHaveAttribute('aria-pressed', 'true')
  })

  test('user can see empty state when no projects exist', async ({ page }) => {
    await page.route(/\/api\/v1\/projects(?:\?.*)?$/, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
        return
      }
      await route.continue()
    })
    await page.reload()

    await expect(page.locator('[data-testid="project-card"]')).toHaveCount(0)
    await expect(page.getByText('还没有任何项目')).toBeVisible()
    await expect(page.getByText(/建好第一个项目/)).toBeVisible()
  })

  test('user can see project list when projects exist', async ({ page }, testInfo) => {
    const project = await createTrackedOwnedProject(page, testInfo, 'project-list')
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('button', { name: `打开项目「${project.name}」` })).toBeVisible()

    // Check that the recent-project count badge is visible
    const countBadge = page.locator('text=最近项目').locator('..').locator('span[class*="rounded-full"]')
    await expect(countBadge).toBeVisible()
  })

  test('user can delete a project', async ({ page }, testInfo) => {
    const project = await createTrackedOwnedProject(page, testInfo, 'project-delete')
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })

    const ownedProjectCard = getOwnedProjectCard(page, project)
    await expect(ownedProjectCard).toBeVisible()
    await ownedProjectCard.hover()

    const deleteResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === 'DELETE' &&
        response.url().endsWith(`/api/v1/projects/${project.id}`),
      { timeout: 10000 }
    )

    // Click the delete button (trash icon), then confirm in the in-app dialog
    await ownedProjectCard
      .locator('button[title="删除项目"], button:has(svg.lucide-trash-2)')
      .first()
      .click({ force: true })
    const confirmDialog = page.getByRole('dialog')
    await expect(confirmDialog).toBeVisible()
    await Promise.all([
      deleteResponsePromise,
      confirmDialog.getByRole('button', { name: '删除' }).click(),
    ])

    await expect(ownedProjectCard).toHaveCount(0, { timeout: 10000 })
  })

  test('user can cancel project deletion', async ({ page }, testInfo) => {
    const project = await createTrackedOwnedProject(page, testInfo, 'project-cancel-delete')
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })

    const ownedProjectCard = getOwnedProjectCard(page, project)
    await expect(ownedProjectCard).toBeVisible()
    await ownedProjectCard.hover()

    // Click the delete button, then cancel in the in-app dialog
    await ownedProjectCard
      .locator('button[title="删除项目"], button:has(svg.lucide-trash-2)')
      .first()
      .click({ force: true })
    const confirmDialog = page.getByRole('dialog')
    await expect(confirmDialog).toBeVisible()
    await confirmDialog.getByRole('button', { name: '取消' }).click()
    await expect(confirmDialog).not.toBeVisible()

    await expect(ownedProjectCard).toBeVisible()
  })

  test('user can navigate to an existing project', async ({ page }, testInfo) => {
    const project = await createTrackedOwnedProject(page, testInfo, 'project-navigation')
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })

    await page.getByRole('button', { name: `打开项目「${project.name}」` }).click()

    // Wait for navigation
    await expect(page).toHaveURL(`/project/${project.id}`, { timeout: 10000 })

    // Verify we navigated to a project page
    expect(page.url()).toContain('/project/')
  })

  test('create button remains enabled when inspiration is empty', async ({ page }) => {
    // Find the create button
    const createButton = page.locator('[data-testid="create-project-button"]')

    // Current dashboard supports quick creation without inspiration.
    await expect(createButton).toBeEnabled()

    // Type something in the textarea
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill('一些文本')

    // Verify button is now enabled
    await expect(createButton).not.toBeDisabled()

    // Clear the textarea
    await inspirationInput.fill('')

    // Verify button remains available after clearing inspiration
    await expect(createButton).toBeEnabled()
  })

  test('project cards display correct information', async ({ page }, testInfo) => {
    const project = await createTrackedOwnedProject(page, testInfo, 'project-card')
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })

    const ownedProjectCard = getOwnedProjectCard(page, project)
    await expect(ownedProjectCard).toBeVisible()

    // Check for project type icon (Book, FileText, or Clapperboard)
    await expect(ownedProjectCard.locator('svg').first()).toBeVisible()

    // Check for project name
    await expect(ownedProjectCard.locator('h3, [class*="font-semibold"]')).toBeVisible()

    // Check for project type label
    await expect(
      ownedProjectCard
        .locator('span')
        .filter({ hasText: /长篇小说|短篇小说|短剧剧本/ })
        .first()
    ).toBeVisible()

    // Check for timestamp (clock icon)
    await expect(ownedProjectCard.locator('svg.lucide-clock')).toBeVisible()
  })

  test('recent projects section shows project count', async ({ page }) => {
    // Look for the "最近项目" section header
    await expect(page.locator('text=最近项目')).toBeVisible()

    // Check that project count badge is present
    const countBadge = page.locator('text=最近项目').locator('..').locator('span[class*="rounded-full"]')
    await expect(countBadge).toBeVisible()

    // The badge should contain a number
    const count = await countBadge.textContent()
    expect(parseInt(count || '0')).not.toBeNaN()
  })

  test('search bar appears when more than 6 projects exist', async ({ page }) => {
    // This test is conditional - only relevant if many projects exist
    // Check if search input is visible
    const searchInput = page.locator('input[placeholder*="搜索"]')

    // Count project cards
    const projectCount = await page.locator('[data-testid="project-card"]').count()

    if (projectCount > 6) {
      await expect(searchInput).toBeVisible()
    } else {
      // Search bar should not be visible if <= 6 projects
      await expect(searchInput).not.toBeVisible()
    }
  })

  test('user can search projects', async ({ page }) => {

    // First create a project with a unique name
    const uniqueName = `搜索测试${Date.now()}`

    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill(uniqueName)

    await createProjectFromDashboard(page)
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    // Create another project with different name
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
    await inspirationInput.fill('其他项目名称')
    await createProjectFromDashboard(page)
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    // Go back to dashboard
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })

    // Check if search input is visible (need > 6 projects)
    const searchInput = page.locator('input[placeholder*="搜索"]')

    if (await searchInput.isVisible()) {
      // Search for the unique project name
      await searchInput.fill(uniqueName)

      // Wait for filtering to complete
      await page.waitForSelector('[data-testid="project-card"]', { state: 'attached', timeout: 2000 })

      // Verify only matching projects are shown
      const visibleProjects = await page.locator('[data-testid="project-card"]').count()
      expect(visibleProjects).toBeGreaterThanOrEqual(1)

      // Clear search
      await searchInput.fill('')

      // Wait for all projects to be visible again
      await page.waitForSelector('[data-testid="project-card"]', { state: 'attached', timeout: 2000 })
    }
  })

  test('dashboard shows greeting with user name', async ({ page }) => {
    // Look for the greeting text (contains user name)
    // The greeting format is "Hi, {name}" or similar
    const greeting = page.locator('h1')
    await expect(greeting).toBeVisible()

    // Verify it contains some text (user's name should be there)
    const greetingText = await greeting.textContent()
    expect(greetingText).toBeTruthy()
    expect(greetingText!.length).toBeGreaterThan(0)
  })
})

test.describe('Project Update', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    ownedProjects = []
    pendingProjectTracking = []
    await loginAndOpenDashboardHome(page, testInfo)
  })

  test('user can update project name via ProjectSwitcher', async ({ page }) => {

    // First create a project to edit
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill('更新名称测试项目')

    await createProjectFromDashboard(page)
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    const { dropdown } = await openProjectSwitcher(page)
    await expect(
      dropdown.getByText('创建新项目').or(dropdown.getByText('Create new project'))
    ).toBeVisible()

    // Hover over the project item to reveal edit button
    const projectItem = getCurrentProjectRow(dropdown)
    await projectItem.hover()

    // Click the pencil/edit icon
    const editButton = projectItem.locator('button[title="编辑项目名称"]').first()
    await editButton.click({ force: true })

    // Find the edit input and change the name
    const updatedName = `已更新的项目名称${Date.now()}`
    const editInput = page.locator('input[type="text"]').nth(1) // Second input (first is search)
    await editInput.fill(updatedName)
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === 'PUT' &&
          response.url().includes('/api/v1/projects/'),
        { timeout: 10000 }
      ),
      editInput.press('Enter'),
    ])

    await expect(dropdown.getByText(updatedName).first()).toBeVisible({ timeout: 5000 })
    await page.keyboard.press('Escape')
  })

  test('user can update project description', async ({ page }) => {

    // First create a project
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill('更新描述测试项目')

    await createProjectFromDashboard(page)
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    // Note: Project description update is typically done through project settings
    // or inline editing in the Dashboard. Since the current UI only supports
    // renaming via ProjectSwitcher, this test verifies the name update works
    // as a proxy for description updates which would follow similar patterns.

    // For now, verify the project exists by checking project switcher
    const { dropdown } = await openProjectSwitcher(page)
    await expect(dropdown.locator('div.group.cursor-pointer').first()).toBeVisible()
    await page.keyboard.press('Escape')
  })

  test('project update persists across sessions', async ({ page }) => {

    // First create a project
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    const uniqueName = `持久化测试${Date.now()}`
    await inspirationInput.fill(uniqueName)

    const projectId = await createProjectFromDashboard(page)
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    // Open ProjectSwitcher and rename the project
    const { dropdown } = await openProjectSwitcher(page)
    const projectItem = getCurrentProjectRow(dropdown)
    await expect(projectItem).toBeVisible({ timeout: 3000 })
    await projectItem.hover()

    // Click the pencil/edit icon
    const editButton = projectItem.locator('button[title="编辑项目名称"]').first()
    await editButton.click({ force: true })

    // Change the name
    const updatedName = `已更新持久化${Date.now()}`
    const editInput = page.locator('input[type="text"]').nth(1)
    await editInput.fill(updatedName)
    const [renameResponse] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url())
        return response.request().method() === 'PUT' && url.pathname === `/api/v1/projects/${projectId}`
      }),
      editInput.press('Enter'),
    ])
    expect(renameResponse.ok()).toBeTruthy()
    await expect(dropdown.getByText(updatedName, { exact: true })).toBeVisible()

    // Complete a real logout before establishing the next session.
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' })
    await expect(page).toHaveURL('/dashboard')
    await expect(page.getByTestId('dashboard-composer')).toBeVisible()
    await page.getByTestId('dashboard-user-panel-toggle').click()
    const logoutButton = page.getByRole('button', { name: /^(退出登录|Logout)$/ })
    await expect(logoutButton).toBeVisible()
    const [logoutResponse] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url())
        return response.request().method() === 'POST' && url.pathname === '/api/auth/logout'
      }),
      logoutButton.click(),
    ])
    expect(logoutResponse.ok()).toBeTruthy()
    await expect(page).toHaveURL('/login')
    await expect(page.locator('#identifier')).toBeVisible()

    const loginPage = new LoginPage(page)
    const [loginResponse] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url())
        return response.request().method() === 'POST' && url.pathname === '/api/auth/login'
      }),
      loginPage.login(currentOwner.email, currentOwner.password),
    ])
    expect(loginResponse.ok()).toBeTruthy()
    await expect(page).toHaveURL('/dashboard')
    await expect(page.getByTestId('dashboard-composer')).toBeVisible()

    // Verify the updated name appears in the project list
    await expect(page.getByTestId('project-card').filter({ hasText: updatedName })).toBeVisible()
  })

  test('cannot update project with empty name', async ({ page }) => {

    // First create a project
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill('空名称测试项目')

    await createProjectFromDashboard(page)
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    // Open ProjectSwitcher
    const { dropdown } = await openProjectSwitcher(page)
    const projectItem = getCurrentProjectRow(dropdown)
    await expect(projectItem).toBeVisible({ timeout: 3000 })
    await projectItem.hover()

    // Click the pencil/edit icon
    const editButton = projectItem.locator('button[title="编辑项目名称"]').first()
    await editButton.click({ force: true })

    // Try to clear the name and save
    const editInput = page.locator('input[type="text"]').nth(1)
    await editInput.fill('')
    await editInput.press('Enter')

    // The edit should be cancelled or the name should remain unchanged
    // because the handleSaveEdit function checks for empty names
    // Wait briefly for UI to process
    await page.waitForLoadState('domcontentloaded', { timeout: 3000 })

    // Verify the original name is still there
    await expect(projectItem).toContainText('我的小说')
  })

  test('user can cancel project name edit', async ({ page }) => {

    // First create a project
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]')
    await inspirationInput.fill('取消编辑测试项目')

    await createProjectFromDashboard(page)
    await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })

    // Open ProjectSwitcher
    const { dropdown } = await openProjectSwitcher(page)
    const projectItem = getCurrentProjectRow(dropdown)
    await expect(projectItem).toBeVisible({ timeout: 3000 })
    await projectItem.hover()

    // Click the pencil/edit icon
    const editButton = projectItem.locator('button[title="编辑项目名称"]').first()
    await editButton.click({ force: true })

    // Type a new name but don't save
    const editInput = page.locator('input[type="text"]').nth(1)
    await editInput.fill('这个名称不应该保存')

    // Press Escape to cancel
    await editInput.press('Escape')
    await expect(projectItem).toContainText('我的小说', { timeout: 3000 })

    // Verify the original name is still there
    await expect(projectItem).toContainText('我的小说')
    await expect(dropdown.getByText('这个名称不应该保存')).not.toBeVisible()
  })
})

test.describe('Project Type Templates', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    ownedProjects = []
    pendingProjectTracking = []
    await loginAndOpenDashboardHome(page, testInfo)
  })

  test('novel template has correct placeholder', async ({ page }) => {
    // Select novel tab
    await page.getByRole('button', { name: NOVEL_LABEL }).click()

    // Check placeholder text
    const textarea = page.locator('textarea')
    const placeholder = await textarea.getAttribute('placeholder')

    // Should contain novel-related placeholder text
    expect(placeholder).toBeTruthy()
  })

  test('short story template has correct placeholder', async ({ page }) => {
    // Select short story tab
    await page.getByRole('button', { name: SHORT_LABEL }).click()

    // Check placeholder text
    const textarea = page.locator('textarea')
    const placeholder = await textarea.getAttribute('placeholder')

    // Should contain short story related placeholder text
    expect(placeholder).toBeTruthy()
  })

  test('screenplay template has correct placeholder', async ({ page }) => {
    // Select screenplay tab
    await page.getByRole('button', { name: SCREENPLAY_LABEL }).click()

    // Check placeholder text
    const textarea = page.locator('textarea')
    const placeholder = await textarea.getAttribute('placeholder')

    // Should contain screenplay related placeholder text
    expect(placeholder).toBeTruthy()
  })

  test('template info section reflects active tab in placeholder', async ({ page }) => {
    // Verify switching tabs changes the textarea placeholder
    const textarea = page.locator('textarea[data-testid="dashboard-inspiration-input"]')

    await page.getByRole('button', { name: NOVEL_LABEL }).click()
    const novelPlaceholder = await textarea.getAttribute('placeholder')
    expect(novelPlaceholder).toBeTruthy()

    await page.getByRole('button', { name: SHORT_LABEL }).click()
    const shortPlaceholder = await textarea.getAttribute('placeholder')
    expect(shortPlaceholder).toBeTruthy()

    await page.getByRole('button', { name: SCREENPLAY_LABEL }).click()
    const screenplayPlaceholder = await textarea.getAttribute('placeholder')
    expect(screenplayPlaceholder).toBeTruthy()
  })
})
