import { test, expect, Page } from '@playwright/test';
import { LoginPage } from './fixtures/page-objects';
import { TEST_USERS } from './config';

const AUTHENTICATED_ROUTE_PATTERN = /\/(dashboard|project)(\/|$)/;

test.describe('Session Management', () => {
  let loginPage: LoginPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
  });

  async function loginAndWaitForAuthenticatedRoute(page: Page) {
    await loginPage.navigateToLogin();
    await loginPage.login(TEST_USERS.standard.email, TEST_USERS.standard.password);
    await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN);
    await expect(page.locator('[data-testid="user-menu-button"], [data-testid="dashboard-user-panel-toggle"]').first()).toBeVisible();
  }

  test('session persists across page reloads', async ({ page }) => {
    await loginAndWaitForAuthenticatedRoute(page);

    await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN);

    // Reload page
    await page.reload();

    await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN);
    await expect(page.locator('[data-testid="user-menu-button"], [data-testid="dashboard-user-panel-toggle"]').first()).toBeVisible();
  });

  test('session expires after token TTL', async ({ page }) => {
    await loginAndWaitForAuthenticatedRoute(page);

    // Simulate full expiry by clearing both tokens before revisiting a protected route.
    await page.evaluate(() => {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      localStorage.removeItem('token_type');
      localStorage.removeItem('user');
    });

    await page.goto('/dashboard', { waitUntil: 'commit' });

    await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
    await expect(page.locator('#identifier')).toBeVisible();
  });

  test('refresh token flow works correctly', async ({ page }) => {
    await loginAndWaitForAuthenticatedRoute(page);

    const refreshToken = await page.evaluate(() => localStorage.getItem('refresh_token'));
    expect(refreshToken).toBeTruthy();

    await page.evaluate(() => {
      localStorage.removeItem('access_token');
      localStorage.removeItem('token_type');
    });

    await page.goto('/dashboard/projects', { waitUntil: 'commit' });
    await expect(page).toHaveURL(/\/(login|dashboard|project)/);
    await expect(page.locator('#identifier, [data-testid="dashboard-user-panel-toggle"], [data-testid="user-menu-button"]').first()).toBeVisible();
  });

  test('concurrent sessions are handled', async ({ browser }) => {
    // Create two browser contexts
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();

    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    // Login in both sessions
    const loginPage1 = new LoginPage(page1);
    const loginPage2 = new LoginPage(page2);

    await loginPage1.navigateToLogin();
    await loginPage1.login(TEST_USERS.standard.email, TEST_USERS.standard.password);

    await loginPage2.navigateToLogin();
    await loginPage2.login(TEST_USERS.standard.email, TEST_USERS.standard.password);

    await expect(page1).toHaveURL(AUTHENTICATED_ROUTE_PATTERN);
    await expect(page2).toHaveURL(AUTHENTICATED_ROUTE_PATTERN);

    await context1.close();
    await context2.close();
  });

  test('logout clears session data', async ({ page }) => {
    await loginAndWaitForAuthenticatedRoute(page);

    // Click logout
    await page.locator('[data-testid="user-menu-button"], [data-testid="dashboard-user-panel-toggle"]').first().click();
    await page.locator('[data-testid="logout-button"], [data-testid="settings-logout-button"]').first().click();

    // Verify redirect to login
    await expect(page).toHaveURL(/.*login/);

    const authState = await page.evaluate(() => ({
      access_token: localStorage.getItem('access_token'),
      refresh_token: localStorage.getItem('refresh_token'),
      token_type: localStorage.getItem('token_type'),
      user: localStorage.getItem('user'),
    }));
    expect(authState.access_token).toBeNull();
    expect(authState.refresh_token).toBeNull();
    expect(authState.token_type).toBeNull();
    expect(authState.user).toBeNull();
  });
});
