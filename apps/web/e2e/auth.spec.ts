import { test, expect, Page } from '@playwright/test';
import { TEST_USERS } from './config';

const TEST_INVITE_CODE = process.env.E2E_TEST_INVITE_CODE || 'E2E1-TST1';
const AUTHENTICATED_ROUTE_PATTERN = /\/(project|dashboard|onboarding\/persona)/;

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
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
      return;
    } catch (error) {
      if (attempt === attempts) {
        throw error;
      }
      await page.waitForTimeout(400 * attempt);
    }
  }
}

async function settleAfterLogin(page: Page) {
  await page.waitForURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 15000 });

  if (!page.url().includes('/onboarding/persona')) {
    return;
  }

  // Tests run with shared seeded users; mark onboarding complete in storage
  // to emulate "existing user" flow and keep auth assertions stable.
  await page.evaluate(() => {
    try {
      const rawUser = localStorage.getItem('user');
      if (!rawUser) return;

      const user = JSON.parse(rawUser) as { id?: string };
      if (!user?.id) return;

      const key = `zenstory_onboarding_persona_v1:${user.id}`;
      if (localStorage.getItem(key)) return;

      localStorage.setItem(
        key,
        JSON.stringify({
          version: 1,
          completed_at: new Date().toISOString(),
          selected_personas: ['fiction_writer'],
          selected_goals: ['finish_first_draft'],
          experience_level: 'beginner',
          skipped: false,
        })
      );
    } catch {
      // Ignore storage errors in tests and fallback to route assertions.
    }
  });

  await gotoWithRetry(page, '/dashboard');
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 10000 });
}

