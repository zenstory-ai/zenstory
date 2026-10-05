import { test, expect } from '@playwright/test';

async function bootstrapSession(page: import('@playwright/test').Page, user: {
  id: string;
  username: string;
  email: string;
  email_verified: boolean;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  updated_at: string;
}) {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(user),
    });
  });

  await page.route('**/api/auth/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'mock-access-token',
        refresh_token: 'mock-refresh-token',
        user,
      }),
    });
  });

  await page.route('**/api/v1/projects**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  // The materials library checks the plan before requesting the summary.
  await page.route('**/api/v1/subscription/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tier: 'pro',
        status: 'active',
        display_name: 'Pro',
        display_name_en: 'Pro',
        current_period_end: null,
        days_remaining: null,
        features: { materials_library_access: true },
      }),
    });
  });

  await page.route('**/api/v1/materials/library-summary**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.addInitScript((sessionUser) => {
    localStorage.setItem('access_token', 'mock-access-token');
    localStorage.setItem('refresh_token', 'mock-refresh-token');
    localStorage.setItem('token_type', 'bearer');
    localStorage.setItem('user', JSON.stringify(sessionUser));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, user);
}

test.describe('Admin route guard', () => {
  test('redirects unauthenticated users from admin pages to login', async ({ page }) => {
    await page.goto('/admin/users');
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 });
    await expect(page.getByRole('button', { name: /登录|log in|login/i }).first()).toBeVisible();
  });

  test('shows permission denied page for authenticated non-admin user', async ({ page }) => {
    const nonAdminUser = {
      id: 'user-1',
      username: 'writer',
      email: 'writer@example.com',
      email_verified: true,
      is_active: true,
      is_superuser: false,
      created_at: '2026-03-01T00:00:00Z',
      updated_at: '2026-03-01T00:00:00Z',
    };

    await bootstrapSession(page, nonAdminUser);

    await page.goto('/admin');
    await expect(page).not.toHaveURL(/\/login/, { timeout: 10000 });
    await expect(page.getByText(/权限不足|Insufficient Permission|admin\.insufficientPermission/i)).toBeVisible();
  });
});
