import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveUser } from './fixtures-responsive';

const tiers = ['free', 'pro'].map(name => ({
  id: name, name, display_name: name === 'pro' ? 'Pro' : '免费版', display_name_en: name === 'pro' ? 'Pro' : 'Free',
  price_monthly_cents: name === 'pro' ? 4900 : 0, price_yearly_cents: name === 'pro' ? 39900 : 0,
  recommended: name === 'pro', summary_key: '', target_user_key: '',
  entitlements: { ai_conversations_per_day: name === 'pro' ? -1 : 3, active_projects_limit: name === 'pro' ? -1 : 3,
    material_decompositions_monthly: name === 'pro' ? 5 : 0, custom_skills_limit: name === 'pro' ? 20 : 3,
    file_versions_per_file: name === 'pro' ? 100 : 10, materials_library_access: name === 'pro' },
}));

async function setup(page: Page, tier: 'free' | 'pro' = 'free') {
  // All account, catalog and payment data are synthetic; never contact paid services.
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await mockResponsiveApp(page, null, tier);
  await page.route('**/api/v1/subscription/catalog', route => route.fulfill({ json: { version: 'test', tiers } }));
  await page.route('**/api/v1/subscription/me', route => route.fulfill({ json: {
    tier, display_name: tier === 'pro' ? 'Pro' : '免费版', status: 'active',
    current_period_end: tier === 'pro' ? '2027-10-17T00:00:00Z' : null, days_remaining: tier === 'pro' ? 365 : null,
  } }));
  await page.route('**/api/v1/subscription/quota', route => route.fulfill({ json: {
    ai_conversations: { used: 1, limit: tier === 'pro' ? -1 : 3 }, projects: { used: 1, limit: tier === 'pro' ? -1 : 3 },
    material_decompositions: { used: 0, limit: tier === 'pro' ? 5 : 0 }, skill_creates: { used: 1, limit: tier === 'pro' ? 20 : 3 },
  } }));
  await page.route('**/api/v1/payments/options', route => route.fulfill({ json: { enabled: true, payment_methods: ['alipay'] } }));
  await page.route('**/api/announcements/current', route => route.fulfill({ json: null }));
}

for (const theme of ['light', 'dark']) {
  test(`original billing layout keeps plan and usage visible with support-only footer (${theme})`, async ({ page }) => {
    await setup(page);
    await page.addInitScript(value => localStorage.setItem('zenstory-theme', value), theme);
    await page.setViewportSize({ width: 390, height: 844 });
    const orders: string[] = [];
    page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/payments/')) orders.push(request.url()); });
    await page.goto('/dashboard/billing');
    await expect(page.getByRole('heading', { name: '订阅权益', exact: true })).toBeVisible();
    await expect(page.getByText('当前套餐', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '当前用量', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '选一个适合你的方案', exact: true })).toBeVisible();
    await expect(page.getByTestId('billing-usage-ai_conversations')).toHaveText('1/3');
    expect(await page.getByText('推荐', { exact: true }).evaluate(element => {
      const style = getComputedStyle(element);
      return element.getBoundingClientRect().height <= Number.parseFloat(style.lineHeight)
        + Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom) + 1;
    })).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`billing-mobile-${theme}.png`) });
    await expect(page.getByRole('button', { name: '兑换码', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: '开通 Pro', exact: true }).click();
    const checkout = page.getByRole('dialog');
    await expect(checkout).toBeVisible();
    await expect(checkout.getByRole('button', { name: '去支付 ¥399' })).toBeVisible();
    await checkout.getByRole('radio', { name: /^月付/ }).click();
    await expect(checkout.getByRole('button', { name: '去支付 ¥49' })).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByTestId('billing-help').locator('summary').click();
    await expect(page.getByRole('button', { name: '兑换码', exact: true })).toHaveCount(0);
    await expect(page.getByTestId('billing-help').getByRole('link', { name: 'support@zenstory.ai' })).toBeVisible();
    expect(orders).toEqual([]);
    await page.keyboard.press('Escape');
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await expect(page.locator('main h2')).toHaveText(['选一个适合你的方案', '当前用量']);
      const comparisonTop = await page.getByRole('heading', { name: '选一个适合你的方案', exact: true }).evaluate(element => element.getBoundingClientRect().top);
      const usageTop = await page.getByRole('heading', { name: '当前用量', exact: true }).evaluate(element => element.getBoundingClientRect().top);
      expect(comparisonTop).toBeLessThan(usageTop);
      if (width === 1440) {
        await page.getByRole('heading', { level: 1 }).scrollIntoViewIfNeeded();
        await page.screenshot({ path: test.info().outputPath(`billing-desktop-${theme}.png`) });
      }
    }
  });
}

