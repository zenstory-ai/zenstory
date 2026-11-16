import { test, expect } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';
import { bootstrapAdminSession } from './fixtures/admin-responsive';

for (const width of [390, 1280]) {
  test(`visible quota surfaces and explicit modal reopens at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockResponsiveApp(page);
    await page.route('**/api/v1/subscription/quota', route => route.fulfill({ json: { ai_conversations: { used: 3, limit: 3, reset_at: '2026-10-15T16:00:00Z' }, projects: {used: 1, limit: 3} } }));
    await page.addInitScript(() => {
      const captures: Array<{action: string; source: string; surface: string}> = [];
      Object.assign(window, { __upgradeCaptures: captures });
      window.addEventListener('zenstory:upgrade-funnel', event => captures.push((event as CustomEvent).detail));
    });
    await page.goto('/project/responsive-project-0');
    if (width < 768) {
      await expect(page.locator('#editor-tab')).toHaveAttribute('aria-selected', 'true');
      await expect(page.getByTestId('chat-quota-card')).toHaveCount(1);
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      expect(await page.evaluate(() => (window as unknown as {__upgradeCaptures: Array<{action: string; surface: string}>}).__upgradeCaptures.filter(event => event.action === 'expose' && event.surface === 'card').length)).toBe(0);
      await page.locator('#chat-tab').click();
    }
    await expect(page.getByTestId('chat-quota-card')).toBeVisible();
    const views = (surface: string) => page.evaluate(value => (window as unknown as {__upgradeCaptures: Array<{action: string; surface: string}>}).__upgradeCaptures.filter(event => event.action === 'expose' && event.surface === value).length, surface);
    await expect.poll(() => views('card')).toBe(1);
    await expect.poll(() => views('badge')).toBeGreaterThanOrEqual(1);
    const count = await views('card');
    await page.getByTestId('chat-input').fill('保留输入，不发起模型请求');
    expect(await views('card')).toBe(count);
    await page.getByTestId('send-button').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect.poll(() => views('modal')).toBe(1);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await page.getByTestId('send-button').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect.poll(() => views('modal')).toBe(2);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('chat-input')).toHaveValue('保留输入，不发起模型请求');
    await page.screenshot({ path: `/tmp/zenstory-issue-validation/184-chat-${width}.png`, fullPage: true });
  });

  test(`admin separates surface views from users and payments at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await bootstrapAdminSession(page);
    await page.addInitScript(() => localStorage.setItem('zenstory-language', 'en'));
    await page.route('**/api/admin/dashboard/upgrade-funnel?**', route => route.fulfill({ json: { window_days: 7, period_start: '2026-03-01T00:00:00Z', period_end: '2026-03-08T00:00:00Z', totals: {expose: 12, click: 6, conversion: 2}, sources: [], surfaces: [{source:'chat_quota_blocked', surface:'card', exposes:12, exposed_users:4, clicks:6, clicking_users:3, page_arrivals:2}] } }));
    await page.goto('/admin');
    const table = page.getByRole('table', {name: 'Upgrade entry surfaces'});
    await expect(table).toBeVisible();
    await expect(table).toContainText('12 / 4');
    await expect(table).toContainText('6 / 3');
    await expect(table).toContainText('Inline card');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await table.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/zenstory-issue-validation/184-admin-${width}.png`, fullPage: true });
  });
}
