import { test, expect } from './fixtures/static-site';

test.beforeEach(async ({ page }) => {
  await page.route('https://fonts.googleapis.com/**', route => route.fulfill({ contentType: 'text/css', body: '' }));
  await page.route('https://fonts.gstatic.com/**', route => route.abort());
});

for (const [name, languages, target] of [
  ['Chinese preference', ['zh-CN', 'en-US'], 'zh'],
  ['English preference', ['en-GB', 'zh-CN'], 'en'],
  ['first supported preference', ['fr-FR', 'zh-TW', 'en'], 'zh'],
  ['unsupported preferences', ['fr-FR', 'de-DE'], 'en'],
] as const) {
  test(`root entry follows ${name} without persisting a manual choice`, async ({ page, context }) => {
    await page.addInitScript(values => Object.defineProperty(navigator, 'languages', { get: () => values }), languages);
    await page.goto('/?source=guide#examples-h');
    await expect(page.locator('html')).toHaveAttribute('lang', target === 'zh' ? 'zh-CN' : 'en');
    await expect(page).toHaveURL(new RegExp(`${target === 'zh' ? '/zh' : '/'}\\?source=guide#examples-h$`));
    expect((await context.cookies()).some(cookie => cookie.name === 'zs_lang')).toBe(false);
  });
}

test('manual language selection wins over the browser and survives returning to the root', async ({ page, context }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN'] }));
  await page.goto('/zh/');
  await page.locator('.lang-switch a[hreflang="en"]').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  expect((await context.cookies()).find(cookie => cookie.name === 'zs_lang')?.value).toBe('en');
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page.locator('.lang-switch a[hreflang="zh-CN"]').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
});

test('explicit language pages and English deep links are not redirected', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN'] }));
  await page.goto('/projects/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page).toHaveURL(/\/projects\/$/);
  await page.goto('/zh/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
});

test('the English switch remains usable when cookies cannot be stored', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN'] });
    Object.defineProperty(document, 'cookie', { get: () => '', set: () => {}, configurable: true });
  });
  await page.goto('/zh/');
  await page.locator('.lang-switch a[hreflang="en"]').click();
  await expect(page).toHaveURL(/\/\?lang=en$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});

test.describe('Without JavaScript', () => {
  test.use({ javaScriptEnabled: false, locale: 'zh-CN' });
  test('the root is readable in English and the ordinary Chinese link still works', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await page.locator('.lang-switch a[hreflang="zh-CN"]').click();
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  });
});
