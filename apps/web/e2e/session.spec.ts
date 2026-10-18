import { test, expect, type Page } from '@playwright/test';
import { LoginPage } from './fixtures/page-objects/LoginPage';
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

  test('removing both tokens requires login', async ({ page }) => {
    await loginAndWaitForAuthenticatedRoute(page);

    // Removing both credentials leaves no session that can be refreshed.
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

    const originalTokens = await page.evaluate(() => ({
      accessToken: localStorage.getItem('access_token'),
      refreshToken: localStorage.getItem('refresh_token'),
    }));
    expect(originalTokens.accessToken).toBeTruthy();
    expect(originalTokens.refreshToken).toBeTruthy();

    const rejectedAccessToken = 'e2e-rejected-access-token';

    await page.evaluate((accessToken) => {
      localStorage.setItem('access_token', accessToken);
      localStorage.removeItem('auth_validated_at');
    }, rejectedAccessToken);

    const rejectedValidation = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'GET' && url.pathname === '/api/auth/me';
    });
    const refreshRequest = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'POST' && url.pathname === '/api/auth/refresh';
    });

    await page.goto('/dashboard/projects', { waitUntil: 'domcontentloaded' });

    expect((await rejectedValidation).status()).toBe(401);
    const refreshResponse = await refreshRequest;
    expect(refreshResponse.ok()).toBeTruthy();
    expect(refreshResponse.request().postDataJSON()).toEqual({
      refresh_token: originalTokens.refreshToken,
    });

    const refreshed = await refreshResponse.json() as {
      access_token: string;
      refresh_token: string;
    };
    expect(refreshed.access_token).toBeTruthy();
    expect(refreshed.refresh_token).toBeTruthy();
    expect(refreshed.access_token).not.toBe(rejectedAccessToken);

    await expect.poll(() => page.evaluate(() => ({
      accessToken: localStorage.getItem('access_token'),
      refreshToken: localStorage.getItem('refresh_token'),
    }))).toEqual({
      accessToken: refreshed.access_token,
      refreshToken: refreshed.refresh_token,
    });
    await expect(page).toHaveURL('/dashboard/projects');
    await expect(page.locator('[data-testid="dashboard-user-panel-toggle"], [data-testid="user-menu-button"]').first()).toBeVisible();
    await expect(page.locator('#identifier')).toHaveCount(0);
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
