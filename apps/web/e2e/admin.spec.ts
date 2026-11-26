import { test, expect, Page } from '@playwright/test';
import { config, TEST_USERS } from './config';

/**
 * E2E Tests for Admin Functionality
 *
 * These tests cover admin features:
 * - User management (CRUD, search, pagination)
 * - System prompts management
 * - Skill review (approve/reject)
 * - Access control for admin routes
 */

// Admin test credentials
const ADMIN_EMAIL = 'test-admin@example.com';
const ADMIN_PASSWORD = 'TestAdmin123!';

// Regular user credentials (for access control tests)
const USER_EMAIL = process.env.E2E_TEST_EMAIL || TEST_USERS.standard.email;
const USER_PASSWORD = process.env.E2E_TEST_PASSWORD || TEST_USERS.standard.password;

// UI Selectors
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const ADMIN = {
  layout: '.h-screen.w-screen.overflow-hidden',
  sidebar: '.w-64, nav',
  header: 'header',
  mainContent: 'main.overflow-y-auto, main',
};

const ADMIN_SIDEBAR = {
  dashboardLink: 'aside a[href="/admin"]',
  usersLink: 'aside a[href="/admin/users"]',
  usageLink: 'aside a[href="/admin/usage"]',
  billingLink: 'aside a[href="/admin/payment-orders"]',
  operationsLink: 'aside a[href="/admin/feedback"]',
  systemLink: 'aside a[href="/admin/prompts"]',
};

const USER_MANAGEMENT = {
  title: 'h1:has-text("用户"), h1:has-text("Users")',
  searchInput: 'input[placeholder*="搜索"], input[placeholder*="Search"]',
  searchButton: 'button:has-text("搜索"), button:has-text("Search")',
  table: 'table',
  tableRow: 'tr',
  editButton: 'button[title="编辑"], button[title="Edit"]',
  deactivateButton: 'button[title="停用用户"], button[title="Deactivate user"]',
  pagination: '.flex.items-center.gap-2',
  prevButton: 'button:has-text("上一页"), button:has-text("Previous")',
  nextButton: 'button:has-text("下一页"), button:has-text("Next")',
};

const USER_EDIT_DIALOG = {
  overlay: '[role="dialog"]:not([aria-hidden="true"])',
  title: 'h2:has-text("编辑"), h2:has-text("Edit User"), h2:has-text("Edit")',
  usernameInput: '[role="dialog"]:not([aria-hidden="true"]) input[type="text"]',
  emailInput: '[role="dialog"]:not([aria-hidden="true"]) input[type="email"]',
  saveButton: 'button:has-text("保存"), button:has-text("Save")',
  cancelButton: 'button:has-text("取消"), button:has-text("Cancel")',
};

const DEACTIVATE_DIALOG = {
  overlay: '[role="dialog"]:not([aria-hidden="true"])',
  confirmButton: 'button:has-text("停用"), button:has-text("Deactivate")',
  cancelButton: 'button:has-text("取消"), button:has-text("Cancel")',
};

const PROMPT_MANAGEMENT = {
  title: 'h1:has-text("Prompt 配置"), h1:has-text("Prompt configs"), h1:has-text("Prompt")',
  promptCard: '[class*="card"][class*="cursor-pointer"], .grid > div',
  createButton: 'button:has-text("新建配置"), button:has-text("New config")',
  reloadButton: 'button:has-text("重新加载"), button:has-text("Reload")',
};

const SKILL_REVIEW = {
  title: 'h1:has-text("技能审核"), h1:has-text("Skill review")',
  pendingList: '.space-y-4',
  skillCard: '.space-y-4 > div',
  skillName: 'h3, [class*="font-semibold"]',
  skillCategory: 'span.text-xs, span.rounded-full',
  approveButton: 'button[title="批准"], button[title="Approve"], button:has(svg.lucide-check)',
  rejectButton: 'button[title="拒绝"], button[title="Reject"], button:has(svg.lucide-x)',
  expandButton:
    'button:has(svg.lucide-chevron-down), button:has(svg.lucide-chevron-up), button:has(svg[class*="chevron"])',
  emptyState: 'text=/No pending skills to review|没有待审核的技能|No data/i',
};

