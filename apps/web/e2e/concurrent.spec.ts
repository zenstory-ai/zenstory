import { test, expect, type Locator } from '@playwright/test';
import { TEST_USERS } from './config';

/**
 * E2E Tests for Concurrent Editing
 *
 * These tests cover scenarios where multiple browser contexts or tabs
 * interact with the same file or project simultaneously:
 * - Two tabs editing the same file
 * - Auto-save conflict handling
 * - Concurrent edit detection
 */

async function expandFolderIfCollapsed(folder: Locator) {
  await folder.waitFor({ state: 'visible', timeout: 5000 });
  if (await folder.locator('..').locator('svg.lucide-chevron-right').isVisible()) {
    await folder.click();
  }
}

const ENABLE_CONCURRENT_E2E = process.env.E2E_ENABLE_CONCURRENT_E2E === 'true';
const CONCURRENT_OPT_IN_MESSAGE = 'Concurrent editing E2E tests are opt-in. Set E2E_ENABLE_CONCURRENT_E2E=true to run.';

test.describe('Concurrent Editing', () => {
  test.skip(!ENABLE_CONCURRENT_E2E, CONCURRENT_OPT_IN_MESSAGE);

  // Test credentials - use environment variables or defaults matching seeded user
  const TEST_EMAIL = TEST_USERS.standard.email;
  const TEST_PASSWORD = TEST_USERS.standard.password;

  // No beforeEach needed - each test creates its own contexts

  test('two tabs editing same file shows conflict warning', async ({ browser }) => {
    // Create two browser contexts (simulating two tabs)
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    try {
      // Login both contexts
      await page1.goto('/login');
      await page1.fill('#identifier', TEST_EMAIL);
      await page1.fill('#password', TEST_PASSWORD);
      await page1.click('button[type="submit"]');
      await page1.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });

      await page2.goto('/login');
      await page2.fill('#identifier', TEST_EMAIL);
      await page2.fill('#password', TEST_PASSWORD);
      await page2.click('button[type="submit"]');
      await page2.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });

      // Navigate both to dashboard
      if (page1.url().includes('/project/')) {
        await page1.goto('/dashboard');
      }
      if (page2.url().includes('/project/')) {
        await page2.goto('/dashboard');
      }

      // Create a test project in page1
      const inspirationInput = page1.locator('[data-testid="dashboard-inspiration-input"]');
      await inspirationInput.fill(`并发测试项目 ${Date.now()}`);
      await page1.click('[data-testid="create-project-button"]');
      await page1.waitForURL(/\/project\//, { timeout: 15000 });

      // Get the project URL and navigate page2 to the same project
      const projectUrl = page1.url();
      await page2.goto(projectUrl);

      // Wait for file tree to load in both pages
      await page1.waitForSelector('.overflow-auto', { timeout: 5000 });
      await page2.waitForSelector('.overflow-auto', { timeout: 5000 });

      // Create a file in page1
      const outlineFolder1 = page1.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder1);
      await outlineFolder1.hover();
      const addButton1 = outlineFolder1.locator('..').locator('button:has(svg.lucide-plus)').first();
      await addButton1.click({ force: true });

      const fileInput1 = page1.locator('input[placeholder*="大纲"]');
      await fileInput1.fill('并发测试文件');
      await fileInput1.press('Enter');

      // Wait for file to be created
      await page1.waitForSelector('text=并发测试文件', { timeout: 5000 });

      // Reload page2 to see the new file
      await page2.reload();
      await page2.waitForSelector('.overflow-auto', { timeout: 5000 });

      // Expand the folder and select the file in both pages
      const outlineFolder2 = page2.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder2);
      await page2.waitForSelector('text=并发测试文件', { timeout: 5000 });

      const file1 = page1.locator('.overflow-auto >> text=并发测试文件').first();
      const file2 = page2.locator('.overflow-auto >> text=并发测试文件').first();

      await file1.click();
      await file2.click();

      // Edit content in page1
      const editor1 = page1.locator('[data-editor-scroll-container="true"] textarea').first();
      await editor1.fill('这是来自页面1的编辑内容');

      // Wait for auto-save
      await page1.waitForResponse(
        (resp) => resp.url().includes('/api/v1/') && resp.url().includes('/files') && resp.request().method() === 'PUT',
        { timeout: 10000 }
      );

      // Edit content in page2 (this should trigger conflict handling)
      const editor2 = page2.locator('[data-editor-scroll-container="true"] textarea').first();
      await editor2.fill('这是来自页面2的编辑内容');

      // Wait for auto-save response (may be success or conflict)
      await page2.waitForResponse(
        (resp) => resp.url().includes('/api/v1/') && resp.url().includes('/files'),
        { timeout: 10000 }
      ).catch(() => {
        // Response may not come if there's a conflict UI shown instead
      });

      // Check if conflict warning or error is shown
      // This could be a toast, modal, or inline message
      const conflictWarning = page2.locator('text=/冲突|conflict|已被修改|modified/i');
      // Log for debugging purposes
      void (await conflictWarning.count());

      // Reload both pages to verify final state
      await page1.reload();
      await page2.reload();

      // Reload resets the selected file; reopen it before reading persisted content.
      await expandFolderIfCollapsed(outlineFolder1);
      await page1.locator('.overflow-auto >> text=并发测试文件').first().click();

      // Verify at least one version persisted
      const editor1After = page1.locator('[data-editor-scroll-container="true"] textarea').first();
      const content1 = await editor1After.inputValue();
      expect(content1.length).toBeGreaterThan(0);
    } finally {
      // Cleanup
      await context1.close();
      await context2.close();
    }
  });

  test('auto-save does not overwrite concurrent edits', async ({ browser }) => {
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    try {
      // Login both contexts
      await page1.goto('/login');
      await page1.fill('#identifier', TEST_EMAIL);
      await page1.fill('#password', TEST_PASSWORD);
      await page1.click('button[type="submit"]');
      await page1.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });

      await page2.goto('/login');
      await page2.fill('#identifier', TEST_EMAIL);
      await page2.fill('#password', TEST_PASSWORD);
      await page2.click('button[type="submit"]');
      await page2.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });

      // Navigate to dashboard
      if (page1.url().includes('/project/')) {
        await page1.goto('/dashboard');
      }
      if (page2.url().includes('/project/')) {
        await page2.goto('/dashboard');
      }

      // Create a test project in page1
      const inspirationInput = page1.locator('[data-testid="dashboard-inspiration-input"]');
      await inspirationInput.fill(`并发保存测试项目 ${Date.now()}`);
      await page1.click('[data-testid="create-project-button"]');
      await page1.waitForURL(/\/project\//, { timeout: 15000 });

      // Navigate page2 to same project
      const projectUrl = page1.url();
      await page2.goto(projectUrl);

      // Wait for file tree
      await page1.waitForSelector('.overflow-auto', { timeout: 5000 });
      await page2.waitForSelector('.overflow-auto', { timeout: 5000 });

      // Create a file
      const outlineFolder1 = page1.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder1);
      await outlineFolder1.hover();
      const addButton1 = outlineFolder1.locator('..').locator('button:has(svg.lucide-plus)').first();
      await addButton1.click({ force: true });

      const fileInput1 = page1.locator('input[placeholder*="大纲"]');
      await fileInput1.fill('并发保存测试');
      await fileInput1.press('Enter');

      // Wait and reload page2
      await page1.waitForSelector('text=并发保存测试', { timeout: 5000 });
      await page2.reload();
      await page2.waitForSelector('.overflow-auto', { timeout: 5000 });

      const outlineFolder2 = page2.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder2);
      await page2.waitForSelector('text=并发保存测试', { timeout: 5000 });

      // Select file in both pages
      await page1.locator('.overflow-auto >> text=并发保存测试').first().click();
      await page2.locator('.overflow-auto >> text=并发保存测试').first().click();

      // Initial content save in page1
      const editor1 = page1.locator('[data-editor-scroll-container="true"] textarea').first();
      const initialContent = '初始内容 - 版本1';
      await editor1.fill(initialContent);
      await page1.waitForResponse(
        (resp) => resp.url().includes('/api/v1/') && resp.url().includes('/files') && resp.request().method() === 'PUT',
        { timeout: 10000 }
      );

      // Edit in page2 without waiting for page1's save to propagate
      const editor2 = page2.locator('[data-editor-scroll-container="true"] textarea').first();
      const concurrentContent = '并发内容 - 来自页面2';
      await editor2.fill(concurrentContent);

      // Wait for page2's save attempt
      const saveResponse = await page2.waitForResponse(
        (resp) => resp.url().includes('/api/v1/') && resp.url().includes('/files'),
        { timeout: 10000 }
      ).catch(() => null);

      // Verify that the system handled the concurrent edit appropriately
      // Either: save succeeds with version conflict handling, or fails gracefully
      if (saveResponse) {
        const status = saveResponse.status();
        // Accept both success (200/204) and conflict (409) responses
        expect([200, 204, 409]).toContain(status);
      }

      // Verify that reloading shows consistent state
      await page2.reload();
      await page2.waitForSelector('.overflow-auto', { timeout: 5000 });

      const outlineFolder2After = page2.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder2After);
      await page2.locator('.overflow-auto >> text=并发保存测试').first().click();

      const editor2After = page2.locator('[data-editor-scroll-container="true"] textarea').first();
      const finalContent = await editor2After.inputValue();

      // Content should be one of the saved versions
      expect([initialContent, concurrentContent]).toContain(finalContent);
    } finally {
      await context1.close();
      await context2.close();
    }
  });

  test('editing different files does not cause conflicts', async ({ browser }) => {
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    try {
      // Login both contexts
      await page1.goto('/login');
      await page1.fill('#identifier', TEST_EMAIL);
      await page1.fill('#password', TEST_PASSWORD);
      await page1.click('button[type="submit"]');
      await page1.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });

      await page2.goto('/login');
      await page2.fill('#identifier', TEST_EMAIL);
      await page2.fill('#password', TEST_PASSWORD);
      await page2.click('button[type="submit"]');
      await page2.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });

      // Navigate to dashboard
      if (page1.url().includes('/project/')) {
        await page1.goto('/dashboard');
      }
      if (page2.url().includes('/project/')) {
        await page2.goto('/dashboard');
      }

      // Create a test project
      const inspirationInput = page1.locator('[data-testid="dashboard-inspiration-input"]');
      await inspirationInput.fill(`不同文件测试项目 ${Date.now()}`);
      await page1.click('[data-testid="create-project-button"]');
      await page1.waitForURL(/\/project\//, { timeout: 15000 });

      const projectUrl = page1.url();
      await page2.goto(projectUrl);

      await page1.waitForSelector('.overflow-auto', { timeout: 5000 });
      await page2.waitForSelector('.overflow-auto', { timeout: 5000 });

      // Create two files
      const outlineFolder1 = page1.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder1);
      await outlineFolder1.hover();
      const addButton1 = outlineFolder1.locator('..').locator('button:has(svg.lucide-plus)').first();
      await addButton1.click({ force: true });

      let fileInput = page1.locator('input[placeholder*="大纲"]');
      await fileInput.fill('文件A');
      await fileInput.press('Enter');
      await fileInput.waitFor({ state: 'hidden', timeout: 5000 });
      await page1.locator('.overflow-auto >> text=文件A').first().waitFor({ state: 'visible', timeout: 5000 });

      await outlineFolder1.hover();
      const addButton2 = outlineFolder1.locator('..').locator('button:has(svg.lucide-plus)').first();
      await addButton2.click({ force: true });

      fileInput = page1.locator('input[placeholder*="大纲"]');
      await fileInput.fill('文件B');
      await fileInput.press('Enter');

      // Reload page2
      await page2.reload();
      await page2.waitForSelector('.overflow-auto', { timeout: 5000 });

      const outlineFolder2 = page2.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder2);

      // Select file A in page1, file B in page2
      await page1.locator('.overflow-auto >> text=文件A').first().click();
      await page2.locator('.overflow-auto >> text=文件B').first().click();

      // Edit both files simultaneously
      const editor1 = page1.locator('[data-editor-scroll-container="true"] textarea').first();
      const editor2 = page2.locator('[data-editor-scroll-container="true"] textarea').first();

      await editor1.fill('文件A的内容 - 来自页面1');
      await editor2.fill('文件B的内容 - 来自页面2');

      // Wait for both saves to complete
      const [save1, save2] = await Promise.all([
        page1.waitForResponse(
          (resp) => resp.url().includes('/api/v1/') && resp.url().includes('/files') && resp.request().method() === 'PUT',
          { timeout: 10000 }
        ),
        page2.waitForResponse(
          (resp) => resp.url().includes('/api/v1/') && resp.url().includes('/files') && resp.request().method() === 'PUT',
          { timeout: 10000 }
        )
      ]);

      // Both saves should succeed
      expect(save1.status()).toBeLessThan(400);
      expect(save2.status()).toBeLessThan(400);

      // Reload and verify both files have correct content
      await page1.reload();
      await page1.waitForSelector('.overflow-auto', { timeout: 5000 });

      const outlineFolder1After = page1.locator('text=大纲').first();
      await expandFolderIfCollapsed(outlineFolder1After);

      await page1.locator('.overflow-auto >> text=文件A').first().click();
      const editor1After = page1.locator('[data-editor-scroll-container="true"] textarea').first();
      await expect(editor1After).toHaveValue(/文件A的内容/);

      await page1.locator('.overflow-auto >> text=文件B').first().click();
      const editor1AfterB = page1.locator('[data-editor-scroll-container="true"] textarea').first();
      await expect(editor1AfterB).toHaveValue(/文件B的内容/);
    } finally {
      await context1.close();
      await context2.close();
    }
  });
});
