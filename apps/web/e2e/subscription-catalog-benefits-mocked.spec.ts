import { test, expect } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

for (const language of ['zh', 'en'] as const) {
  for (const width of [390, 768, 1440]) {
    test(`catalog benefits stay consistent in ${language} at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await mockResponsiveApp(page, null, 'free', language);
      await page.route('**/api/v1/subscription/catalog', route => route.fulfill({ json: { version: 'fixture', tiers: ['free', 'pro'].map(name => ({
        id: name, name, display_name: name, display_name_en: name, price_monthly_cents: name === 'pro' ? 4900 : 0, price_yearly_cents: name === 'pro' ? 39900 : 0, recommended: name === 'pro', summary_key: name === 'pro' ? 'creator' : 'starter', target_user_key: name === 'pro' ? 'daily_writer' : 'explorer',
        entitlements: { ai_conversations_per_day: name === 'pro' ? -1 : 3, active_projects_limit: name === 'pro' ? -1 : 3, material_decompositions_monthly: name === 'pro' ? 7 : 0, file_versions_per_file: name === 'pro' ? 42 : 10, custom_skills_limit: name === 'pro' ? 20 : 3 },
      })) } }));
      await page.route('**/api/v1/payments/options', route => route.fulfill({ json: { enabled: true, payment_methods: ['alipay'] } }));
      for (const path of ['/pricing', '/dashboard/billing']) {
        await page.goto(path);
        await expect(page.getByText(language === 'zh' ? '42 个' : '42 versions', { exact: true })).toBeVisible();
        await expect(page.getByText(language === 'zh' ? '10 个' : '10 versions', { exact: true })).toBeVisible();
        if (language === 'en') await expect(page.getByText('3/day', { exact: true }).first()).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      }
      const upgrade = page.getByRole('button', { name: language === 'zh' ? '开通 Pro' : 'Get Pro', exact: true }).first();
      await upgrade.click();
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByText(language === 'zh' ? 'AI 消息不限条数 · 项目不限 · 每月 7 次素材拆解' : 'Unlimited AI messages · Unlimited projects · 7 material breakdowns per month')).toBeVisible();
      const year = dialog.getByRole('radio').nth(1);
      const badge = await year.getByText(language === 'zh' ? /最划算/ : /Best value/).boundingBox();
      const duration = await year.getByText(language === 'zh' ? '年付 · 365 天' : 'Yearly · 365 days', { exact: true }).boundingBox();
      expect(badge!.y >= duration!.y + duration!.height || badge!.y + badge!.height <= duration!.y).toBe(true);
      const box = (await dialog.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      await page.screenshot({ path: `/tmp/zenstory-issue-validation/189-${language}-${width}.png`, fullPage: true });
    });
  }
}