const REJECT_MODAL = {
  overlay: '[role="dialog"]:not([aria-hidden="true"])',
  textarea: 'textarea',
  confirmButton: 'button:has-text("确认拒绝"), button:has-text("Confirm Reject"), button:has-text("Confirm")',
  cancelButton: 'button:has-text("取消"), button:has-text("Cancel")',
};

async function waitForNetworkSettled(page: Page, timeout = 5000) {
  await page.waitForLoadState('networkidle', { timeout }).catch(() => {});
}

async function gotoWithRetry(
  page: Page,
  url: string,
  options: { attempts?: number; timeout?: number } = {}
) {
  const attempts = options.attempts ?? 2;
  const timeout = options.timeout ?? 25000;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
      await waitForNetworkSettled(page, 5000);
      return;
    } catch (error) {
      if (attempt === attempts) {
        throw error;
      }
      await page.waitForTimeout(400 * attempt);
    }
  }
}

// Helper to clear auth state in browser storage/cookies
async function clearAuthState(page: Page) {
  await gotoWithRetry(page, '/login');
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  await page.context().clearCookies();
  await gotoWithRetry(page, '/login');
}

// Helper to login as admin
async function loginAsAdmin(page: Page) {
  await clearAuthState(page);
  await expect(page.locator('#identifier')).toBeVisible({ timeout: 10000 });
  await page.locator('#identifier').fill(ADMIN_EMAIL);
  await page.locator('#password').fill(ADMIN_PASSWORD);
  await page.locator('button[type="submit"]').click();
  // Wait for redirect - could be admin or dashboard
  await page.waitForURL(/\/(admin|dashboard)/, { timeout: 15000 });
  const user = await page.evaluate(() => {
    const raw = localStorage.getItem('user');
    return raw ? JSON.parse(raw) : null;
  });
  expect(user?.is_superuser).toBeTruthy();
}

// Helper to login as regular user
async function loginAsUser(page: Page) {
  await clearAuthState(page);
  await expect(page.locator('#identifier')).toBeVisible({ timeout: 10000 });
  await page.locator('#identifier').fill(USER_EMAIL);
  await page.locator('#password').fill(USER_PASSWORD);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/(project|dashboard)/, { timeout: 15000 });
  const user = await page.evaluate(() => {
    const raw = localStorage.getItem('user');
    return raw ? JSON.parse(raw) : null;
  });
  expect(user?.is_superuser).toBeFalsy();
}

// Helper to navigate to admin section
async function navigateToAdmin(page: Page) {
  await gotoWithRetry(page, '/admin');
  await page.waitForURL(/\/admin/, { timeout: 10000 });
}

