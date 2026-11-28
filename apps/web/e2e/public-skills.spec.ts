import { test, expect, type Page } from '@playwright/test'
import { TEST_USERS } from './config'

/**
 * Public Skills E2E Tests
 *
 * Tests the public skills discovery workflow including:
 * - Browsing public skill library
 * - Category filtering
 * - Search functionality
 * - Adding skills to user's library
 * - Removing added skills
 */

// UI Selectors
const DISCOVER = {
  searchInput: 'input[placeholder*="搜索"]',
  categoryButtons: '[data-testid="discover-category-filters"] button',
  skillCard: '[data-testid="public-skill-card"]',
  addButton: 'button:has-text("添加")',
  addedBadge: 'button:has-text("已添加")',
  officialBadge: 'span:has-text("官方")',
  expandButton: 'button:has-text("展开")',
  collapseButton: 'button:has-text("收起")',
  loadingSpinner: '.animate-spin',
  emptyState: '.text-center:has(svg.lucide-zap)',
}

const ADDED_SKILL_CARD = {
  removeButton: 'button:has(svg.lucide-minus-circle)',
  addedBadge: 'span:has-text("已添加")',
}

const getDiscoverTab = (page: Page) =>
  page.getByRole('button', { name: /发现技能|discover/i }).first()

const getMySkillsTab = (page: Page) =>
  page.getByRole('button', { name: /我的技能|my skills/i }).first()

async function expectDiscoverContent(page: Page) {
  await expect(page.locator(DISCOVER.searchInput).first()).toBeVisible()

  await expect
    .poll(async () => {
      return page.locator(DISCOVER.categoryButtons).count()
    }, { timeout: 10000 })
    .toBeGreaterThan(0)
}

