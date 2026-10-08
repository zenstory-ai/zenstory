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

const growthMetrics = {
  new_users: 43,
  ai_active_users: 31,
  cohort_activated_users: 19,
  cohort_activation_rate: 0.4419,
  paid_orders: 9,
  revenue_cents: 123456,
  paid_users: 8,
  cohort_paid_users: 5,
  signup_to_paid_rate: 0.1163,
  grant_upgrade_events: 4,
  grant_upgrade_users: 3,
  grant_channels: [{ channel: 'admin_update', events: 4, users: 3 }],
};

const growthDashboard = {
  days: 7,
  timezone: 'Asia/Shanghai',
  current: {
    period_start: '2026-03-01T00:00:00Z',
    period_end: '2026-03-08T00:00:00Z',
    metrics: growthMetrics,
  },
  previous: {
    period_start: '2026-02-22T00:00:00Z',
    period_end: '2026-03-01T00:00:00Z',
    metrics: {
      ...growthMetrics,
      new_users: 35,
      ai_active_users: 24,
      paid_orders: 6,
      revenue_cents: 84500,
      paid_users: 6,
    },
  },
  daily: [{
    date: '2026-03-07',
    ...growthMetrics,
    new_users: 17,
    ai_active_users: 13,
    paid_orders: 4,
    revenue_cents: 32109,
  }],
  definitions: {
    window: 'Beijing-time half-open reporting window ending at the request cutoff.',
    activation: 'Signup cohort with a live model call observed by the period cutoff.',
    paid: 'Confirmed paid orders; pending, refunded, and granted upgrades are excluded.',
    grants: 'Non-payment subscription upgrades grouped separately from revenue.',
  },
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

  await page.route('**/api/admin/**', async (route) => {
    const request = route.request();
    const { pathname } = new URL(request.url());

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/dashboard/growth')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(growthDashboard),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/prompts')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [] }),
      });
      return;
    }

    const usageMetrics = {
      calls: 2,
      cache_hit_tokens: 1000,
      cache_miss_tokens: 200,
      output_tokens: 50,
      cost_cny: '0.0010',
      peak_cost_cny: '0.0000',
      offpeak_cost_cny: '0.0010',
    };
    const usagePeriod = {
      timezone: 'Asia/Shanghai',
      period_start: '2026-10-07',
      period_end: '2026-10-07',
      pricing_version: 'deepseek-flash-2026-10',
      prices: {
        peak: { cache_hit: '0.04', cache_miss: '2', output: '8' },
        offpeak: { cache_hit: '0.02', cache_miss: '1', output: '4' },
      },
    };

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/usage/summary')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window: 'today',
          ...usagePeriod,
          totals: { users: 1, ...usageMetrics },
          by_source: [{ source: 'agent', ...usageMetrics }],
          daily: [{ date: '2026-10-07', users: 1, ...usageMetrics }],
        }),
      });
      return;
    }

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/usage/users')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window: 'today',
          ...usagePeriod,
          items: [{
            user_id: 'user-1',
            username: 'writer',
            email: 'writer@example.com',
            last_used_at: '2026-10-07T01:00:00Z',
            ...usageMetrics,
          }],
          total: 1,
          page: 1,
          page_size: 20,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && /\/api\/admin\/usage\/users\/[^/]+\/daily$/.test(pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 'user-1',
          username: 'writer',
          email: 'writer@example.com',
          days: 7,
          ...usagePeriod,
          totals: usageMetrics,
          by_source: [{ source: 'agent', ...usageMetrics }],
          daily: [{ date: '2026-10-07', ...usageMetrics }],
        }),
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
    '/admin/usage',
    '/admin/users/user-1',
    '/admin/prompts',
    '/admin/prompts/new',
    '/admin/skills',
    '/admin/codes',
    '/admin/subscriptions',
    '/admin/payment-orders',
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

test('usage page opens a user from a ?user= link', async ({ page }) => {
  await bootstrapAdminSession(page);

  await page.goto('/admin/usage?user=user-1');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('writer@example.com');
});

test('user detail page links to usage and lists every section', async ({ page }) => {
  await bootstrapAdminSession(page);

  await page.goto('/admin/users/user-1');

  await expect(page.locator('main')).toBeVisible();
  await expect(page.getByRole('link', { name: /查看用量|View usage/ })).toHaveAttribute('href', '/admin/usage?user=user-1');
  for (const section of [/^(账号|Account)$/, /^(会员|Membership)$/, /^(配额|Quota)$/, /^(积分|Points)$/, /^(支付订单|Payment orders)$/]) {
    await expect(page.getByRole('heading', { name: section })).toBeVisible();
  }
});