test.describe('Admin Functionality', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await navigateToAdmin(page);
  });

  // ============================================
  // User Management Tests
  // ============================================

  test.describe('User Management', () => {
    test.beforeEach(async ({ page }) => {
      // Navigate to user management directly to avoid locale-dependent sidebar label mismatch
      await gotoWithRetry(page, '/admin/users');
      await expect(page.locator(USER_MANAGEMENT.title)).toBeVisible({ timeout: 10000 });
    });

    test('admin can list all users', async ({ page }) => {
      // Wait for user table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // Verify table has headers
      const tableHeaders = page.locator('table th');
      expect(await tableHeaders.count()).toBeGreaterThan(0);

      // Verify at least one user row exists
      const userRows = page.locator('table tbody tr');
      const rowCount = await userRows.count();
      expect(rowCount).toBeGreaterThan(0);
    });

    test('admin can search users by name/email', async ({ page }) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // Get initial user count
      const initialRows = await page.locator('table tbody tr').count();

      // Type in search box
      const searchInput = page.locator(USER_MANAGEMENT.searchInput);
      await searchInput.fill('admin');
      await page.click(USER_MANAGEMENT.searchButton);

      // Wait for results to update
      await page.waitForResponse(resp => resp.url().includes('/api/admin'));

      // Verify filtered results
      const filteredRows = await page.locator('table tbody tr').count();
      // After searching for 'admin', we should have fewer or equal results
      expect(filteredRows).toBeLessThanOrEqual(initialRows);
    });

    test('admin can view user details in table', async ({ page }) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // Verify first row has required columns
      const firstRow = page.locator('table tbody tr').first();

      // Check for username
      await expect(firstRow.locator('td').first()).not.toBeEmpty();

      // Check for email
      const emailCell = firstRow.locator('td').nth(1);
      await expect(emailCell).not.toBeEmpty();
    });

    test('admin can update user info', async ({ page }) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // Prefer editing standard E2E user to avoid mutating admin account
      const targetRow = page.locator('table tbody tr').filter({ hasText: USER_EMAIL }).first();
      if (await targetRow.count() === 0) {
        test.skip();
        return;
      }

      // Click edit button on target user
      const targetEditButton = targetRow.locator(USER_MANAGEMENT.editButton);
      await targetEditButton.click();

      // Wait for edit dialog
      await expect(page.locator(USER_EDIT_DIALOG.overlay)).toBeVisible({ timeout: 5000 });
      await expect(page.locator(USER_EDIT_DIALOG.title)).toBeVisible();

      // Verify form fields are populated
      const usernameInput = page.locator(USER_EDIT_DIALOG.usernameInput);
      const username = await usernameInput.inputValue();
      expect(username.length).toBeGreaterThan(0);

      // Modify username slightly
      await usernameInput.fill(username + '1');

      // Save changes
      await page.click(USER_EDIT_DIALOG.saveButton);

      // Wait for dialog to close
      await expect(page.locator(USER_EDIT_DIALOG.overlay)).not.toBeVisible({ timeout: 5000 });

      // Revert the change
      await page.locator('table tbody tr').filter({ hasText: USER_EMAIL }).first().locator(USER_MANAGEMENT.editButton).click();
      await expect(page.locator(USER_EDIT_DIALOG.overlay)).toBeVisible({ timeout: 5000 });
      await page.locator(USER_EDIT_DIALOG.usernameInput).fill(username);
      await page.click(USER_EDIT_DIALOG.saveButton);
      await expect(page.locator(USER_EDIT_DIALOG.overlay)).not.toBeVisible({ timeout: 5000 });
    });

    test('admin can deactivate user', async ({ page }, testInfo) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      const adminToken = await page.evaluate(() => localStorage.getItem('access_token'));
      expect(adminToken).toBeTruthy();
      const adminHeaders = {
        Authorization: `Bearer ${adminToken}`,
        'Content-Type': 'application/json',
      };

      // Prefer an isolated disposable user so the shared seed user is never
      // deactivated even if an assertion fails mid-test.
      const disposableEmail = `admin-deact-${Date.now()}-${testInfo.workerIndex}@example.com`;
      const inviteCode = process.env.E2E_TEST_INVITE_CODE || 'E2E1-TST1';
      const registerResp = await page.request.post(`${config.apiBaseUrl}/api/auth/register`, {
        data: {
          email: disposableEmail,
          password: 'DisposableUser123!',
          invite_code: inviteCode,
        },
      });

      const targetEmail = registerResp.ok() ? disposableEmail : USER_EMAIL;
      const listResp = await page.request.get(
        `${config.apiBaseUrl}/api/admin/users?search=${encodeURIComponent(targetEmail)}`,
        { headers: adminHeaders }
      );
      expect(listResp.ok()).toBe(true);
      const listPayload = (await listResp.json()) as {
        items?: Array<{ id: string; email: string; is_active?: boolean }>;
      };
      const targetUser = (listPayload.items ?? []).find((u) => u.email === targetEmail);
      if (!targetUser) {
        test.skip();
        return;
      }

      const originalActive = targetUser.is_active ?? true;

      // Search for the exact target user so the row does not depend on page-1 ordering.
      const searchInput = page.locator(USER_MANAGEMENT.searchInput);
      await searchInput.fill(targetEmail);
      const searchResponse = page.waitForResponse(
        (resp) => resp.url().includes('/api/admin/users') && resp.request().method() === 'GET'
      );
      await page.click(USER_MANAGEMENT.searchButton);
      await searchResponse;

      const targetRow = page.locator('table tbody tr').filter({ hasText: targetEmail }).first();
      await expect(targetRow).toBeVisible({ timeout: 10000 });

      try {
        await targetRow.locator(USER_MANAGEMENT.editButton).click();
        await expect(page.locator(USER_EDIT_DIALOG.overlay)).toBeVisible({ timeout: 5000 });

        const checkbox = page.locator('button[role="checkbox"], input[type="checkbox"]').first();
        await checkbox.click();

        const updateResponse = page.waitForResponse(
          (resp) => resp.url().includes(`/api/admin/users/${targetUser.id}`) && resp.request().method() === 'PUT'
        );
        await page.click(USER_EDIT_DIALOG.saveButton);
        expect((await updateResponse).ok()).toBe(true);
        await expect(page.locator(USER_EDIT_DIALOG.overlay)).not.toBeVisible({ timeout: 5000 });
      } finally {
        // Always restore active status via admin API so no user state leaks across tests.
        await page.request.put(`${config.apiBaseUrl}/api/admin/users/${targetUser.id}`, {
          headers: adminHeaders,
          data: { is_active: originalActive },
        });
      }
    });

    test('admin cannot deactivate self', async ({ page }) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // The signed-in admin's own row offers no deactivate action (the API also refuses it).
      const ownRow = page.locator('table tbody tr').filter({ hasText: ADMIN_EMAIL }).first();
      if (await ownRow.count() === 0) {
        test.skip();
        return;
      }
      await expect(ownRow.locator(USER_MANAGEMENT.deactivateButton)).toHaveCount(0);
    });

    test('admin can cancel user edit', async ({ page }) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // Click edit on standard E2E user
      const targetRow = page.locator('table tbody tr').filter({ hasText: USER_EMAIL }).first();
      if (await targetRow.count() === 0) {
        test.skip();
        return;
      }
      await targetRow.locator(USER_MANAGEMENT.editButton).click();

      // Wait for dialog
      await expect(page.locator(USER_EDIT_DIALOG.overlay)).toBeVisible({ timeout: 5000 });

      // Click cancel
      await page.click(USER_EDIT_DIALOG.cancelButton);

      // Dialog should close
      await expect(page.locator(USER_EDIT_DIALOG.overlay)).not.toBeVisible({ timeout: 5000 });
    });

    test('admin can cancel user deactivation', async ({ page }) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // Open deactivate on the first row that offers it (active users other than the admin)
      const deactivate = page.locator(`table tbody ${USER_MANAGEMENT.deactivateButton}`).first();
      if (await deactivate.count() === 0) {
        test.skip();
        return;
      }
      await deactivate.click();

      // Wait for the confirmation dialog
      await expect(page.locator(DEACTIVATE_DIALOG.overlay)).toBeVisible({ timeout: 5000 });

      // Cancel
      await page.click(DEACTIVATE_DIALOG.cancelButton);

      // Dialog should close
      await expect(page.locator(DEACTIVATE_DIALOG.overlay)).not.toBeVisible({ timeout: 5000 });
    });

    test('user table pagination works', async ({ page }) => {
      // Wait for table to load
      await expect(page.locator(USER_MANAGEMENT.table)).toBeVisible({ timeout: 10000 });

      // Check if pagination exists
      const nextButton = page.locator(USER_MANAGEMENT.nextButton);

      // Only test pagination if next button is visible and enabled
      if (await nextButton.isVisible() && await nextButton.isEnabled()) {
        // Click next
        await nextButton.click();
        await waitForNetworkSettled(page);

        // Should be on page 2
        const prevButton = page.locator(USER_MANAGEMENT.prevButton);
        await expect(prevButton).toBeEnabled();

        // Click prev to go back
        await prevButton.click();
        await waitForNetworkSettled(page);
      }
    });
  });

  // ============================================
  // Prompt Management Tests
  // ============================================

  test.describe('Prompt Management', () => {
    test.beforeEach(async ({ page }) => {
      // Navigate directly to avoid locale-dependent sidebar label mismatch
      await gotoWithRetry(page, '/admin/prompts');
      await expect(page.locator(PROMPT_MANAGEMENT.title)).toBeVisible({ timeout: 10000 });
    });

    test('admin can list all prompt configurations', async ({ page }) => {
      // The E2E database intentionally seeds all supported project types.
      const promptCards = page.locator(PROMPT_MANAGEMENT.promptCard);
      await expect(promptCards).toHaveCount(3);
      for (const projectType of ['novel', 'short', 'screenplay']) {
        await expect(promptCards.getByRole('heading', { name: projectType, exact: true })).toBeVisible();
      }
    });

    test('admin can view prompt card details', async ({ page }) => {
      const promptCards = page.locator(PROMPT_MANAGEMENT.promptCard);
      await expect(promptCards).toHaveCount(3);
      const firstCard = promptCards.first();
      await expect(firstCard.getByRole('heading')).toHaveText(/^(novel|short|screenplay)$/);
      await expect(firstCard.getByText(/^(已启用|Active)$/)).toBeVisible();
    });

    test('admin can click create new prompt', async ({ page }) => {
      const createButton = page.locator(PROMPT_MANAGEMENT.createButton).first();
      if (await createButton.count() === 0) {
        test.skip();
      }

      // Click create button
      await createButton.click();

      // Should navigate to new prompt editor
      await page.waitForURL(/\/admin\/prompts\/new/, { timeout: 5000 });
      expect(page.url()).toContain('/admin/prompts/new');
    });

    test('admin can edit prompt configuration', async ({ page }) => {
      // Wait for prompts to load
      await waitForNetworkSettled(page);

      const promptCards = page.locator(PROMPT_MANAGEMENT.promptCard);
      const cardCount = await promptCards.count();

      if (cardCount > 0) {
        // Click on first prompt card
        await promptCards.first().click();

        // Should navigate to editor
        await page.waitForURL(/\/admin\/prompts\//, { timeout: 5000 });
        expect(page.url()).toContain('/admin/prompts/');
      } else {
        test.skip();
      }
    });

    test('admin can reload prompts from database', async ({ page }) => {
      const reloadResponsePromise = page.waitForResponse(
        (resp) => resp.url().includes('/prompts/reload') && resp.request().method() === 'POST'
      );
      await page.click(PROMPT_MANAGEMENT.reloadButton);
      const reloadResponse = await reloadResponsePromise;
      expect(reloadResponse.ok()).toBe(true);

      await expect(page.locator(PROMPT_MANAGEMENT.reloadButton)).toBeEnabled({ timeout: 5000 });
    });

    test('prompt cards show version info', async ({ page }) => {
      // Wait for prompts to load
      await waitForNetworkSettled(page);

      const promptCards = page.locator(PROMPT_MANAGEMENT.promptCard);
      const cardCount = await promptCards.count();

      if (cardCount === 0) {
        test.skip();
        return;
      }

      const firstCard = promptCards.first();
      await expect(firstCard.getByText(/版本|Version/i)).toBeVisible({ timeout: 5000 });
    });
  });

  // ============================================
  // Skill Review Tests
  // ============================================

  test.describe('Skill Review', () => {
    test.beforeEach(async ({ page }) => {
      // Navigate directly to avoid locale-dependent sidebar label mismatch
      await gotoWithRetry(page, '/admin/skills');
      await expect(page.locator(SKILL_REVIEW.title)).toBeVisible({ timeout: 10000 });
    });

    test('admin can view pending skills list', async ({ page }) => {
      // Wait for skills to load
      await waitForNetworkSettled(page);

      // Check for either skill cards or empty state
      const skillCards = page.locator(SKILL_REVIEW.skillCard);
      const emptyState = page.locator(SKILL_REVIEW.emptyState);

      const hasCards = await skillCards.count() > 0;
      const hasEmptyState = await emptyState.isVisible().catch(() => false);

      // Either skills or empty state should be visible
      expect(hasCards || hasEmptyState).toBeTruthy();
    });

    test('admin can see skill card details', async ({ page }) => {
      // Wait for skills to load
      await waitForNetworkSettled(page);

      const skillCards = page.locator(SKILL_REVIEW.skillCard);
      const cardCount = await skillCards.count();

      if (cardCount > 0) {
        const firstCard = skillCards.first();

        // Check for skill name
        await expect(firstCard.locator(SKILL_REVIEW.skillName)).toBeVisible();

        // Check for approve/reject buttons
        await expect(firstCard.locator(SKILL_REVIEW.approveButton)).toBeVisible();
        await expect(firstCard.locator(SKILL_REVIEW.rejectButton)).toBeVisible();

        // Check for expand button
        await expect(firstCard.locator(SKILL_REVIEW.expandButton)).toBeVisible();
      } else {
        // Check for empty state
        await expect(page.locator(SKILL_REVIEW.emptyState)).toBeVisible();
      }
    });

    test('admin can expand skill to view instructions', async ({ page }) => {
      // Wait for skills to load
      await waitForNetworkSettled(page);

      const skillCards = page.locator(SKILL_REVIEW.skillCard);
      const cardCount = await skillCards.count();

      if (cardCount > 0) {
        const firstCard = skillCards.first();

        // Click expand button
        await firstCard.locator(SKILL_REVIEW.expandButton).click();

        // Check for markdown content area
        const markdownContent = firstCard.locator('.markdown-content, .prose');
        await expect(markdownContent).toBeVisible({ timeout: 3000 });
      } else {
        test.skip();
      }
    });

    test('admin can approve pending skill', async ({ page }) => {
      // Wait for skills to load
      await waitForNetworkSettled(page);

      const skillCards = page.locator(SKILL_REVIEW.skillCard);
      const cardCount = await skillCards.count();

      if (cardCount > 0) {
        const firstCard = skillCards.first();

        // Click approve button
        await firstCard.locator(SKILL_REVIEW.approveButton).click();

        // Wait for processing
        await waitForNetworkSettled(page);

        // Skill should be removed from list (or show success state)
        const remainingCards = page.locator(SKILL_REVIEW.skillCard);
        const newCount = await remainingCards.count();

        // Card count should decrease or skill should no longer be visible
        expect(newCount).toBeLessThanOrEqual(cardCount);
      } else {
        // No pending skills - that's acceptable
        test.skip();
      }
    });

    test('admin can reject pending skill with reason', async ({ page }) => {
      // Wait for skills to load
      await waitForNetworkSettled(page);

      const skillCards = page.locator(SKILL_REVIEW.skillCard);
      const cardCount = await skillCards.count();

      if (cardCount > 0) {
        const firstCard = skillCards.first();

        // Click reject button
        await firstCard.locator(SKILL_REVIEW.rejectButton).click();

        // Wait for reject modal
        await expect(page.locator(REJECT_MODAL.overlay)).toBeVisible({ timeout: 5000 });

        // Enter rejection reason
        await page.locator(REJECT_MODAL.textarea).fill('Test rejection reason');

        // Confirm rejection
        await page.click(REJECT_MODAL.confirmButton);

        // Wait for processing
        await waitForNetworkSettled(page);

        // Modal should close
        await expect(page.locator(REJECT_MODAL.overlay)).not.toBeVisible({ timeout: 5000 });
      } else {
        test.skip();
      }
    });

    test('admin can cancel skill rejection', async ({ page }) => {
      // Wait for skills to load
      await waitForNetworkSettled(page);

      const skillCards = page.locator(SKILL_REVIEW.skillCard);
      const cardCount = await skillCards.count();

      if (cardCount > 0) {
        const firstCard = skillCards.first();

        // Click reject button
        await firstCard.locator(SKILL_REVIEW.rejectButton).click();

        // Wait for reject modal
        await expect(page.locator(REJECT_MODAL.overlay)).toBeVisible({ timeout: 5000 });

        // Cancel rejection
        await page.click(REJECT_MODAL.cancelButton);

        // Modal should close
        await expect(page.locator(REJECT_MODAL.overlay)).not.toBeVisible({ timeout: 5000 });
      } else {
        test.skip();
      }
    });

    test('skill review shows empty state when no pending skills', async ({ page }) => {
      // Wait for skills to load
      await waitForNetworkSettled(page);

      const skillCards = page.locator(SKILL_REVIEW.skillCard);
      const cardCount = await skillCards.count();

      if (cardCount === 0) {
        // Should show empty state
        await expect(page.locator(SKILL_REVIEW.emptyState)).toBeVisible();
      }
      // If there are skills, this test is N/A
    });
  });

  // ============================================
  // Navigation Tests
  // ============================================

  test.describe('Admin Navigation', () => {
    test('admin sidebar shows all 6 flat navigation options', async ({ page }) => {
      await expect(page.locator(ADMIN_SIDEBAR.dashboardLink)).toBeVisible();
      await expect(page.locator(ADMIN_SIDEBAR.usersLink)).toBeVisible();
      await expect(page.locator(ADMIN_SIDEBAR.usageLink)).toBeVisible();
      await expect(page.locator(ADMIN_SIDEBAR.billingLink)).toBeVisible();
      await expect(page.locator(ADMIN_SIDEBAR.operationsLink)).toBeVisible();
      await expect(page.locator(ADMIN_SIDEBAR.systemLink)).toBeVisible();
    });

    test('admin can navigate to dashboard', async ({ page }) => {
      await page.click(ADMIN_SIDEBAR.dashboardLink);
      await waitForNetworkSettled(page);
      expect(page.url()).toContain('/admin');
    });

    test('admin can navigate between sections and sub-tabs', async ({ page }) => {
      // Navigate to users
      await page.click(ADMIN_SIDEBAR.usersLink);
      await expect(page.locator(USER_MANAGEMENT.title)).toBeVisible({ timeout: 5000 });

      // Navigate to system (Prompt configs default)
      await page.click(ADMIN_SIDEBAR.systemLink);
      await expect(page.locator(PROMPT_MANAGEMENT.title)).toBeVisible({ timeout: 5000 });

      // Navigate to operations then switch to Skill review sub-tab
      await page.click(ADMIN_SIDEBAR.operationsLink);
      await page.locator('main nav a[href="/admin/skills"]').click();
      await expect(page.locator(SKILL_REVIEW.title)).toBeVisible({ timeout: 5000 });
    });
  });
});