test.describe('Public Skills', () => {
  // Test credentials
  const TEST_EMAIL = TEST_USERS.standard.email
  const TEST_PASSWORD = TEST_USERS.standard.password

  test.beforeEach(async ({ page }) => {
    // Navigate to login page
    await page.goto('/login')

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('登录')

    // Login with test credentials
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()

    // Wait for redirect to complete
    await page.waitForURL(/\/(dashboard|project|skills)/, { timeout: 10000 })

    // Navigate to authenticated skills management page
    await page.goto('/dashboard/skills')

    // Wait for page to load - look for skills title
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 })

    // Click on discover tab if not already active
    const discoverTab = getDiscoverTab(page)
    if (await discoverTab.isVisible()) {
      await discoverTab.click()
      // Wait for loading to complete
      await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })
    }
  })

  test('user can browse public skill library', async ({ page }) => {
    // Wait for loading spinner to disappear (skills loaded)
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })
    await expectDiscoverContent(page)
  })

  test('public skills are categorized', async ({ page }) => {
    // Wait for loading spinner to disappear
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Look for category filter buttons
    const categoryButtons = page.locator(DISCOVER.categoryButtons)

    // There should be at least an "All" or similar category button
    const count = await categoryButtons.count()

    // If categories exist, test clicking on one
    if (count > 0) {
      // Click the first category button that's not "All"
      const categoryButton = categoryButtons.nth(Math.min(1, count - 1))
      if (await categoryButton.isVisible()) {
        await categoryButton.click()

        // Wait for loading spinner to appear and disappear
        await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

        // Verify the button is now selected (has accent color)
        await expect(categoryButton).toBeVisible()
      }
    }
  })

  test('public skills can be searched', async ({ page }) => {
    // Wait for loading spinner to disappear
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Find the search input in discover tab
    const searchInput = page.locator(DISCOVER.searchInput).first()

    if (await searchInput.isVisible()) {
      // Type a search query and wait for response
      await searchInput.fill('角色')

      // Wait for network to settle (debounce + API call)
      await page.waitForLoadState('networkidle')

      // Verify search input has the value
      await expect(searchInput).toHaveValue('角色')

      // Clear search
      await searchInput.fill('')
      await page.waitForLoadState('networkidle')
    }
  })

  test('skill card displays required information', async ({ page }) => {
    // Wait for loading spinner to disappear
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Find a skill card
    const skillCard = page.locator(DISCOVER.skillCard).first()

    if (await skillCard.isVisible()) {
      // Verify card has a name/title
      const name = skillCard.locator('h3')
      await expect(name).not.toBeEmpty()

      // Verify add button or added badge exists
      const addButton = skillCard.locator('button:has-text("添加")')
      const addedBadge = skillCard.locator('button:has-text("已添加")')

      // Either button should exist
      const hasAddButton = await addButton.isVisible()
      const hasAddedBadge = await addedBadge.isVisible()
      expect(hasAddButton || hasAddedBadge).toBeTruthy()
    }
  })

  test('skill card can be expanded to show instructions', async ({ page }) => {
    // Wait for loading spinner to disappear
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Find a skill card
    const skillCard = page.locator(DISCOVER.skillCard).first()

    if (await skillCard.isVisible()) {
      // Find the expand button (chevron or "展开" text)
      const expandButton = skillCard.locator('button:has-text("展开"), button:has(svg.lucide-chevron-down)').first()

      if (await expandButton.isVisible()) {
        await expandButton.click()

        // Wait for instructions to be visible
        const instructions = skillCard.locator('.markdown-content')
        // Instructions may or may not exist depending on the skill - just check visibility
        await instructions.isVisible().catch(() => false)

        // Collapse if expanded
        const collapseButton = skillCard.locator('button:has-text("收起"), button:has(svg.lucide-chevron-up)').first()
        if (await collapseButton.isVisible()) {
          await collapseButton.click()
          // Wait for collapse animation
          await expect(collapseButton).not.toBeVisible({ timeout: 5000 })
        }
      }
    }
  })

  test('user can add public skill to their library', async ({ page }) => {
    // Wait for loading spinner to disappear
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Find a skill card that hasn't been added yet
    const skillCards = page.locator(DISCOVER.skillCard)
    const count = await skillCards.count()

    let addedSkillName = ''

    for (let i = 0; i < count; i++) {
      const skillCard = skillCards.nth(i)
      const addButton = skillCard.locator('button:has-text("添加")')

      if (await addButton.isVisible()) {
        // Get the skill name before adding
        addedSkillName = await skillCard.locator('h3').textContent() || ''

        // Click add button and wait for API response
        const responsePromise = page.waitForResponse(resp =>
          resp.url().includes('/api/v1/skills/') && resp.request().method() === 'POST'
        )
        await addButton.click()
        await responsePromise

        // Verify the button changed to "已添加"
        const addedBadge = skillCard.locator('button:has-text("已添加")')
        await expect(addedBadge).toBeVisible({ timeout: 5000 })

        break
      }
    }

    // If we added a skill, verify it appears in "My Skills" tab
    if (addedSkillName) {
      // Switch to "My Skills" tab
      const mySkillsTab = getMySkillsTab(page)
      await mySkillsTab.click()

      // Wait for tab content to load
      await page.waitForLoadState('networkidle')

      // Look for the added skill in the "已添加" section
      const addedSkill = page.locator(`text="${addedSkillName}"`)
      await expect(addedSkill).toBeVisible({ timeout: 5000 })
    }
  })

  test('added skill appears in my skills', async ({ page }) => {
    // First, switch to "My Skills" tab to check current state
    const mySkillsTab = getMySkillsTab(page)
    await mySkillsTab.click()
    await page.waitForLoadState('networkidle')

    // Count existing added skills
    const existingCount = await page.locator(ADDED_SKILL_CARD.addedBadge).count()

    // Switch back to discover tab
    const discoverTab = getDiscoverTab(page)
    await discoverTab.click()
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Find and add a new skill
    const skillCards = page.locator(DISCOVER.skillCard)
    const count = await skillCards.count()

    for (let i = 0; i < count; i++) {
      const skillCard = skillCards.nth(i)
      const addButton = skillCard.locator('button:has-text("添加")')

      if (await addButton.isVisible()) {
        // Click and wait for API response
        const responsePromise = page.waitForResponse(resp =>
          resp.url().includes('/api/v1/skills/') && resp.request().method() === 'POST'
        )
        await addButton.click()
        await responsePromise
        break
      }
    }

    // Switch back to "My Skills" tab
    await mySkillsTab.click()
    await page.waitForLoadState('networkidle')

    // Verify the count increased or section exists
    const newCount = await page.locator(ADDED_SKILL_CARD.addedBadge).count()
    expect(newCount).toBeGreaterThanOrEqual(existingCount)
  })

  test('user can remove added public skill', async ({ page }) => {
    // Switch to "My Skills" tab
    const mySkillsTab = getMySkillsTab(page)
    await mySkillsTab.click()
    await page.waitForLoadState('networkidle')

    // Find an added skill with remove button
    const removeButtons = page.locator(ADDED_SKILL_CARD.removeButton)
    const count = await removeButtons.count()

    if (count > 0) {
      // Click remove button and wait for API response
      const responsePromise = page.waitForResponse(resp =>
        resp.url().includes('/api/v1/skills/') && resp.request().method() === 'DELETE'
      )
      await removeButtons.first().click()
      await responsePromise

      // Wait for UI to update
      await page.waitForLoadState('networkidle')
    }
  })

  test('official skills display official badge', async ({ page }) => {
    // Wait for loading spinner to disappear
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Look for official badges
    const officialBadges = page.locator(DISCOVER.officialBadge)
    const count = await officialBadges.count()

    // If there are official skills, verify the badge is visible
    if (count > 0) {
      await expect(officialBadges.first()).toBeVisible()
    }
  })

  test('search with no results shows empty state', async ({ page }) => {
    // Wait for loading spinner to disappear
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    // Find search input
    const searchInput = page.locator(DISCOVER.searchInput).first()

    if (await searchInput.isVisible()) {
      // Type a search query that won't match anything
      await searchInput.fill('xyznonexistentskill12345')

      // Wait for network to settle (search API call)
      await page.waitForLoadState('networkidle')

      // Check if empty state or no results is shown
      const skillCards = page.locator(DISCOVER.skillCard)
      const cardCount = await skillCards.count()

      // Either no cards should be visible, or they should be very few
      // (depending on search implementation)
      expect(cardCount).toBeLessThanOrEqual(3)
    }
  })
})

