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
  await page.route('**/api/v1/subscription/me', async (route) => {
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

  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'mock-access-token');
    localStorage.setItem('refresh_token', 'mock-refresh-token');
    localStorage.setItem('token_type', 'bearer');
    localStorage.setItem('user', JSON.stringify(user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, adminUser);
}

test.describe('Admin audit logs (mocked)', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    void page;
    test.skip(testInfo.project.name === 'mobile', 'Desktop-focused admin audit scenarios');
  });

  test('applies audit filters and requests next page with same query params', async ({ page }) => {
    await bootstrapAdminSession(page);

    const capturedQueries: Array<Record<string, string | null>> = [];

    await page.route('**/api/admin/audit-logs**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const queryRecord = {
        page: url.searchParams.get('page'),
        page_size: url.searchParams.get('page_size'),
        resource_type: url.searchParams.get('resource_type'),
        action: url.searchParams.get('action'),
      };
      capturedQueries.push(queryRecord);

      const pageNum = Number(queryRecord.page ?? '1');
      const size = pageNum === 1 ? 20 : 1;
      const items = Array.from({ length: size }, (_, index) => ({
        id: `log-${pageNum}-${index + 1}`,
        admin_id: 'admin-1',
        admin_name: pageNum === 1 ? 'admin-page-1' : 'admin-page-2',
        action: queryRecord.action || 'update',
        resource_type: queryRecord.resource_type || 'subscription',
        resource_id: `res-${pageNum}-${index + 1}`,
        details: 'changed fields',
        old_value: { before: 'v1' },
        new_value: { after: 'v2' },
        ip_address: '127.0.0.1',
        user_agent: 'playwright',
        created_at: '2026-03-08T00:00:00Z',
      }));

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items,
          total: 40,
          page: pageNum,
          page_size: 20,
        }),
      });
    });

    await page.goto('/admin/audit-logs');
    await expect(page.locator('table tbody tr', { hasText: 'admin-page-1' }).first()).toBeVisible();

    await page.locator('select').nth(0).selectOption('subscription');
    await page.locator('select').nth(1).selectOption('update_subscription');

    await expect.poll(() =>
      capturedQueries.some((query) =>
        query.page === '1'
        && query.page_size === '20'
        && query.resource_type === 'subscription'
        && query.action === 'update_subscription',
      ),
    ).toBe(true);

    await page.getByRole('button', { name: /下一页|下一步|next|common:next/i }).click();

    await expect.poll(() =>
      capturedQueries.some((query) =>
        query.page === '2'
        && query.page_size === '20'
        && query.resource_type === 'subscription'
        && query.action === 'update_subscription',
      ),
    ).toBe(true);

    await expect(page.locator('table tbody tr', { hasText: 'admin-page-2' }).first()).toBeVisible();
  });

  test('opens and closes audit log details modal', async ({ page }) => {
    await bootstrapAdminSession(page);

    await page.route('**/api/admin/audit-logs**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'log-1',
              admin_id: 'admin-1',
              admin_name: 'super-admin',
              action: 'update',
              resource_type: 'plan',
              resource_id: 'pro',
              details: 'changed subscription tier',
              old_value: { price_monthly_cents: 2900 },
              new_value: { price_monthly_cents: 3900 },
              ip_address: '127.0.0.1',
              user_agent: 'playwright',
              created_at: '2026-03-08T00:00:00Z',
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        }),
      });
    });

    await page.goto('/admin/audit-logs');
    await expect(page.locator('table tbody tr', { hasText: 'super-admin' }).first()).toBeVisible();

    await page
      .locator('button[title*="查看"], button[title*="details"], button[title*="auditLogs.viewDetails"]')
      .first()
      .click();

    const detailModal = page.locator('.fixed.inset-0.z-50').last();
    await expect(detailModal).toBeVisible();
    await expect(detailModal.getByText(/3900/)).toBeVisible();

    await detailModal.getByRole('button', { name: /^关闭$|^close$|^common:close$/i }).first().click();
    await expect(page.locator('.fixed.inset-0.z-50')).toHaveCount(0);
  });
});
