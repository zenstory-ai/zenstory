import { test, expect, type Page } from '@playwright/test';
import { FileSearchPOM } from './fixtures/page-objects/FileSearchPOM';
import { createOwnedProject, createOwnedFile, cleanupOwnedProject, type OwnedProject } from './fixtures/project-test-data';

const ENABLED = process.env.E2E_ENABLE_FILE_SEARCH_E2E === 'true';

test.describe('File Search (Cmd+K)', () => {
  test.skip(!ENABLED, 'File search E2E tests require E2E_ENABLE_FILE_SEARCH_E2E=true.');
  let project: OwnedProject | undefined;
  let exactFileId: string;
  let fileSearch: FileSearchPOM;

  test.beforeEach(async ({ page }, testInfo) => {
    project = await createOwnedProject(page, testInfo, 'File Search');
    for (const title of ['draft', 'draft-续篇', '我的draft记录', '角色资料']) {
      const file = await createOwnedFile(page, project, { title, fileType: 'outline', parentFolderTitle: '大纲', content: `搜索测试：${title}` });
      if (title === 'draft') exactFileId = file.id;
    }
    await page.goto(`/project/${project.id}`);
    await expect(page.getByTestId('file-search-input')).toBeVisible();
    fileSearch = new FileSearchPOM(page);
  });

  test.afterEach(async ({ page }) => {
    await cleanupOwnedProject(page, project);
    project = undefined;
  });

  async function searchDrafts(page: Page) {
    await fileSearch.openSearch();
    await fileSearch.search('draft');
    await expect(page.getByRole('option')).toHaveCount(3);
  }

  test('opens with Cmd+K keyboard shortcut', async ({ page }) => {
    await page.keyboard.press('Meta+k');
    await expect(page.getByTestId('file-search-input')).toBeFocused();
  });

  test('opens with Ctrl+K keyboard shortcut on Windows/Linux', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.getByTestId('file-search-input')).toBeFocused();
  });

  test('closes with Escape key', async ({ page }) => {
    await searchDrafts(page);
    await fileSearch.closeSearch();
    await expect(page.getByTestId('search-results-dropdown')).not.toBeVisible();
  });

  test('closes when clicking outside', async ({ page }) => {
    await searchDrafts(page);
    await page.getByTestId('editor-panel').click({ position: { x: 30, y: 60 } });
    await expect(page.getByTestId('search-results-dropdown')).not.toBeVisible();
  });

  test('shows loading state while searching', async ({ page }) => {
    const now = Date.now();
    await page.clock.install({ time: now });
    await page.clock.pauseAt(now + 1000);
    await fileSearch.openSearch();
    await fileSearch.search('draft');
    await expect(page.getByTestId('search-results-loading')).toBeVisible();
    await page.clock.runFor(400);
    await expect(page.getByRole('option')).toHaveCount(3);
    await expect(page.getByTestId('search-results-loading')).not.toBeVisible();
  });

  test('shows empty state for no results', async ({ page }) => {
    await fileSearch.openSearch();
    await fileSearch.search('zzzzzzzznonexistentfile123456');
    await expect(page.getByTestId('search-results-empty')).toBeVisible();
    expect(await fileSearch.isEmptyState()).toBe(true);
    await expect(page.getByRole('option')).toHaveCount(0);
  });

  test('displays results with fuzzy matching', async ({ page }) => {
    await fileSearch.openSearch();
    await fileSearch.search('RAF');
    await expect(page.getByRole('option')).toHaveCount(3);
    expect(await fileSearch.getResultTitles()).toEqual(expect.arrayContaining([
      expect.stringContaining('draft'), expect.stringContaining('draft-续篇'), expect.stringContaining('我的draft记录'),
    ]));
  });

  test('ranks exact matches higher than partial matches', async ({ page }) => {
    await searchDrafts(page);
    await expect(page.getByRole('option').first()).toContainText('draft');
    expect((await fileSearch.getResultTitles())[0]).not.toContain('续篇');
    expect((await fileSearch.getResultTitles())[0]).not.toContain('我的');
  });

  test('ranks prefix matches higher than contains matches', async ({ page }) => {
    await fileSearch.openSearch();
    await fileSearch.search('dra');
    await expect(page.getByRole('option')).toHaveCount(3);
    const titles = await fileSearch.getResultTitles();
    expect(titles[0]).not.toContain('我的');
    expect(titles[1]).not.toContain('我的');
    expect(titles[2]).toContain('我的draft记录');
  });

  test('navigates results with ArrowDown/ArrowUp', async ({ page }) => {
    await searchDrafts(page);
    await expect(page.getByRole('option').nth(0)).toHaveAttribute('aria-selected', 'true');
    await fileSearch.navigateResults('down');
    await expect(page.getByRole('option').nth(1)).toHaveAttribute('aria-selected', 'true');
    await fileSearch.navigateResults('up');
    await expect(page.getByRole('option').nth(0)).toHaveAttribute('aria-selected', 'true');
  });

  test('selects result with Enter key', async ({ page }) => {
    await searchDrafts(page);
    const opened = page.waitForResponse(response => response.url().endsWith(`/api/v1/files/${exactFileId}`) && response.request().method() === 'GET');
    await page.keyboard.press('Enter');
    expect((await opened).ok()).toBe(true);
    await expect(page.getByTestId('search-results-dropdown')).not.toBeVisible();
    await expect(page.locator('[data-editor-scroll-container] textarea').first()).toHaveValue('搜索测试：draft');
  });

  test('selecting result navigates to file', async ({ page }) => {
    await searchDrafts(page);
    const opened = page.waitForResponse(response => response.url().endsWith(`/api/v1/files/${exactFileId}`) && response.request().method() === 'GET');
    await fileSearch.selectResultByTitle('draft');
    expect((await opened).ok()).toBe(true);
    await expect(page.locator('[data-editor-scroll-container] textarea').first()).toHaveValue('搜索测试：draft');
    await page.reload();
    await expect(page.locator('[data-editor-scroll-container] textarea').first()).toHaveValue('搜索测试：draft');
  });

  test('handles Chinese input with IME composition', async ({ page }) => {
    await fileSearch.openSearch();
    await fileSearch.typeWithIME('角色资料');
    await expect(page.getByTestId('file-search-input')).toHaveValue('角色资料');
    await expect(page.getByRole('option')).toHaveCount(1);
    await expect(page.getByRole('option')).toContainText('角色资料');
  });
});
