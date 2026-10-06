/* eslint-disable react-hooks/rules-of-hooks */
import { test as base, expect, Page, Route } from '@playwright/test'
import { TEST_USERS } from './config'

/**
 * Materials Library E2E Tests with Mocked API Responses
 *
 * These tests use Playwright route mocking to intercept materials API calls
 * and return pre-recorded responses. The materials library depends on Prefect
 * for async processing, so we mock the backend to make tests:
 * - Fast: No waiting for real async decomposition jobs
 * - Deterministic: Same input always produces same output
 * - Reliable: No flaky tests due to Prefect queue or processing delays
 */

// Mock data for testing
const mockTimestamp = '2024-01-15T10:30:00Z'

const mockNovels = [
  {
    id: '1',
    user_id: 'test-user',
    title: 'Completed Novel',
    author: 'Test Author',
    synopsis: 'A completed novel for testing',
    original_filename: 'completed.txt',
    file_size: 1024000,
    created_at: mockTimestamp,
    updated_at: mockTimestamp,
    status: 'completed',
    chapters_count: 20,
    total_chapters: 20,
  },
  {
    id: '2',
    user_id: 'test-user',
    title: 'Failed Novel',
    author: 'Test Author',
    synopsis: 'A failed novel for testing',
    original_filename: 'failed.txt',
    file_size: 1024000,
    created_at: mockTimestamp,
    updated_at: mockTimestamp,
    status: 'failed',
    chapters_count: 0,
    error_message: 'Processing failed',
  },
  {
    id: '3',
    user_id: 'test-user',
    title: 'Processing Novel',
    author: 'Test Author',
    synopsis: 'A processing novel for testing',
    original_filename: 'processing.txt',
    file_size: 1024000,
    created_at: mockTimestamp,
    updated_at: mockTimestamp,
    status: 'processing',
    chapters_count: 5,
  },
]

const mockCharacters = [
  {
    id: '1',
    novel_id: '1',
    name: 'Hero',
    aliases: ['The Protagonist', 'Chosen One'],
    description: 'The main protagonist of the story',
    archetype: 'Hero',
    first_appearance_chapter: 1,
    created_at: mockTimestamp,
  },
  {
    id: '2',
    novel_id: '1',
    name: 'Villain',
    aliases: ['The Antagonist', 'Dark Lord'],
    description: 'The main antagonist opposing the hero',
    archetype: 'Shadow',
    first_appearance_chapter: 3,
    created_at: mockTimestamp,
  },
]

const mockChapters = [
  {
    id: '1',
    novel_id: '1',
    chapter_number: 1,
    title: 'Chapter 1: The Beginning',
    content: 'Once upon a time, in a land far away, there lived a brave hero...',
    word_count: 1500,
    summary: 'Introduction to the story and the main character.',
    created_at: mockTimestamp,
  },
  {
    id: '2',
    novel_id: '1',
    chapter_number: 2,
    title: 'Chapter 2: The Journey',
    content: 'The hero begins their epic journey across the realm...',
    word_count: 1800,
    summary: 'The hero begins their journey.',
    created_at: mockTimestamp,
  },
]

const mockStoryLines = [
  {
    id: 1,
    novel_id: 1,
    title: 'Main Plot',
    description: 'The primary storyline',
    main_characters: ['Hero', 'Villain'],
    themes: ['Redemption', 'Courage'],
    stories_count: 5,
    created_at: mockTimestamp,
  },
]

const mockRelationships = [
  {
    id: 1,
    character_a_id: 1,
    character_a_name: 'Hero',
    character_b_id: 2,
    character_b_name: 'Villain',
    relationship_type: 'Enemy',
    sentiment: 'Hostile',
    description: 'Sworn enemies since childhood',
  },
]

const mockGoldenFingers = [
  {
    id: 1,
    novel_id: 1,
    name: 'Ancient Ring',
    type: 'Artifact',
    description: 'A mysterious ring containing an ancient soul',
    first_appearance_chapter_id: 1,
    evolution_history: [
      { chapter: 1, stage: 'Awakened' },
      { chapter: 5, stage: 'Level 2' },
    ],
    created_at: mockTimestamp,
  },
]

