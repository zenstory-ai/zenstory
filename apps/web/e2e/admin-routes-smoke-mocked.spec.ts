import { expect, test, type Page } from '@playwright/test';

const adminUser = {
  id: 'admin-1',
  username: 'admin',
  email: 'admin@example.com',
  email_verified: true,
  is_active: true,
  is_superuser: true,
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-03-01T00:00:00Z',
};

async function bootstrapAdminSession(page: Page) {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(adminUser),
    });
  });

  await page.route('**/api/auth/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'mock-access-token',
        refresh_token: 'mock-refresh-token',
        user: adminUser,
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

  await page.route('**/api/admin/**', async (route) => {
    const request = route.request();
    const { pathname } = new URL(request.url());

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/prompts')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [] }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/plans')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    });
  });

  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'mock-access-token');
    localStorage.setItem('refresh_token', 'mock-refresh-token');
    localStorage.setItem('token_type', 'bearer');
    localStorage.setItem('user', JSON.stringify(user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, adminUser);
}

test('all admin routes are reachable for superuser with mocked APIs', async ({ page }) => {
  await bootstrapAdminSession(page);

  const routes = [
    '/admin',
    '/admin/users',
    '/admin/prompts',
    '/admin/prompts/new',
    '/admin/skills',
    '/admin/codes',
    '/admin/subscriptions',
    '/admin/plans',
    '/admin/audit-logs',
    '/admin/inspirations',
    '/admin/feedback',
    '/admin/points',
    '/admin/check-in',
    '/admin/referrals',
    '/admin/quota',
  ];

  for (const route of routes) {
    await page.goto(route);
    await expect(page).toHaveURL(new RegExp(route.replace('/', '\\/')));
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator('main')).toBeVisible();
  }
});