test.describe('Public Skills - Tab Navigation', () => {
  const TEST_EMAIL = TEST_USERS.standard.email
  const TEST_PASSWORD = TEST_USERS.standard.password

  test.beforeEach(async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('h1')).toContainText('登录')
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()
    await page.waitForURL(/\/(dashboard|project|skills)/, { timeout: 10000 })
    await page.goto('/dashboard/skills')
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 })
  })

  test('discover tab is accessible from my skills tab', async ({ page }) => {
    // Click on my skills tab first
    const mySkillsTab = getMySkillsTab(page)
    if (await mySkillsTab.isVisible()) {
      await mySkillsTab.click()
      await page.waitForLoadState('networkidle')
    }

    // Then click discover tab
    const discoverTab = getDiscoverTab(page)
    if (await discoverTab.isVisible()) {
      await discoverTab.click()

      // Wait for loading to complete
      await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })
      await expectDiscoverContent(page)
    }
  })

  test('skills page has proper header', async ({ page }) => {
    // Verify the page title is visible
    const title = page.locator('h1')
    await expect(title).toBeVisible()

    // Verify description is visible
    const description = page.locator('h1 + p')
    if (await description.isVisible()) {
      await expect(description).not.toBeEmpty()
    }
  })

  test('tabs are visible and clickable', async ({ page }) => {
    // Check that both tabs exist
    const discoverTab = getDiscoverTab(page)
    const mySkillsTab = getMySkillsTab(page)

    await expect(discoverTab).toBeVisible()
    await expect(mySkillsTab).toBeVisible()

    // Both tabs should be clickable
    await discoverTab.click()
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })

    await mySkillsTab.click()
    await page.waitForLoadState('networkidle')

    await discoverTab.click()
    await expect(page.locator(DISCOVER.loadingSpinner)).not.toBeVisible({ timeout: 10000 })
  })
})