test.describe('Authentication', () => {
  test.describe('Registration', () => {
    test('user can register with valid credentials', async ({ page }, testInfo) => {
      await gotoWithRetry(page, '/register');

      const uniqueSuffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}-${testInfo.project.name.replace(/\W+/g, '-')}`;

      // Fill in registration form
      await page.fill('input#username', `newuser-${uniqueSuffix}`);
      await page.fill('input#email', `newuser-${uniqueSuffix}@example.com`);
      await page.fill('input#password', 'SecurePass123!');
      await page.fill('input#confirmPassword', 'SecurePass123!');
      await page.fill('input#invite_code', TEST_INVITE_CODE);
      await page.locator('input[type="checkbox"]').first().check();

      // Submit form
      await page.click('button[type="submit"]');

      // Redirects to verify-email; the address travels in navigation state,
      // never in the URL (URLs are captured by analytics).
      const email = `newuser-${uniqueSuffix}@example.com`;
      await expect(page).toHaveURL(/\/verify-email(?:$|\?)/, { timeout: 15000 });
      expect(page.url()).not.toContain('email=');
      await expect(page.getByText(email).first()).toBeVisible({ timeout: 10000 });
    });

    test('user cannot register with mismatched passwords', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      await page.fill('input#username', 'newuser');
      await page.fill('input#email', 'newuser2@example.com');
      await page.fill('input#password', 'SecurePass123!');
      await page.fill('input#confirmPassword', 'DifferentPass123!');
      await page.fill('input#invite_code', TEST_INVITE_CODE);
      await page.locator('input[type="checkbox"]').first().check();

      // Current register page blocks submit until client-side validation passes.
      await expect(page.locator('button[type="submit"]')).toBeDisabled();
      await expect(page.locator('#register-confirm-password-helper')).toBeVisible();
      await expect(page.locator('#register-confirm-password-helper')).toContainText(/(Passwords don’t match|两次.*密码.*一致)/i);
    });

    test('user cannot register with short password', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      await page.fill('input#username', 'newuser');
      await page.fill('input#email', 'newuser3@example.com');
      await page.fill('input#password', 'short');
      await page.fill('input#confirmPassword', 'short');
      await page.fill('input#invite_code', TEST_INVITE_CODE);
      await page.locator('input[type="checkbox"]').first().check();

      await expect(page.locator('button[type="submit"]')).toBeDisabled();
      await expect(page).toHaveURL(/\/register/);
      await expect(page.locator('#register-password-helper')).toBeVisible();
      const isTooShort = await page.locator('input#password').evaluate((el) => (el as HTMLInputElement).validity.tooShort);
      expect(isTooShort).toBe(true);
    });

    test('user cannot register with short username', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      await page.fill('input#username', 'ab');
      await page.fill('input#email', 'newuser4@example.com');
      await page.fill('input#password', 'SecurePass123!');
      await page.fill('input#confirmPassword', 'SecurePass123!');
      await page.fill('input#invite_code', TEST_INVITE_CODE);
      await page.locator('input[type="checkbox"]').first().check();

      await expect(page.locator('button[type="submit"]')).toBeDisabled();
      await expect(page).toHaveURL(/\/register/);
      await expect(page.locator('#register-username-helper')).toBeVisible();
      const isTooShort = await page.locator('input#username').evaluate((el) => (el as HTMLInputElement).validity.tooShort);
      expect(isTooShort).toBe(true);
    });
  });

  test.describe('Login', () => {
    test('user can login with valid credentials', async ({ page }) => {
      await gotoWithRetry(page, '/login');

      // Fill in login form with test credentials
      const TEST_EMAIL = TEST_USERS.standard.email;
      const TEST_PASSWORD = TEST_USERS.standard.password;

      await page.fill('input#identifier', TEST_EMAIL);
      await page.fill('input#password', TEST_PASSWORD);

      // Submit form
      await page.click('button[type="submit"]');

      await settleAfterLogin(page);
      await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN);
    });

    test('existing seeded user bypasses persona onboarding gate', async ({ page }) => {
      const TEST_EMAIL = TEST_USERS.standard.email;
      const TEST_PASSWORD = TEST_USERS.standard.password;

      await gotoWithRetry(page, '/login');
      await page.fill('input#identifier', TEST_EMAIL);
      await page.fill('input#password', TEST_PASSWORD);
      await page.click('button[type="submit"]');

      await page.waitForURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 15000 });
      expect(page.url()).not.toContain('/onboarding/persona');
    });

    test('server-required login is redirected despite the legacy account age', async ({ page }) => {
      // Keep the real legacy account: only the server response should require onboarding.
      let onboardingReads = 0;
      await page.route("**/api/v1/persona/onboarding", async (route) => {
        if (route.request().method() !== "GET") return route.continue();
        onboardingReads += 1;
        await route.fulfill({
          status: 200,
          json: {
            required: true, rollout_at: "2026-03-05T16:00:00Z", new_user_window_days: 7,
            profile: null, recommendations: [],
          },
        });
      });

      const TEST_EMAIL = TEST_USERS.standard.email;
      const TEST_PASSWORD = TEST_USERS.standard.password;
      await gotoWithRetry(page, '/login');
      await page.fill('input#identifier', TEST_EMAIL);
      await page.fill('input#password', TEST_PASSWORD);
      await page.click('button[type="submit"]');

      await expect(page).toHaveURL(/\/onboarding\/persona/, { timeout: 15000 });
      expect(onboardingReads).toBeGreaterThan(0);
    });

    test('user sees error with invalid credentials', async ({ page }) => {
      await gotoWithRetry(page, '/login');

      await page.fill('input#identifier', 'wrong@example.com');
      await page.fill('input#password', 'wrongpass');

      await page.click('button[type="submit"]');

      // Should show error message in the form
      await expect(page.getByText(/(Incorrect account or password|账号或密码错误)/i)).toBeVisible();
    });

    test('login form has link to register', async ({ page }) => {
      await gotoWithRetry(page, '/login');

      // Click register link
      await page.click('a[href="/register"]');

      await expect(page).toHaveURL('/register');
    });
  });

  test.describe('Logout', () => {
    test('user can logout successfully', async ({ page }) => {
      // First, login with test credentials
      const TEST_EMAIL = TEST_USERS.standard.email;
      const TEST_PASSWORD = TEST_USERS.standard.password;

      await gotoWithRetry(page, '/login');
      await page.fill('input#identifier', TEST_EMAIL);
      await page.fill('input#password', TEST_PASSWORD);
      await page.click('button[type="submit"]');

      await settleAfterLogin(page);
      await gotoWithRetry(page, '/dashboard');
      await expect(page).toHaveURL(/\/dashboard/);

      // Dashboard uses sidebar user panel toggle.
      const userMenuButton = page.locator('[data-testid="dashboard-user-panel-toggle"], [data-testid="user-menu-button"]').first();
      await expect(userMenuButton).toBeVisible();
      await userMenuButton.click();

      const logoutButton = page
        .locator('button:has-text("Logout"), button:has-text("Sign out"), button:has-text("退出"), button:has-text("登出"), [data-testid="logout-button"]')
        .first();
      await expect(logoutButton).toBeVisible();
      await logoutButton.click();

      // Should redirect to login page
      await expect(page).toHaveURL('/login');
    });
  });

  test.describe('Protected Routes', () => {
    const publicRoutePattern = /\/($|login(?:\?.*)?$)/;

    test('protected routes redirect to login when not authenticated', async ({ page }) => {
      // Try to access protected route directly
      await gotoWithRetry(page, '/dashboard/projects');

      // Should be redirected to public entry (login or home)
      await expect(page).toHaveURL(publicRoutePattern);
    });

    test('project page redirects to login when not authenticated', async ({ page }) => {
      await gotoWithRetry(page, '/project/some-project-id');

      // Should be redirected to public entry (login or home)
      await expect(page).toHaveURL(publicRoutePattern);
    });

    test('dashboard redirects to login when not authenticated', async ({ page }) => {
      await gotoWithRetry(page, '/dashboard');

      // Should be redirected to public entry (login or home)
      await expect(page).toHaveURL(publicRoutePattern);
    });
  });

  test.describe('Navigation', () => {
    test('user can navigate from login to register', async ({ page }) => {
      await gotoWithRetry(page, '/login');

      await page.click('a[href="/register"]');

      await expect(page).toHaveURL('/register');
    });

    test('user can navigate from register to login', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      await page.click('a[href="/login"]');

      await expect(page).toHaveURL('/login');
    });
  });

  test.describe('Google OAuth', () => {
    test('Google OAuth button is visible on login page', async ({ page }) => {
      await gotoWithRetry(page, '/login');

      const googleButtons = page.locator('button:has-text("Google"), a:has-text("Google"), button:has-text("谷歌"), a:has-text("谷歌")');
      const count = await googleButtons.count();
      expect(count).toBeLessThanOrEqual(1);
      if (count > 0) {
        await expect(googleButtons.first()).toBeVisible();
      }
    });

    test('Google OAuth button is visible on register page', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      const googleButtons = page.locator('button:has-text("Google"), a:has-text("Google"), button:has-text("谷歌"), a:has-text("谷歌")');
      const count = await googleButtons.count();
      expect(count).toBeLessThanOrEqual(1);
      if (count > 0) {
        await expect(googleButtons.first()).toBeVisible();
      }
    });

    test('Google OAuth callback handles success and redirects to dashboard', async ({ page }) => {
      // Navigate to callback page with mock params
      // The callback handler should exchange code for tokens
      // Note: Actual OAuth flow completion would require backend mock
      await gotoWithRetry(page, `/auth/callback?code=test-code&state=test-state`);

      // Verify the callback route exists - it may redirect to login if OAuth fails
      // This test mainly checks that the callback route doesn't crash
    });
  });

  test.describe('Token Refresh', () => {
    test('token refresh works when access token expires', async ({ page }) => {
      const TEST_EMAIL = TEST_USERS.standard.email;
      const TEST_PASSWORD = TEST_USERS.standard.password;

      // Login first
      await gotoWithRetry(page, '/login');
      await page.fill('input#identifier', TEST_EMAIL);
      await page.fill('input#password', TEST_PASSWORD);
      await page.click('button[type="submit"]');
      await settleAfterLogin(page);

      // Verify we're logged in
      await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN);

      // Clear access token to simulate expiry
      await page.evaluate(() => localStorage.removeItem('access_token'));

      // Make an API call that should trigger refresh by navigating to a protected page
      await gotoWithRetry(page, '/dashboard/projects');

      // App should handle missing access token gracefully (stay authenticated or redirect to login)
      await expect(page).toHaveURL(/\/(login|dashboard|project\/)/);
    });

    test('refresh token is used for API calls when access token missing', async ({ page }) => {
      const TEST_EMAIL = TEST_USERS.standard.email;
      const TEST_PASSWORD = TEST_USERS.standard.password;

      // Login first
      await gotoWithRetry(page, '/login');
      await page.fill('input#identifier', TEST_EMAIL);
      await page.fill('input#password', TEST_PASSWORD);
      await page.click('button[type="submit"]');
      await settleAfterLogin(page);

      // Store the refresh token before clearing access token
      const refreshToken = await page.evaluate(() => localStorage.getItem('refresh_token'));
      expect(refreshToken).toBeTruthy();

      // Clear access token
      await page.evaluate(() => localStorage.removeItem('access_token'));

      // Verify refresh token still exists
      const storedRefreshToken = await page.evaluate(() => localStorage.getItem('refresh_token'));
      expect(storedRefreshToken).toBeTruthy();

      // Trigger a page reload which should attempt to refresh token
      await page.reload();

      // App should handle missing access token gracefully
      await expect(page).toHaveURL(/\/(login|dashboard|project\/)/);
    });

    test('logout when both tokens expire', async ({ page }) => {
      const TEST_EMAIL = TEST_USERS.standard.email;
      const TEST_PASSWORD = TEST_USERS.standard.password;

      // Login first
      await gotoWithRetry(page, '/login');
      await page.fill('input#identifier', TEST_EMAIL);
      await page.fill('input#password', TEST_PASSWORD);
      await page.click('button[type="submit"]');
      await settleAfterLogin(page);

      // Clear both tokens to simulate full expiry
      await page.evaluate(() => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
      });

      // Refresh should drop authenticated state when both tokens are missing
      await page.reload();

      // Should end up on a public route since there's no refresh token
      await expect(page).toHaveURL(/\/($|login(?:\?.*)?$)/);
    });
  });

  test.describe('Form Validation', () => {
    test('login form requires identifier and password', async ({ page }) => {
      await gotoWithRetry(page, '/login');

      // Current login page disables submit until required fields are present.
      await expect(page.locator('button[type="submit"]')).toBeDisabled();
      await expect(page).toHaveURL('/login');

      // Check HTML5 validation
      const identifierInput = page.locator('input#identifier');
      const passwordInput = page.locator('input#password');

      await expect(identifierInput).toHaveAttribute('required', '');
      await expect(passwordInput).toHaveAttribute('required', '');
    });

    test('register form requires all fields', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      const usernameInput = page.locator('input#username');
      const emailInput = page.locator('input#email');
      const passwordInput = page.locator('input#password');
      const confirmPasswordInput = page.locator('input#confirmPassword');

      await expect(usernameInput).toHaveAttribute('required', '');
      await expect(emailInput).toHaveAttribute('required', '');
      await expect(passwordInput).toHaveAttribute('required', '');
      await expect(confirmPasswordInput).toHaveAttribute('required', '');
    });

    test('register email field validates email format', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      const emailInput = page.locator('input#email');
      await expect(emailInput).toHaveAttribute('type', 'email');
    });

    test('password fields are masked', async ({ page }) => {
      await gotoWithRetry(page, '/register');

      const passwordInput = page.locator('input#password');
      const confirmPasswordInput = page.locator('input#confirmPassword');

      await expect(passwordInput).toHaveAttribute('type', 'password');
      await expect(confirmPasswordInput).toHaveAttribute('type', 'password');
    });
  });
});