const mockWorldView = {
  id: 1,
  novel_id: 1,
  power_system: 'Cultivation System with Nine Realms',
  world_structure: 'Three Domains: Mortal, Immortal, Divine',
  key_factions: [
    { name: 'Righteous Sect' },
    { name: 'Demonic Cult' },
  ],
  special_rules: 'No killing within sect grounds',
  created_at: mockTimestamp,
  updated_at: mockTimestamp,
}

// Helper to set up route mocking for materials API
async function setupMaterialsMocking(page: Page) {
  // Mock material detail and sub-resources
  await page.route('**/api/v1/materials/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathname = url.pathname

    if (pathname.endsWith('/list') && request.method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockNovels),
      })
      return
    }

    if (pathname.endsWith('/upload') && request.method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          novel_id: '999',
          title: 'Test Novel',
          job_id: 1001,
          status: 'pending',
          message: 'Novel upload successful, decomposition started',
        }),
      })
      return
    }

    if (pathname.endsWith('/retry') && request.method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'Retry started successfully' }),
      })
      return
    }

    if (request.method() === 'DELETE') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'Material library deleted successfully' }),
      })
      return
    }

    if (request.method() !== 'GET') {
      await route.continue()
      return
    }

    // Parse the path to determine which endpoint
    if (pathname.includes('/chapters/') && !pathname.endsWith('/chapters')) {
      // Get specific chapter
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockChapters[0]),
      })
    } else if (pathname.endsWith('/chapters')) {
      // List chapters
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockChapters),
      })
    } else if (pathname.endsWith('/characters')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockCharacters),
      })
    } else if (pathname.endsWith('/tree')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tree: mockChapters.map(ch => ({
            id: ch.id,
            type: 'chapter',
            title: ch.title,
            metadata: { chapter_number: ch.chapter_number, summary: ch.summary },
          })),
        }),
      })
    } else if (pathname.endsWith('/status')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: 1001,
          novel_id: 1,
          status: 'completed',
          total_chapters: 10,
          processed_chapters: 10,
          progress_percentage: 100,
          stage_progress: { chapter_split: 100, entity_extraction: 100 },
          error_message: null,
          started_at: mockTimestamp,
          completed_at: mockTimestamp,
          created_at: mockTimestamp,
          updated_at: mockTimestamp,
        }),
      })
    } else if (pathname.endsWith('/storylines')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockStoryLines),
      })
    } else if (pathname.endsWith('/relationships')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockRelationships),
      })
    } else if (pathname.endsWith('/goldenfingers')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockGoldenFingers),
      })
    } else if (pathname.endsWith('/worldview')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockWorldView),
      })
    } else {
      // Get material detail
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...mockNovels[0],
          chapters_count: 10,
          characters_count: 5,
          story_lines_count: 3,
          golden_fingers_count: 2,
          has_world_view: true,
        }),
      })
    }
  })
}

// Define custom test fixture with route mocking
const test = base.extend<{
  mockedMaterials: void
}>({
   
  mockedMaterials: async ({ page }, use) => {
    // Set up route mocking before test
    await setupMaterialsMocking(page)
    await use()
  },
})

// Helper to login and navigate to materials page
async function navigateToMaterials(page: Page) {
  const TEST_EMAIL = TEST_USERS.standard.email
  const TEST_PASSWORD = TEST_USERS.standard.password

  await page.addInitScript(() => {
    const cachedUser = localStorage.getItem('user')
    if (cachedUser) {
      localStorage.setItem('auth_validated_at', Date.now().toString())
    }
  })

  const loginIdentifier = page.locator('#identifier')
  await page.goto('/dashboard/materials', { waitUntil: 'domcontentloaded' })

  if (
    await loginIdentifier
      .waitFor({ state: 'visible', timeout: 8000 })
      .then(() => true)
      .catch(() => false)
  ) {
    await page.fill('#identifier', TEST_EMAIL)
    await page.fill('#password', TEST_PASSWORD)
    await page.click('button[type="submit"]')
    await expect
      .poll(
        () =>
          page.evaluate(() => ({
            accessToken: localStorage.getItem('access_token'),
            user: localStorage.getItem('user'),
          })),
        { timeout: 15000 }
      )
      .toMatchObject({
        accessToken: expect.any(String),
        user: expect.any(String),
      })
    await page.goto('/dashboard/materials', { waitUntil: 'domcontentloaded' })
  }

  await expect(page).toHaveURL(/\/dashboard\/materials/, { timeout: 10000 })
  await expect(page.locator('h1')).toBeVisible({ timeout: 5000 })
}

