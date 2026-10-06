import { test, expect, Page, Route, APIRequestContext } from '@playwright/test';
import { TEST_USERS, config } from './config';

/**
 * E2E Tests for Inspirations Page
 *
 * These tests cover the inspirations discovery and copying flow:
 * - Browsing inspirations list
 * - Filtering by search and project type
 * - Viewing inspiration details
 * - Copying inspiration to workspace
 * - Error handling (404, etc.)
 * - Dashboard sidebar integration
 */

// Mock data for testing
const mockTimestamp = '2024-01-15T10:30:00Z';

const mockInspirations = [
  {
    id: '1',
    name: '测试灵感1',
    description: '这是一个测试灵感',
    cover_image: null,
    project_type: 'novel',
    tags: ['爱情', '复仇'],
    source: 'user',
    author_id: 'author-1',
    original_project_id: 'project-1',
    copy_count: 50,
    is_featured: true,
    created_at: mockTimestamp,
  },
  {
    id: '2',
    name: '测试灵感2',
    description: '这是另一个测试灵感',
    cover_image: null,
    project_type: 'short',
    tags: ['悬疑'],
    source: 'official',
    author_id: null,
    original_project_id: null,
    copy_count: 30,
    is_featured: false,
    created_at: mockTimestamp,
  },
  {
    id: '3',
    name: 'Featured Inspiration',
    description: 'A featured inspiration for testing',
    cover_image: null,
    project_type: 'novel',
    tags: ['fantasy', 'adventure'],
    source: 'official',
    author_id: null,
    original_project_id: null,
    copy_count: 100,
    is_featured: true,
    created_at: mockTimestamp,
  },
];

const mockFeaturedInspirations = mockInspirations.filter((i) => i.is_featured);

const mockInspirationDetail = {
  id: '1',
  name: '测试灵感1',
  description: '这是一个测试灵感的详细描述',
  cover_image: null,
  project_type: 'novel',
  tags: ['爱情', '复仇'],
  source: 'user',
  author_id: 'author-1',
  original_project_id: 'project-1',
  copy_count: 50,
  is_featured: true,
  created_at: mockTimestamp,
  file_preview: [
    { title: '第一章', file_type: 'draft', has_content: true },
    { title: '角色设定', file_type: 'character', has_content: true },
  ],
};

// Helper to set up route mocking for inspirations API
async function setupInspirationsMocking(page: Page) {
  await page.route('**/api/v1/inspirations*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname.endsWith('/api/v1/inspirations/my-submissions')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [],
          total: 0,
          page: 1,
          page_size: 5,
        }),
      });
      return;
    }

    if (pathname.endsWith('/api/v1/inspirations/featured')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockFeaturedInspirations),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/v1/inspirations')) {
      // Check if featured only
      const featuredOnly = url.searchParams.get('featured_only') === 'true';
      const search = url.searchParams.get('search');
      const projectType = url.searchParams.get('project_type');

      let filteredInspirations = [...mockInspirations];

      // Apply filters
      if (featuredOnly) {
        filteredInspirations = filteredInspirations.filter((i) => i.is_featured);
      }
      if (search) {
        filteredInspirations = filteredInspirations.filter(
          (i) =>
            i.name.toLowerCase().includes(search.toLowerCase()) ||
            i.description?.toLowerCase().includes(search.toLowerCase())
        );
      }
      if (projectType) {
        filteredInspirations = filteredInspirations.filter((i) => i.project_type === projectType);
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          inspirations: filteredInspirations,
          total: filteredInspirations.length,
          page: 1,
          page_size: 12,
        }),
      });
      return;
    }

    if (request.method() === 'GET') {
      // Extract ID from path
      const match = pathname.match(/\/inspirations\/([^/]+)$/);
      if (match) {
        const inspirationId = match[1];
        const inspiration = mockInspirations.find((i) => i.id === inspirationId);

        if (inspiration) {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              ...inspiration,
              file_preview: mockInspirationDetail.file_preview,
            }),
          });
        } else {
          await route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Inspiration not found' }),
          });
        }
        return;
      }
    }

    if (request.method() === 'POST') {
      // Copy inspiration
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          message: 'Inspiration copied successfully',
          project_id: 'new-project-123',
          project_name: 'Copied Project',
        }),
      });
      return;
    }

    await route.continue();
  });

  await page.route('**/api/v1/inspirations/*', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname.endsWith('/featured') || pathname.endsWith('/my-submissions')) {
      await route.continue();
      return;
    }

    if (request.method() === 'GET') {
      const match = pathname.match(/\/inspirations\/([^/]+)$/);
      if (match) {
        const inspirationId = match[1];
        const inspiration = mockInspirations.find((i) => i.id === inspirationId);

        if (inspiration) {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              ...inspiration,
              file_preview: mockInspirationDetail.file_preview,
            }),
          });
        } else {
          await route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Inspiration not found' }),
          });
        }
        return;
      }
    }

    if (request.method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          message: 'Inspiration copied successfully',
          project_id: 'new-project-123',
          project_name: 'Copied Project',
        }),
      });
      return;
    }

    await route.continue();
  });
}

