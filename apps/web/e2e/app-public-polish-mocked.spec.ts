import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const catalog = {
  version: 'public-polish-synthetic', comparison_mode: 'task_outcome', pricing_anchor_monthly_cents: 4900,
  tiers: [
    { id: 'free', name: 'free', display_name: '免费版', display_name_en: 'Free', price_monthly_cents: 0, price_yearly_cents: 0, recommended: false,
      entitlements: { ai_conversations_per_day: 3, active_projects_limit: 3, material_decompositions_monthly: 0, custom_skills_limit: 3, file_versions_per_file: 10 } },
    { id: 'pro', name: 'pro', display_name: 'Pro', display_name_en: 'Pro', price_monthly_cents: 4900, price_yearly_cents: 39900, recommended: true,
      entitlements: { ai_conversations_per_day: -1, active_projects_limit: -1, material_decompositions_monthly: 5, custom_skills_limit: 20, file_versions_per_file: 100 } },
  ],
};

for (const language of ['zh', 'en']) {
  for (const theme of ['light', 'dark']) {
    for (const width of [390, 1440]) {
      test(`public pricing keeps one price per cycle ${language} ${theme} ${width}`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        await page.addInitScript(({ language, theme }) => {
          localStorage.setItem('zenstory-language', language);
          localStorage.setItem('zenstory-theme', theme);
        }, { language, theme });
        const writes: string[] = [];
        await page.route('**/api/**', route => {
          const request = route.request();
          if (request.method() !== 'GET') writes.push(request.url());
          return route.fulfill({ json: request.url().includes('/subscription/catalog') ? catalog : null });
        });
        await page.goto('/pricing');
        const yearly = page.getByRole('radio', { name: language === 'zh' ? /年付/ : /Yearly/ });
        const monthly = page.getByRole('radio', { name: language === 'zh' ? '月付' : 'Monthly', exact: true });
        // English readers see Pro's USD list price; the yearly saving comes from those USD prices.
        const annualPrice = page.getByText(language === 'zh' ? '¥399/年' : '$79/year', { exact: true });
        const monthlyPrice = page.getByText(language === 'zh' ? '¥49/月' : '$9.99/month', { exact: true });
        await expect(monthly).toHaveAttribute('aria-checked', 'true');
        await expect(monthlyPrice).toBeVisible();
        await expect(annualPrice).toHaveCount(0);
        await expect(page.getByText(language === 'zh' ? /32%/ : /34%/)).toHaveCount(1);
        await page.screenshot({ path: info.outputPath('pricing-month.png'), fullPage: true, animations: 'disabled' });
        await yearly.click();
        await expect(annualPrice).toBeVisible();
        await expect(monthlyPrice).toHaveCount(0);
        // Native keyboard selection retains the same billing intent.
        await monthly.press('ArrowRight');
        await expect(yearly).toHaveAttribute('aria-checked', 'true');
        await yearly.press('ArrowLeft');
        await expect(monthly).toHaveAttribute('aria-checked', 'true');
        if (width >= 1024) {
          const headings = await page.locator('main h2').all();
          const a = (await headings[0].boundingBox())!, b = (await headings[1].boundingBox())!;
          expect(Math.abs(a.y - b.y)).toBeLessThanOrEqual(1);
          const cards = headings.map(heading => heading.locator('xpath=../../..'));
          const metric = language === 'zh' ? '每日 AI 消息' : 'Daily AI messages';
          const firstRows = await Promise.all(cards.map(card => card.getByText(metric, { exact: true }).boundingBox()));
          expect(Math.abs(firstRows[0]!.y - firstRows[1]!.y), 'plan rights remain aligned when the Free card omits its redundant price').toBeLessThanOrEqual(1);
          const actions = await Promise.all(cards.map(card => card.getByRole('button').boundingBox()));
          expect(Math.abs(actions[0]!.y - actions[1]!.y)).toBeLessThanOrEqual(1);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath('pricing-year.png'), fullPage: true, animations: 'disabled' });
        const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
        expect(result.violations.filter(issue => ['serious', 'critical'].includes(issue.impact ?? ''))).toEqual([]);
        expect(writes, 'public browsing never makes a generation or payment request').toEqual([]);
      });
    }
  }
  test(`public pricing retry stays touch-sized and recovers ${language}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(language => localStorage.setItem('zenstory-language', language), language);
    let recovered = false;
    const writes: string[] = [];
    await page.route('**/api/**', route => {
      const request = route.request();
      if (request.method() !== 'GET') writes.push(request.url());
      if (!request.url().includes('/subscription/catalog')) return route.fulfill({ json: null });
      return recovered
        ? route.fulfill({ json: catalog })
        : route.fulfill({ status: 503, json: { detail: 'Synthetic catalog outage' } });
    });
    await page.goto('/pricing');
    const retry = page.getByRole('button', { name: language === 'zh' ? '重试' : 'Retry', exact: true });
    await expect(retry).toBeVisible({ timeout: 15_000 });
    expect((await retry.boundingBox())!.height, 'error recovery is a 44px phone touch target').toBeGreaterThanOrEqual(44);
    recovered = true;
    await retry.click();
    await expect(page.getByRole('heading', { name: 'Pro', exact: true })).toBeVisible();
    await expect(retry).toHaveCount(0);
    expect(writes).toEqual([]);
  });

  for (const path of ['/privacy-policy', '/terms-of-service']) {
    test(`App footer opens its own legal reader ${language} ${path}`, async ({ page }, info) => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.addInitScript(language => localStorage.setItem('zenstory-language', language), language);
      await page.goto('/');
      await page.locator(`footer a[href="${path}"]`).click();
      await expect(page).toHaveURL(new URL(path, info.project.use.baseURL).href);
      await expect(page.locator('#root main h1')).toBeVisible();
      await expect(page.getByRole('banner').getByRole('link', { name: language === 'zh' ? '返回首页' : 'Back to home', exact: true })).toHaveAttribute('href', '/');
      await expect(page.locator('#legal-zh')).toHaveCount(0);
      // Host canonical metadata is covered by site-domain and the built deployment smoke matrix.
      await page.screenshot({ path: info.outputPath('app-legal.png'), animations: 'disabled' });
    });
  }
}