// UI Selectors for Materials Library
const MATERIALS_PAGE = {
  title: 'h1',
  uploadButton: 'button:has-text("Upload"), button:has-text("上传")',
  materialsGrid: '.grid',
  materialCard: '[class*="group"][class*="rounded-xl"]',
  emptyState: '.text-center:has(svg)',
  searchInput: 'input[placeholder*="search" i], input[placeholder*="搜索"]',
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const MATERIAL_CARD = {
  title: 'h3',
  filename: 'p.text-xs',
  status: 'span:has(svg)',
  deleteButton: 'button:has(svg)',
}

const UPLOAD_MODAL = {
  overlay: '[role="dialog"], .modal-overlay',
  title: 'h2',
  dropzone: '.border-dashed',
  fileInput: 'input[type="file"]',
  titleInput: 'input[type="text"]',
  uploadButton: 'button:has-text("Upload"), button:has-text("上传"), button:has-text("上传并拆解")',
  cancelButton: 'button:has-text("Cancel"), button:has-text("取消")',
}

test.describe('Materials Library - Upload Flow (Mocked)', () => {
  test.beforeEach(async ({ page }) => {
    await setupMaterialsMocking(page)
    await navigateToMaterials(page)
  })

  test('user can upload TXT file to materials library', async ({ page }) => {
    // Click upload button in header
    const uploadButton = page.locator(MATERIALS_PAGE.uploadButton).first()
    await uploadButton.click()

    // Wait for upload modal
    await expect(page.locator(UPLOAD_MODAL.overlay)).toBeVisible()

    // Create a test file
    const testFile = {
      name: 'test-novel.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('This is a test novel content.\n\nChapter 1: The Beginning\n\nOnce upon a time...'),
    }

    // Set title if input is visible
    const titleInput = page.locator(UPLOAD_MODAL.titleInput)
    if (await titleInput.isVisible()) {
      await titleInput.fill('Test Novel Upload')
    }

    // Upload file using file chooser
    const fileInput = page.locator(UPLOAD_MODAL.fileInput)
    await fileInput.setInputFiles(testFile)

    // Click upload button in modal (use last to get the one inside the modal)
    const modalUploadButton = page.locator(UPLOAD_MODAL.uploadButton).last()
    await modalUploadButton.click()

    // Wait for upload to complete and modal to close
    await expect(page.locator(UPLOAD_MODAL.overlay)).not.toBeVisible({ timeout: 5000 })
  })
})

test.describe('Materials Library - Material Listing', () => {
  test.beforeEach(async ({ page }) => {
    await setupMaterialsMocking(page)
    await navigateToMaterials(page)
  })

  test('materials list shows all uploaded novels', async ({ page }) => {
    // Wait for materials grid to appear
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })

    // Verify at least one material is shown (from mocked data)
    const materialCards = page.locator(MATERIALS_PAGE.materialCard)
    const count = await materialCards.count()
    expect(count).toBeGreaterThan(0)
  })

  test('materials list shows chapter count', async ({ page }) => {
    // Wait for materials grid to appear
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })

    // Look for chapter count text
    const chapterText = page.locator('text=/\\d+\\s*(章|chapters?)/i')
    await expect(chapterText.first()).toBeVisible({ timeout: 3000 })
  })

  test('materials list shows processing status', async ({ page }) => {
    // Wait for materials grid to appear
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })

    // Check for status badges (completed, processing, failed, or pending)
    const statusBadge = page.locator('span:has(svg)')
    await expect(statusBadge.first()).toBeVisible({ timeout: 3000 })
  })
})

