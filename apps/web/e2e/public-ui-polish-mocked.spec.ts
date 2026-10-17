import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const tiers = [
  { id: 'free', name: 'free', display_name: '免费版', price_monthly_cents: 0, price_yearly_cents: 0, recommended: false, summary_key: 'starter', target_user_key: 'explorer', entitlements: { ai_conversations_per_day: 3, active_projects_limit: 3, material_decompositions_monthly: 0, custom_skills_limit: 3 } },
  { id: 'pro', name: 'pro', display_name: 'Pro', price_monthly_cents: 4900, price_yearly_cents: 39900, recommended: true, summary_key: 'creator', target_user_key: 'daily_writer', entitlements: { ai_conversations_per_day: -1, active_projects_limit: -1, material_decompositions_monthly: 5, custom_skills_limit: 3 } },
];

test('demo decoration never intercepts pause or manual scene clicks', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.addInitScript(() => localStorage.setItem('zenstory-language', 'zh'));
  await page.goto('/');
  await page.getByRole('button', { name: '查看演示', exact: true }).click();
  await page.getByRole('button', { name: '暂停演示', exact: true }).click();
  await expect(page.getByRole('button', { name: '继续演示', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '审阅与回退', exact: true }).click();
  await expect(page.getByRole('button', { name: '审阅与回退', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '继续演示', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停演示', exact: true })).toBeVisible();
});

for (const width of [320, 390, 768, 1440]) {
  for (const language of ['zh', 'en']) {
    test(`anonymous home and pricing controls fit ${width}px in ${language}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.addInitScript((lang) => localStorage.setItem('zenstory-language', lang), language);
      await page.route('**/api/**', route => route.fulfill({ json: { version: 'public-ui-test', tiers } }));
      await page.goto('/');
      await expect(page.getByRole('main')).toBeVisible();
      const demo = page.getByRole('region', { name: language === 'zh' ? '查看演示' : 'View Demo' });
      await page.getByRole('button', { name: language === 'zh' ? '查看演示' : 'View Demo', exact: true }).click();
      await expect(demo).toBeFocused();
      await expect.poll(() => page.locator('header').evaluate(el => el.getBoundingClientRect().top)).toBe(0);
      for (const control of await demo.getByRole('button').all()) {
        expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
      if (width < 1024) {
        await page.getByRole('button', { name: language === 'zh' ? '打开菜单' : 'Open menu', exact: true }).click();
        const menu = page.getByRole('menu');
        const bounds = (await menu.boundingBox())!;
        expect(bounds.y).toBe(48);
        expect(bounds.y + bounds.height).toBeLessThanOrEqual(900);
        await page.keyboard.press('Escape');
        await expect(menu).not.toBeVisible();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const homeResults = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
      expect(homeResults.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? '')).map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) }))).toEqual([]);
      await page.goto('/pricing');
      await expect(page.getByRole('heading', { name: 'Pro', exact: true })).toBeVisible();
      const toggle = page.getByRole('switch');
      expect((await toggle.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      for (const checked of [false, true]) {
        await expect(toggle).toHaveAttribute('aria-checked', String(checked));
        const geometry = await toggle.evaluate(el => {
          const track = el.querySelector('[aria-hidden]')!;
          const thumb = track.firstElementChild!;
          const a = track.getBoundingClientRect(), b = thumb.getBoundingClientRect();
          return { left: b.left - a.left, right: a.right - b.right, top: b.top - a.top, bottom: a.bottom - b.bottom };
        });
        expect(Math.min(...Object.values(geometry))).toBeGreaterThanOrEqual(0);
        await toggle.focus();
        await page.keyboard.press('Space');
      }
      const year = page.getByRole('radio').filter({ hasText: language === 'zh' ? '年付' : 'Yearly' });
      await year.focus();
      await page.keyboard.press('ArrowLeft');
      await expect(page.getByRole('radio').filter({ hasText: language === 'zh' ? '月付' : 'Monthly' })).toBeFocused();
      await expect(year).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
      const cards = page.locator('main section').filter({ has: page.getByRole('heading', { name: 'Pro', exact: true }) });
      const freeCta = cards.getByRole('button').nth(0);
      const proCta = cards.getByRole('button').nth(1);
      if (width >= 1024) expect((await freeCta.boundingBox())!.y).toBe((await proCta.boundingBox())!.y);
      await freeCta.focus();
      expect(await freeCta.evaluate(el => getComputedStyle(el).outlineStyle)).toBe('none');
      expect(await freeCta.evaluate(el => getComputedStyle(el).boxShadow)).not.toBe('none');
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
      expect(results.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? '')).map(v => ({ id: v.id, nodes: v.nodes.map(n => ({target: n.target, summary: n.failureSummary})) }))).toEqual([]);
    });
  }
}

for (const width of [390, 1440]) {
  test(`anonymous public pages remain readable in dark mode at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.addInitScript(() => {
      localStorage.setItem('zenstory-language', 'zh');
      localStorage.setItem('zenstory-theme', 'dark');
    });
    await page.route('**/api/**', route => route.fulfill({ json: { version: 'public-ui-test', tiers } }));
    for (const path of ['/', '/pricing']) {
      await page.goto(path);
      await expect(page.getByRole('main')).toBeVisible();
      if (path === '/pricing') {
        await expect(page.getByRole('heading', { name: 'Pro', exact: true })).toBeVisible();
        await expect(page.getByRole('button', { name: '开通 Pro', exact: true }).last()).toHaveCSS('opacity', '1');
      }
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
      expect(results.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? '')).map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) }))).toEqual([]);
    }
  });
}