// ============================================
// Access Control Tests (Separate describe block)
// ============================================

test.describe('Admin Access Control', () => {
  test('non-admin cannot access admin pages', async ({ page }) => {
    // Login as regular user
    await loginAsUser(page);

    // Try to access admin page
    await gotoWithRetry(page, '/admin');

    // Wait for redirect or error
    await waitForNetworkSettled(page);

    // Should show insufficient permission state
    await expect(page.locator('text=/No access|无权访问/i')).toBeVisible({ timeout: 10000 });
  });

  test('non-admin cannot access user management', async ({ page }) => {
    // Login as regular user
    await loginAsUser(page);

    // Try to access user management directly
    await gotoWithRetry(page, '/admin/users');

    // Wait for redirect or error
    await waitForNetworkSettled(page);

    // Should show insufficient permission state
    await expect(page.locator('text=/No access|无权访问/i')).toBeVisible({ timeout: 10000 });
  });

  test('non-admin cannot access prompt management', async ({ page }) => {
    // Login as regular user
    await loginAsUser(page);

    // Try to access prompt management directly
    await gotoWithRetry(page, '/admin/prompts');

    // Wait for redirect or error
    await waitForNetworkSettled(page);

    // Should show insufficient permission state
    await expect(page.locator('text=/No access|无权访问/i')).toBeVisible({ timeout: 10000 });
  });

  test('non-admin cannot access skill review', async ({ page }) => {
    // Login as regular user
    await loginAsUser(page);

    // Try to access skill review directly
    await gotoWithRetry(page, '/admin/skills');

    // Wait for redirect or error
    await waitForNetworkSettled(page);

    // Should show insufficient permission state
    await expect(page.locator('text=/No access|无权访问/i')).toBeVisible({ timeout: 10000 });
  });

  test('unauthenticated user is redirected to login', async ({ page }) => {
    // Try to access admin without logging in
    await gotoWithRetry(page, '/admin');

    // Should be redirected to login
    await page.waitForURL(/\/login/, { timeout: 10000 });
    expect(page.url()).toContain('/login');
  });
});