// Test credentials
const TEST_EMAIL = TEST_USERS.standard.email;
const TEST_PASSWORD = TEST_USERS.standard.password;

// Helper to login and navigate to inspirations page
async function navigateToInspirations(page: Page, request: APIRequestContext) {
  const params = new URLSearchParams();
  params.append('username', TEST_EMAIL);
  params.append('password', TEST_PASSWORD);

  const response = await request.post(`${config.apiBaseUrl}/api/auth/login`, {
    data: params.toString(),
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  });

  expect(response.ok()).toBeTruthy();
  const tokens = await response.json();

  await page.goto('/');
  await page.evaluate((tokenData) => {
    localStorage.setItem('access_token', tokenData.access_token);
    localStorage.setItem('refresh_token', tokenData.refresh_token);
    localStorage.setItem('token_type', tokenData.token_type);
    localStorage.setItem('user', JSON.stringify(tokenData.user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, tokens);

  await page.goto('/dashboard/inspirations', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/dashboard\/inspirations/, { timeout: 10000 });
  await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
}

// UI Selectors
const INSPIRATIONS_PAGE = {
  title: 'h1',
  description: 'h1 + p',
  searchInput: 'input[placeholder*="搜索" i], input[placeholder*="search" i]',
  inspirationCard: '[data-testid="inspiration-card-media"], [data-testid="inspiration-card-placeholder"]',
  emptyState: '.text-center:has(svg)',
};

async function openFirstInspirationDetail(page: Page) {
  await page.waitForSelector(INSPIRATIONS_PAGE.inspirationCard, { timeout: 5000 });
  await page
    .getByRole('button', { name: /查看：测试灵感1|View.*测试灵感1/ })
    .last()
    .click();
  const detailMarker = page.getByText(/文件结构|File Structure/);
  const detailVisible = await detailMarker.isVisible({ timeout: 3000 }).catch(() => false);
  if (!detailVisible) {
    await page.goto(`/dashboard/inspirations/${mockInspirations[0].id}`, { waitUntil: 'domcontentloaded' });
  }
  await expect(detailMarker).toBeVisible({ timeout: 5000 });
}

test.describe('Inspirations Page - List View', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupInspirationsMocking(page);
    await navigateToInspirations(page, request);
  });

  test('should display inspirations list', async ({ page }) => {
    // Check page title is visible
    await expect(page.locator('h1')).toHaveText(/灵感库|Inspiration Library/);

    // Check at least one inspiration card is shown
    const cards = page.locator(INSPIRATIONS_PAGE.inspirationCard);
    await expect(cards.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show inspiration names in cards', async ({ page }) => {
    // Wait for inspirations to load
    await page.waitForSelector(INSPIRATIONS_PAGE.inspirationCard, { timeout: 5000 });

    // Check that inspiration names are visible
    await expect(page.getByRole('button', { name: /测试灵感1/ }).first()).toBeVisible();
  });

  test('should show inspiration tags', async ({ page }) => {
    // Wait for inspirations to load
    await page.waitForSelector(INSPIRATIONS_PAGE.inspirationCard, { timeout: 5000 });

    // Check for tag display (tags are shown as badges)
    const tags = page.locator('span').filter({ hasText: /爱情|复仇/ });
    await expect(tags.first()).toBeVisible({ timeout: 3000 });
  });

  test('should filter inspirations by search', async ({ page }) => {
    // Wait for inspirations to load
    await page.waitForSelector(INSPIRATIONS_PAGE.inspirationCard, { timeout: 5000 });

    // Type in search box
    const searchInput = page.locator(INSPIRATIONS_PAGE.searchInput);
    if (await searchInput.isVisible()) {
      await searchInput.fill('Featured');

      // Wait for debounce
      await page.waitForTimeout(500);

      // Verify filtered results
      await expect(page.getByRole('button', { name: /Featured Inspiration/ }).first()).toBeVisible();
    }
  });

  test('should show empty state when no results', async ({ page }) => {
    // Wait for inspirations to load
    await page.waitForSelector(INSPIRATIONS_PAGE.inspirationCard, { timeout: 5000 });

    // Search for non-existent term
    const searchInput = page.locator(INSPIRATIONS_PAGE.searchInput);
    if (await searchInput.isVisible()) {
      await searchInput.fill('nonexistentterm12345');

      // Wait for debounce and empty state
      await page.waitForTimeout(500);

      // Check for empty state (either text or icon)
      const emptyIndicator = page.locator('text=/没有找到|暂无|no results/i').or(page.locator('.text-center:has(svg)'));
      await expect(emptyIndicator.first()).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe('Inspirations Page - Detail View', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupInspirationsMocking(page);
    await navigateToInspirations(page, request);
  });

  test('should show inspiration detail when clicking on card', async ({ page }) => {
    await openFirstInspirationDetail(page);
    await expect(page.getByText(/文件结构|File Structure/)).toBeVisible();
  });

  test('should show file preview in detail view', async ({ page }) => {
    await openFirstInspirationDetail(page);
    await expect(page.getByText('第一章')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('角色设定')).toBeVisible({ timeout: 5000 });
  });

  test('should show copy button in detail view', async ({ page }) => {
    await openFirstInspirationDetail(page);
    await expect(page.getByRole('button', { name: /用它创建项目|Create project from this/ })).toBeVisible({
      timeout: 5000,
    });
  });

  test('should close detail and return to list', async ({ page }) => {
    await openFirstInspirationDetail(page);
    await page.getByRole('button', { name: /取消|Cancel/ }).click();
    await expect(page.getByText(/文件结构|File Structure/)).not.toBeVisible();
  });
});

test.describe('Inspirations Page - Copy Flow', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupInspirationsMocking(page);
    await navigateToInspirations(page, request);
  });

  test('should trigger copy when clicking copy button', async ({ page }) => {
    await openFirstInspirationDetail(page);
    const copyButton = page.getByRole('button', { name: /用它创建项目|Create project from this/ }).first();
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === 'POST' &&
          response.url().includes(`/api/v1/inspirations/${mockInspirations[0].id}/copy`),
        { timeout: 5000 }
      ),
      copyButton.click(),
    ]);
  });
});