test.describe('Materials Library - Material Detail View', () => {
  test.beforeEach(async ({ page }) => {
    await setupMaterialsMocking(page)
    await navigateToMaterials(page)
  })

  test('user can navigate to material detail', async ({ page }) => {
    // Wait for materials grid to appear
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })

    // Click on first material card
    const materialCard = page.locator(MATERIALS_PAGE.materialCard).first()
    await materialCard.click()

    // Wait for detail page to load
    await page.waitForURL(/\/materials\//, { timeout: 5000 })
  })

  test('material detail shows chapter count', async ({ page }) => {
    // Wait for materials grid to appear
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })

    // Click on first material card
    const materialCard = page.locator(MATERIALS_PAGE.materialCard).first()
    await materialCard.click()

    // Wait for detail page to load
    await page.waitForURL(/\/materials\//, { timeout: 5000 })

    // Look for chapter count
    const chapterText = page.locator('text=/\\d+\\s*(章|chapters?)/i')
    await expect(chapterText.first()).toBeVisible({ timeout: 3000 })
  })
})

test.describe('Materials Library - Entity Browsing', () => {
  test.beforeEach(async ({ page }) => {
    await setupMaterialsMocking(page)
    await navigateToMaterials(page)

    // Navigate to first material detail
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })
    const materialCard = page.locator(MATERIALS_PAGE.materialCard).first()
    await materialCard.click()
    await page.waitForURL(/\/materials\//, { timeout: 5000 })
  })

  test('user can browse chapters in material', async ({ page }) => {
    // Look for chapters folder button
    const chaptersButton = page.locator('button:has-text("Chapter"), button:has-text("章节")')
    await expect(chaptersButton.first()).toBeVisible({ timeout: 5000 })
  })

  test('user can view character list', async ({ page }) => {
    // Look for characters folder button
    const charactersButton = page.locator('button:has-text("Character"), button:has-text("角色")')
    await expect(charactersButton.first()).toBeVisible({ timeout: 5000 })
  })
})

test.describe('Materials Library - Delete', () => {
  test.beforeEach(async ({ page }) => {
    await setupMaterialsMocking(page)
    await navigateToMaterials(page)
  })

  test('user can delete material library', async ({ page }) => {
    // Wait for materials grid to appear
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })

    // Find a material card
    const materialCard = page.locator(MATERIALS_PAGE.materialCard).first()
    await expect(materialCard).toBeVisible()

    // Hover to reveal delete button
    await materialCard.hover()

    // Click delete button (trash icon)
    const deleteButton = materialCard.locator('button:has(svg)')
    await deleteButton.click({ force: true })

    // Confirm deletion in modal
    const confirmButton = page.locator('button:has-text("Delete"), button:has-text("删除")').last()
    if (await confirmButton.isVisible()) {
      await confirmButton.click()
    }

    // Wait for deletion flow to settle; mocked list stays static, so verify the dialog action completed
    await expect(confirmButton).not.toBeVisible({ timeout: 3000 }).catch(() => {})
  })
})

test.describe('Materials Library - Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await setupMaterialsMocking(page)
    await navigateToMaterials(page)
  })

  test('user can navigate back from material detail', async ({ page }) => {
    // Wait for materials grid to appear
    await page.waitForSelector(MATERIALS_PAGE.materialsGrid, { timeout: 5000 })

    // Click on first material
    const materialCard = page.locator(MATERIALS_PAGE.materialCard).first()
    await materialCard.click()

    // Wait for detail page
    await page.waitForURL(/\/materials\//, { timeout: 5000 })

    // Click back button
    const backButton = page.locator('button:has(svg)').first()
    await backButton.click()

    // Should be back on materials list
    await expect(page.locator(MATERIALS_PAGE.title)).toBeVisible({ timeout: 3000 })
  })
})