test('paid accounts lead with expiry, usage and renewal, not an acquisition offer', async ({ page }) => {
  await setup(page, 'pro');
  await page.goto('/dashboard/billing');
  await expect(page.getByRole('heading', { name: '订阅权益', exact: true })).toBeVisible();
  await expect(page.getByTestId('billing-pro-offer')).toHaveCount(0);
  await expect(page.getByText(/有效期至/)).toBeVisible();
  await expect(page.getByTestId('billing-usage-ai_conversations')).toBeVisible();
  await expect(page.locator('main h2')).toHaveText(['选一个适合你的方案', '当前用量']);
  await expect(page.getByRole('button', { name: '兑换码', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '续费 Pro', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '续费 Pro', exact: true })).toBeVisible();
});

test('an expiring Pro takes the banner slot and its renewal order is attributed to the reminder', async ({ page }) => {
  await setup(page, 'pro');
  await page.route('**/api/v1/subscription/me', route => route.fulfill({ json: {
    tier: 'pro', display_name: 'Pro', status: 'active', current_period_end: '2026-11-04T04:00:00Z', days_remaining: 3,
  } }));
  await page.route('**/api/announcements/current', route => route.fulfill({ json: {
    title: '写作指南更新', description: '新的写作指南已经上线。', action_label: null, action_url: null, version: 1, updated_at: '2026-10-17T00:00:00Z',
  } }));
  const orderBodies: Record<string, unknown>[] = [];
  await page.route('**/api/v1/payments/orders', route => {
    orderBodies.push(route.request().postDataJSON());
    return route.fulfill({ status: 503, json: { detail: 'synthetic', error_code: 'ERR_PAYMENT_UNAVAILABLE' } });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/dashboard');
  const reminder = page.getByRole('region', { name: 'Pro 到期提醒', exact: true });
  await expect(reminder.getByText('Pro 将于 2026/11/04 到期', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '站内公告', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: test.info().outputPath('renewal-reminder-mobile.png') });
  await reminder.getByRole('link', { name: '续费 Pro', exact: true }).click();
  const checkout = page.getByRole('dialog', { name: '续费 Pro', exact: true });
  await expect(checkout).toBeVisible();
  await checkout.getByRole('button', { name: /^去支付/ }).click();
  await expect.poll(() => orderBodies.length).toBe(1);
  expect(orderBodies[0]).toMatchObject({ upgrade_source: 'renewal_reminder' });
});

test('admin publishes one banner, dismissal persists, a new version reappears, and stopping preserves published content', async ({ page }) => {
  await setup(page);
  const admin = { ...responsiveUser, is_superuser: true };
  await page.addInitScript(value => localStorage.setItem('user', JSON.stringify(value)), admin);
  await page.route('**/api/auth/me', route => route.fulfill({ json: admin }));
  let writeSequence = 0;
  let current = { title: '', description: '', action_label: null as string | null, action_url: null as string | null,
    version: 0, enabled: false, updated_at: null as string | null, updated_by: null as string | null };
  await page.route('**/api/admin/announcements/current', async route => {
    if (route.request().method() === 'PUT') {
      const { expected_version, expected_updated_at, ...request } = route.request().postDataJSON();
      expect(expected_updated_at).toBe(current.updated_at);
      expect(expected_version).toBe(current.version);
      const changed = ['title', 'description', 'action_label', 'action_url'].some(key => request[key] !== current[key as keyof typeof current]);
      current = { ...current, ...request, version: current.version + Number(changed), updated_at: new Date(Date.UTC(2026, 9, 17, 0, 0, writeSequence++)).toISOString(), updated_by: admin.id };
    }
    await route.fulfill({ json: current });
  });
  await page.route('**/api/announcements/current', route => route.fulfill({ json: current.enabled ? current : null }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/admin/announcements');
  await page.getByLabel('标题', { exact: true }).fill('写作指南更新');
  await page.getByLabel('说明', { exact: true }).fill('新的写作指南已经上线，按自己的节奏写下去。');
  await page.getByLabel('按钮文案（可选）').fill('查看指南');
  await page.getByLabel('按钮链接（可选）').fill('/docs');
  await page.getByRole('button', { name: '保存并展示', exact: true }).click();
  await expect(page.getByText('正在展示', { exact: false })).toBeVisible();
  expect(current.version).toBe(1);
  await page.screenshot({ path: test.info().outputPath('announcement-admin-mobile.png') });
  await page.goto('/dashboard');
  const banner = page.getByRole('region', { name: '站内公告', exact: true });
  await expect(banner).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('announcement-mobile.png') });
  await expect(banner.getByRole('link', { name: '查看指南' })).toHaveAttribute('rel', 'noopener noreferrer');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await banner.getByRole('button', { name: '关闭公告' }).click();
  await page.reload();
  await expect(banner).not.toBeVisible();
  await page.goto('/admin/announcements');
  await page.getByLabel('标题', { exact: true }).fill('写作指南再次更新');
  await page.getByRole('button', { name: '保存并展示', exact: true }).click();
  await expect(page.getByText('版本 2', { exact: false })).toBeVisible();
  await page.goto('/dashboard');
  await expect(banner.getByText('写作指南再次更新', { exact: true })).toBeVisible();
  await page.goto('/admin/announcements');
  await page.getByLabel('标题', { exact: true }).fill('还没发布的草稿');
  await page.getByRole('button', { name: '停止展示', exact: true }).click();
  await expect(page.getByText('未展示', { exact: false })).toBeVisible();
  expect(current.title).toBe('写作指南再次更新');
  expect(current.version).toBe(2);
  await page.goto('/dashboard');
  await expect(banner).not.toBeVisible();
});


test('English original layout and footer disclosure stay usable on a phone', async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/dashboard/billing?lang=en');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Plans & Benefits');
  await expect(page.getByText('Current Plan', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Current usage', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.getByRole('button', { name: 'Redeem Code', exact: true })).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('billing-mobile-en.png') });
  await page.getByRole('button', { name: 'Get Pro', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Get Pro', exact: true })).toBeVisible();
});

test('SPA back and return-to-app navigation refreshes cached empty, changed and stopped announcements', async ({ page }) => {
  await setup(page);
  const admin = { ...responsiveUser, is_superuser: true };
  await page.addInitScript(value => localStorage.setItem('user', JSON.stringify(value)), admin);
  await page.route('**/api/auth/me', route => route.fulfill({ json: admin }));
  let sequence = 0;
  let adminReads = 0;
  let publicReads = 0;
  let current = { title: '', description: '', action_label: null, action_url: null,
    enabled: false, version: 0, updated_at: null as string | null, updated_by: null as string | null };
  await page.route('**/api/admin/announcements/current', async route => {
    if (route.request().method() === 'PUT') {
      const { expected_version, expected_updated_at, ...body } = route.request().postDataJSON();
      expect(expected_version).toBe(current.version);
      expect(expected_updated_at).toBe(current.updated_at);
      const contentChanged = body.title !== current.title || body.description !== current.description;
      current = { ...current, ...body, version: current.version + Number(contentChanged),
        updated_at: new Date(Date.UTC(2026, 9, 17, 0, 0, sequence++)).toISOString(), updated_by: admin.id };
    } else adminReads += 1;
    await route.fulfill({ json: current });
  });
  await page.route('**/api/announcements/current', route => {
    publicReads += 1;
    return route.fulfill({ json: current.enabled ? current : null });
  });
  await page.goto('/admin/announcements');
  await page.getByLabel('标题', { exact: true }).waitFor();
  await page.evaluate(() => { Object.assign(window, { announcementSpaMarker: 'same-document' }); });
  await page.getByRole('button', { name: '返回应用', exact: true }).click();
  await expect.poll(() => publicReads).toBe(1);
  const banner = page.getByRole('region', { name: '站内公告', exact: true });
  await expect(banner).toHaveCount(0);

  for (const [index, title] of ['首次发布', '新版公告'].entries()) {
    await page.goBack();
    await page.getByLabel('标题', { exact: true }).fill(title);
    await page.getByRole('textbox', { name: '说明', exact: true }).fill('真实站内跳转，不通过整页刷新清空缓存。');
    await page.getByRole('button', { name: '保存并展示', exact: true }).click();
    await expect(page.getByText(`版本 ${index + 1}`, { exact: false })).toBeVisible();
    await page.getByRole('button', { name: '返回应用', exact: true }).click();
    await expect(banner.getByText(title, { exact: true })).toBeVisible();
  }
  await page.goBack();
  await page.getByRole('button', { name: '停止展示', exact: true }).click();
  await expect(page.getByText('未展示', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '返回应用', exact: true }).click();
  await expect.poll(() => publicReads).toBe(4);
  await expect(banner).toHaveCount(0);
  expect(adminReads).toBe(4);
  expect(await page.evaluate(() => Reflect.get(window, 'announcementSpaMarker'))).toBe('same-document');
});

test('explicitly disabled checkout offers support without a manual redemption entry', async ({ page }) => {
  await setup(page);
  await page.route('**/api/v1/payments/options', route => route.fulfill({ json: { enabled: false, payment_methods: [] } }));
  await page.goto('/dashboard/billing');
  await expect(page.getByTestId('billing-checkout-unavailable')).toBeVisible();
  await expect(page.getByRole('button', { name: '开通 Pro', exact: true })).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByTestId('billing-help').locator('summary').click();
  await expect(page.getByTestId('billing-help').getByRole('link', { name: 'support@zenstory.ai' })).toBeVisible();
  await expect(page.getByRole('button', { name: '兑换码', exact: true })).toHaveCount(0);
});

test('unknown payment availability remains distinct from disabled checkout', async ({ page }) => {
  await setup(page);
  await page.route('**/api/v1/payments/options', route => route.fulfill({ status: 503, json: { detail: 'temporarily unavailable' } }));
  await page.goto('/dashboard/billing');
  await page.getByRole('button', { name: '开通 Pro', exact: true }).click();
  const checkout = page.getByRole('dialog');
  await expect(checkout.getByRole('alert')).toContainText('暂时无法在线支付');
  await expect(checkout.getByRole('button', { name: '去支付 ¥399' })).toBeDisabled();
  await expect(page.getByTestId('billing-checkout-unavailable')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '兑换码', exact: true })).toHaveCount(0);
});

test('an older server without announcements does not block the dashboard', async ({ page }) => {
  await setup(page);
  let attempts = 0;
  await page.route('**/api/announcements/current', route => {
    attempts += 1;
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  });
  await page.goto('/dashboard');
  await expect(page.getByRole('region', { name: '站内公告', exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect.poll(() => attempts).toBe(1);
  await page.goto('/dashboard/billing');
  await expect(page.getByRole('heading', { name: '订阅权益', exact: true })).toBeVisible();
});

test('checkout cannot create an order while catalog prices are loading or unavailable', async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/subscription/catalog', async route => {
    await pending;
    await route.fulfill({ status: 503, json: { detail: 'catalog unavailable' } });
  });
  const orders: string[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().includes('/payments/orders')) orders.push(request.url());
  });
  await page.goto('/dashboard/billing');
  await page.getByRole('button', { name: '开通 Pro', exact: true }).click();
  const checkout = page.getByRole('dialog');
  await expect(checkout.getByText('正在加载价格…', { exact: true })).toBeVisible();
  await expect(checkout.getByRole('button', { name: '去支付', exact: true })).toBeDisabled();
  release();
  await expect(checkout.getByRole('alert')).toContainText('暂时无法确认价格', { timeout: 15000 });
  await expect(checkout.getByRole('button', { name: '去支付', exact: true })).toBeDisabled();
  expect(orders).toEqual([]);
});
