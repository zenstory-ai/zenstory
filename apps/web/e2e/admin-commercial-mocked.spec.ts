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

  // ProtectedProviders mount ProjectProvider + MaterialLibraryProvider even on /admin routes.
  // Provide benign defaults to avoid background 401 -> auto logout while the page loads.
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

  // Code tiers come from the plan catalog; tests that need other plans route this again.
  await page.route('**/api/admin/plans**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 'plan-free', name: 'free', display_name: '免费版', display_name_en: 'Free', price_monthly_cents: 0, price_yearly_cents: 0, features: {}, is_active: true },
        { id: 'plan-pro', name: 'pro', display_name: '专业版', display_name_en: 'Pro', price_monthly_cents: 2900, price_yearly_cents: 29000, features: {}, is_active: true },
      ]),
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

test.describe('Admin commercial flows (mocked)', () => {
  test('can toggle redemption code status', async ({ page }) => {
    await bootstrapAdminSession(page);

    let updatePayload: Record<string, unknown> | null = null;

    await page.route('**/api/admin/codes**', async (route) => {
      const request = route.request();
      if (request.method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [
              {
                id: 'code-1',
                code: 'E2E-CODE',
                tier: 'pro',
                duration_days: 30,
                code_type: 'single_use',
                max_uses: 1,
                current_uses: 0,
                is_active: true,
                notes: null,
                created_at: '2026-03-08T00:00:00Z',
                updated_at: '2026-03-08T00:00:00Z',
              },
            ],
            total: 1,
            page: 1,
            page_size: 20,
          }),
        });
        return;
      }

      if (request.method() === 'PUT') {
        updatePayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'code-1',
            code: 'E2E-CODE',
            tier: 'pro',
            duration_days: 30,
            code_type: 'single_use',
            max_uses: 1,
            current_uses: 0,
            is_active: false,
            notes: null,
            created_at: '2026-03-08T00:00:00Z',
            updated_at: '2026-03-08T00:00:00Z',
          }),
        });
        return;
      }

      await route.continue();
    });

    await page.goto('/admin/codes');
    await expect(page.locator('table').first().getByText('E2E-CODE')).toBeVisible();

    const row = page.locator('table tbody tr', { hasText: 'E2E-CODE' }).first();
    await row.locator('button').last().click();
    const confirmation = page.getByRole('dialog');
    await expect(confirmation).toBeVisible();
    expect(updatePayload).toBeNull();
    await confirmation.getByRole('button', { name: /^(禁用|Deactivate)$|codes\.confirmDeactivate/i }).click();

    await expect.poll(() => updatePayload).not.toBeNull();
    expect(updatePayload).toEqual({ is_active: false });
  });

  test('batch-creates multi-use codes with a use limit and shows them', async ({ page }) => {
    await bootstrapAdminSession(page);

    let batchPayload: Record<string, unknown> | null = null;
    await page.route('**/api/admin/codes**', async (route) => {
      const request = route.request();
      if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/codes/batch')) {
        batchPayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ created: 2, count: 2, codes: ['ERG-PRO-AAAA-11111111', 'ERG-PRO-BBBB-22222222'], code_type: 'multi_use', max_uses: 25 }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
      });
    });

    await page.goto('/admin/codes');
    await page.getByRole('button', { name: /^(批量生成|批量创建|Batch create|Bulk create|codes\.batchCreate)/i }).first().click();
    const modal = page.locator('.fixed.inset-0.z-50').last();
    await modal.locator('select').nth(1).selectOption('multi_use');
    await modal.locator('#batch-max-uses').fill('25');
    await modal.locator('input[type="number"]').nth(1).fill('2');
    await modal.getByRole('button', { name: /^(批量生成|批量创建|Batch create|Bulk create|codes\.batchCreate)/i }).click();

    await expect.poll(() => batchPayload).not.toBeNull();
    expect(batchPayload).toMatchObject({ tier: 'pro', code_type: 'multi_use', max_uses: 25, count: 2 });
    const result = page.getByRole('dialog');
    await expect(result).toBeVisible();
    await expect(result.getByRole('textbox')).toHaveValue('ERG-PRO-AAAA-11111111\nERG-PRO-BBBB-22222222');
  });

  test('can submit subscription duration update', async ({ page }) => {
    await bootstrapAdminSession(page);

    let updatePayload: Record<string, unknown> | null = null;

    await page.route('**/api/admin/subscriptions**', async (route) => {
      const request = route.request();

      if (request.method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [
              {
                id: 'sub-1',
                user_id: 'user-1',
                username: 'writer',
                email: 'writer@example.com',
                plan_name: 'pro',
                plan_display_name: 'Pro',
                status: 'active',
                current_period_start: '2026-03-01T00:00:00Z',
                current_period_end: '2026-04-01T00:00:00Z',
                created_at: '2026-03-01T00:00:00Z',
                updated_at: '2026-03-01T00:00:00Z',
                has_subscription_record: true,
              },
            ],
            total: 1,
            page: 1,
            page_size: 20,
          }),
        });
        return;
      }

      if (request.method() === 'PUT') {
        updatePayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: true }),
        });
        return;
      }

      await route.continue();
    });

    await page.route('**/api/admin/plans**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 'plan-pro',
            name: 'pro',
            display_name: 'Pro',
            display_name_en: 'Pro',
            price_monthly_cents: 1999,
            price_yearly_cents: 19999,
            features: {},
            is_active: true,
          },
        ]),
      });
    });

    await page.goto('/admin/subscriptions');
    const row = page.locator('table tbody tr', { hasText: 'writer@example.com' }).first();
    await expect(row).toBeVisible();

    await row.locator('button').nth(1).click();

    await page.locator('input[type="number"]').fill('30');
    await page.locator('.fixed.inset-0.z-50').last().locator('button').last().click();

    await expect.poll(() => updatePayload).not.toBeNull();
    expect(updatePayload).toEqual({
      plan_name: 'pro',
      duration_days: 30,
    });
  });

  test('can generate referral invite code and view rewards tab', async ({ page }) => {
    await bootstrapAdminSession(page);

    let generateCalled = 0;

    await page.route('**/api/admin/referrals/stats', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_codes: 8,
          active_codes: 4,
          total_referrals: 12,
          successful_referrals: 6,
          pending_rewards: 2,
          total_points_awarded: 300,
        }),
      });
    });

    await page.route('**/api/admin/invites**', async (route) => {
      const request = route.request();
      if (request.method() === 'POST') {
        generateCalled += 1;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'invite-new',
            code: 'NEWCODE',
            owner_id: 'admin-1',
            owner_name: 'admin',
            max_uses: 10,
            current_uses: 0,
            is_active: true,
            expires_at: null,
            created_at: '2026-03-08T00:00:00Z',
          }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'invite-1',
              code: 'REF-CODE',
              owner_id: 'user-1',
              owner_name: 'alice',
              max_uses: 5,
              current_uses: 2,
              is_active: true,
              expires_at: null,
              created_at: '2026-03-08T00:00:00Z',
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        }),
      });
    });

    await page.route('**/api/admin/referrals/rewards**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'reward-1',
              user_id: 'user-2',
              username: 'bob',
              reward_type: 'points',
              amount: 50,
              source: 'invite',
              is_used: false,
              expires_at: null,
              created_at: '2026-03-08T00:00:00Z',
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        }),
      });
    });

    await page.goto('/admin/referrals');
    await expect(page.locator('table').first().getByText('REF-CODE')).toBeVisible();

    await page.getByRole('button', { name: /生成邀请码|generate invite code/i }).click();
    await expect.poll(() => generateCalled).toBe(1);

    await page.getByRole('button', { name: /奖励记录|reward records/i }).click();
    await expect(page.locator('table').first().getByText('bob')).toBeVisible();
  });
});
