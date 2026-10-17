import { test, expect } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

const catalog = {
  version: 'free-three-message-regression',
  tiers: [
    { id: 'free', name: 'free', display_name: '免费版', display_name_en: 'Free', price_monthly_cents: 0, price_yearly_cents: 0, summary_key: 'starter', target_user_key: 'explorer', entitlements: { ai_conversations_per_day: 3, active_projects_limit: 3, material_decompositions_monthly: 0, custom_skills_limit: 3 } },
    { id: 'pro', name: 'pro', display_name: 'Pro', display_name_en: 'Pro', price_monthly_cents: 4900, price_yearly_cents: 39900, recommended: true, summary_key: 'creator', target_user_key: 'daily_writer', entitlements: { ai_conversations_per_day: -1, active_projects_limit: -1, material_decompositions_monthly: 5, custom_skills_limit: 20 } },
  ],
};

for (const language of ['zh', 'en']) {
  test(`the public offer and catalog consistently show three daily free messages in ${language}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(lang => localStorage.setItem('zenstory-language', lang), language);
    await page.route('**/api/**', route => route.fulfill({ json: catalog }));
    await page.goto('/');
    await expect(page.getByRole('heading', { name: language === 'zh'
      ? /^3\s*条\s*AI\s*消息\/天$/
      : /^3 AI messages a day$/i })).toBeAttached();
    await page.goto('/pricing');
    await expect(page.getByText(language === 'zh' ? '3 条/天' : '3/day', { exact: true })).toBeVisible();
    await expect(page.getByText(language === 'zh'
      ? /免费版每天包含\s*3\s*条\s*AI\s*消息/
      : /Free includes 3 AI messages per day/i)).toBeVisible();
    await expect(page.getByText(language === 'zh' ? '不限' : 'Unlimited', { exact: true }).first()).toBeAttached();
  });
}

test('lowering the free allowance preserves existing usage and the Beijing reset wall', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResponsiveApp(page);
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/subscription/quota')) {
      await route.fulfill({ json: { ai_conversations: { used: 10, limit: 3, reset_at: '2026-10-15T16:00:00Z' }, projects: { used: 1, limit: 3 }, material_decompositions: { used: 0, limit: 0 }, skill_creates: { used: 0, limit: 3 } } });
    } else if (path.endsWith('/subscription/catalog')) {
      await route.fulfill({ json: catalog });
    } else {
      await route.fallback();
    }
  });
  await page.goto('/dashboard');
  await page.getByTestId('dashboard-inspiration-input').fill('保留今天已有的消息用量，明天再开始这个故事');
  await page.getByTestId('create-project-button').click();
  await expect(page.getByRole('dialog')).toContainText('今天的免费 AI 消息用完了');
  await expect(page.getByRole('dialog')).toContainText('00:00');
  await expect(page.getByTestId('dashboard-inspiration-input')).toHaveValue('保留今天已有的消息用量，明天再开始这个故事');
  await page.keyboard.press('Escape');
  await page.goto('/dashboard/billing');
  await expect(page.getByTestId('billing-usage-ai_conversations')).toHaveText('10/3');
  const barWidth = await page.getByTestId('billing-usage-bar-ai_conversations').evaluate(el => ({ bar: el.getBoundingClientRect().width, track: el.parentElement!.getBoundingClientRect().width }));
  expect(barWidth.bar).toBeCloseTo(barWidth.track, 1);
  await expect(page.getByText('免费用户每日最多 3 条 AI 消息，北京时间次日 00:00 恢复。', { exact: true })).toBeVisible();
});

for (const width of [320, 390, 768, 1920]) {
  for (const language of ['zh', 'en']) {
    test(`three-message quota upgrade controls fit ${width}px in ${language}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await mockResponsiveApp(page);
      await page.addInitScript(lang => localStorage.setItem('zenstory-language', lang), language);
      await page.route('**/api/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith('/subscription/quota')) {
          await route.fulfill({ json: { ai_conversations: { used: 2, limit: 3 }, projects: { used: 1, limit: 3 } } });
        } else {
          await route.fallback();
        }
      });
      await page.goto('/dashboard');
      const fullUpgrade = page.getByRole('button', { name: language === 'zh' ? '开通 Pro，AI 消息不限条数' : 'Get Pro for unlimited AI messages', exact: true });
      await expect(fullUpgrade).toBeVisible();
      const fullBox = (await fullUpgrade.boundingBox())!;
      if (width < 768) expect.soft(fullBox.height).toBeGreaterThanOrEqual(44);
      expect(fullBox.x + fullBox.width).toBeLessThanOrEqual(width);
      const greeting = (await page.getByRole('heading', { level: 1 }).boundingBox())!;
      expect(greeting.width).toBeGreaterThanOrEqual(Math.min(200, width - 48));
      await page.goto('/project/responsive-project-0');
      if (width < 768) {
        await page.locator('#chat-tab').click();
        await expect(page.locator('#chat-panel')).toHaveCSS('opacity', '1');
      }
      const compact = page.getByTestId('quota-badge-compact');
      await expect(compact).toHaveText('2/3');
      const compactUpgrade = compact.locator('..').getByRole('button');
      const button = (await compactUpgrade.boundingBox())!;
      if (width < 768) {
        expect(button.height).toBeGreaterThanOrEqual(44);
        expect(button.width).toBeGreaterThanOrEqual(44);
      }
      const actions = page.getByTestId('chat-panel-header-actions');
      const controls = await actions.getByRole('button').all();
      for (let i = 0; i < controls.length; i += 1) {
        const current = (await controls[i].boundingBox())!;
        expect(current.x).toBeGreaterThanOrEqual(0);
        expect(current.x + current.width).toBeLessThanOrEqual(width);
        if (i > 0) {
          const previous = (await controls[i - 1].boundingBox())!;
          expect(current.x).toBeGreaterThanOrEqual(previous.x + previous.width);
        }
      }
    });
  }
}
