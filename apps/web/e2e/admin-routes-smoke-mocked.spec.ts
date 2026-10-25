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

    if (request.method() === 'GET' && pathname.endsWith('/api/admin/usage/models')) {
      const source = new URL(request.url()).searchParams.get('source');
      const distribution = (p50: number | null, p95: number | null, samples: number) => ({ p50, p95, samples });
      const row = {
        attempts: 10, success: 4, error: 3, timeout: 1, cancelled: 1, not_recorded: 1, success_rate: 0.5,
        latency_ms: distribution(5000, 20000, 4), ttft_ms: distribution(850, 4000, 3),
        decode_tps: { value: 100, samples: 2 },
        cache_hit_tokens: 0, cache_miss_tokens: 400, output_tokens: 3000, cost_cny: '0.0120',
      };
      const routerRow = {
        ...row, attempts: 1, success: 1, error: 0, timeout: 0, cancelled: 0, not_recorded: 0, success_rate: 1,
        latency_ms: distribution(900, 900, 1), ttft_ms: distribution(null, null, 0), decode_tps: { value: null, samples: 0 },
      };
      // A feature with no calls in the window: no rows, N/A totals.
      const items = source === 'polish' ? [] : [{ model: 'deepseek-flash', ...row }, { model: 'MiniMax-M3.1-Flash', ...routerRow }];
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          window: 'today',
          ...usagePeriod,
          source,
          items,
          totals: items.length ? { ...row, attempts: 11, success: 5, success_rate: 0.5556 } : { ...row, attempts: 0, success_rate: null },
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

test('usage page shows per-model call metrics, filters by feature and explains each metric', async ({ page }) => {
  await bootstrapAdminSession(page);
  const requests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/admin/usage/models')) requests.push(new URL(request.url()).search);
  });

  await page.goto('/admin/usage');
  const section = page.locator('section').filter({ has: page.getByRole('heading', { name: /^(按模型|By model)$/ }) });
  const deepseek = section.getByRole('row').filter({ hasText: 'deepseek-flash' });
  await expect(deepseek).toContainText('50%');
  await expect(deepseek).toContainText('4 / 8');
  await expect(deepseek).toContainText(/100 tokens\/(秒|s)/);
  // Not streamed: first token and decode speed are N/A, not 0.
  await expect(section.getByRole('row').filter({ hasText: 'MiniMax-M3.1-Flash' })).toContainText('N/A');
  await expect(section.getByRole('row').filter({ hasText: /全部模型|All models/ })).toContainText('55.6%');

  await section.getByRole('combobox', { name: /^(功能|Feature)$/ }).selectOption('polish');
  await expect(section).toContainText(/这段时间没有模型调用|No model calls in this period/);
  expect(requests.at(-1)).toBe('?window=today&source=polish');

  await section.getByText(/指标怎么算|How these are counted/).click();
  await expect(section).toContainText(/成功率 = 成功 ÷|Success rate = succeeded ÷/);
  await expect(section).toContainText(/解码速度 = 输出 tokens 合计|Decode speed = total output tokens/);
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