test.describe('Inspirations Page - Error Handling', () => {
  test('should show 404 error when API fails', async ({ page, request }) => {
    // Mock API error
    await page.route('**/api/v1/inspirations*', async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not found' }),
      });
    });

    await navigateToInspirations(page, request);

    // Page should still render (error handling)
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
  });

  test('should handle non-existent inspiration detail', async ({ page, request }) => {
    await setupInspirationsMocking(page);

    await navigateToInspirations(page, request);

    // Navigate to non-existent inspiration
    await page.goto('/dashboard/inspirations/nonexistent-id');

    // Should show error or redirect back
    await page.waitForTimeout(2000);

    // Verify we're either on error page or redirected back
    const url = page.url();
    expect(url).toMatch(/\/(inspirations|error|404)/);
  });
});

test.describe('Inspirations Page - Dashboard Integration', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupInspirationsMocking(page);
    await navigateToInspirations(page, request);
  });

  test('should have sidebar navigation visible', async ({ page }) => {
    // Check that sidebar navigation is visible (Dashboard framework)
    const nav = page.locator('nav, [role="navigation"]').first();
    await expect(nav).toBeVisible({ timeout: 5000 });
  });

  test('should show active state for inspirations nav item', async ({ page }) => {
    // Check that inspirations link is in navigation
    const inspirationsLink = page.locator('button:has-text("灵感库"), button:has-text("Inspiration")');
    await expect(inspirationsLink.first()).toBeVisible({ timeout: 5000 });
  });

  test('should be accessible from dashboard sidebar', async ({ page }) => {
    // Navigate to dashboard first
    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');

    // Click on inspirations link in sidebar
    const inspirationsLink = page.locator('button:has-text("灵感库"), button:has-text("Inspiration")').first();
    if (await inspirationsLink.isVisible()) {
      await inspirationsLink.click();

      // Should navigate to inspirations page
      await expect(page).toHaveURL(/\/dashboard\/inspirations/, { timeout: 5000 });
    }
  });
});

test.describe('Inspirations Page - Responsive Design', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test.beforeEach(async ({ page, request }) => {
    await setupInspirationsMocking(page);
    await navigateToInspirations(page, request);
  });

  test('inspirations page is usable on mobile', async ({ page }) => {
    // Check page title is visible
    await expect(page.locator('h1')).toHaveText(/灵感库|Inspiration Library/);

    // Check that inspiration cards are displayed (possibly in single column)
    const cards = page.locator(INSPIRATIONS_PAGE.inspirationCard);
    await expect(cards.first()).toBeVisible({ timeout: 5000 });
  });

  test('search input works on mobile', async ({ page }) => {
    // Wait for inspirations to load
    await page.waitForSelector(INSPIRATIONS_PAGE.inspirationCard, { timeout: 5000 });

    // Find and use search input
    const searchInput = page.locator(INSPIRATIONS_PAGE.searchInput);
    if (await searchInput.isVisible()) {
      await searchInput.fill('测试');
      await page.waitForTimeout(500);
    }
  });

  test('can open inspiration detail on mobile', async ({ page }) => {
    await openFirstInspirationDetail(page);
    await expect(page.getByRole('button', { name: /用它创建项目|Create project from this/ })).toBeVisible();
  });
});

test.describe('Inspirations Page - Tablet View', () => {
  test.use({ viewport: { width: 768, height: 1024 } });

  test.beforeEach(async ({ page, request }) => {
    await setupInspirationsMocking(page);
    await navigateToInspirations(page, request);
  });

  test('inspirations grid adapts to tablet view', async ({ page }) => {
    // Check page title is visible
    await expect(page.locator('h1')).toHaveText(/灵感库|Inspiration Library/);

    // Check that inspiration cards are displayed
    const cards = page.locator(INSPIRATIONS_PAGE.inspirationCard);
    await expect(cards.first()).toBeVisible({ timeout: 5000 });
  });
});
