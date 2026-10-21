import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

for (const width of [320, 390, 768]) {
  test(`documentation drawer keeps its controls and focus reachable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 600 });
    await page.addInitScript(() => localStorage.setItem('zenstory-language', 'en'));
    await page.goto('/docs/user-guide/editor/');
    await expect(page.getByRole('heading', { name: 'Using the editor', exact: true })).toBeVisible();
    const trigger = page.getByRole('button', { name: 'Menu', exact: true });
    expect((await trigger.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await trigger.click();
    const drawer = page.getByRole('dialog', { name: 'Documentation Center', exact: true });
    await expect(drawer).toBeVisible();
    const close = drawer.getByRole('button', { name: 'Close Menu', exact: true });
    await expect(close).toBeFocused();
    expect((await close.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await expect(page.locator('body')).toHaveCSS('overflow', 'hidden');
    const search = drawer.getByRole('textbox', { name: 'Search documentation' });
    await search.fill('editor');
    const clear = drawer.getByRole('button', { name: 'Clear search', exact: true });
    expect((await clear.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await clear.click();
    const links = drawer.getByRole('navigation').getByRole('link');
    const last = links.last();
    await last.focus();
    await expect(last).toBeInViewport();
    const nav = (await drawer.getByRole('navigation').boundingBox())!;
    expect(nav.y + nav.height).toBeLessThanOrEqual(600);
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(drawer).not.toBeVisible();
    await expect(trigger).toBeFocused();
    await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
    await trigger.click();
    await page.setViewportSize({ width: 1280, height: 600 });
    await expect(drawer).not.toBeVisible();
    await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  });
}

test('verification digits have distinct names and the return action is a phone touch target', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem('zenstory-language', 'en'));
  await page.goto('/verify-email?email=reader%40example.com');
  for (let index = 1; index <= 6; index += 1) {
    await expect(page.getByRole('textbox', { name: `Verification digit ${index} of 6`, exact: true })).toBeVisible();
  }
  const back = page.getByRole('button', { name: 'Back to Login', exact: true });
  expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
});

test('phone password recovery actions are easy to tap in both steps', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem('zenstory-language', 'en'));
  await page.goto('/login');
  const forgot = page.getByRole('link', { name: 'Forgot password?', exact: true });
  expect((await forgot.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await forgot.click();
  const back = page.getByRole('link', { name: 'Back to login', exact: true });
  expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.route('**/api/auth/password-reset/request', route => route.fulfill({ json: { message: 'Code sent' } }));
  await page.getByLabel('Account email').fill('reader@example.com');
  await page.getByRole('button', { name: 'Send code', exact: true }).click();
  const change = page.getByRole('button', { name: 'Use a different email', exact: true });
  await expect(change).toBeVisible();
  expect((await change.boundingBox())!.height).toBeGreaterThanOrEqual(44);
});

for (const theme of ['light', 'dark']) {
  test(`public forms and document code remain accessible in ${theme} mode`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(mode => {
      localStorage.setItem('zenstory-language', 'en');
      localStorage.setItem('zenstory-theme', mode);
    }, theme);
    await page.route('**/api/**', route => route.fulfill({ status: 403, json: { detail: 'The email or password is incorrect' } }));
    for (const path of ['/login', '/register', '/forgot-password', '/verify-email?email=reader%40example.com', '/docs/user-guide/editor']) {
      await page.goto(path);
      await expect(page.locator('h1').first()).toBeVisible();
      if (path.startsWith('/docs')) {
        // The article fades in; check contrast on the settled page, not mid-animation.
        await expect(page.locator('article.prose')).toHaveCSS('opacity', '1');
      }
      if (path === '/login') {
        await page.locator('#identifier').fill('sample-user');
        await page.locator('#password').fill('disposable-password');
        await page.locator('form button[type=submit]').click();
        await expect(page.getByRole('alert')).toBeVisible();
        await expect(page.locator('form button[type=submit]')).toBeEnabled();
        await expect(page.locator('form button[type=submit]')).toHaveCSS('opacity', '1');
      }
      const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
      expect(result.violations.filter(v => ['critical', 'serious'].includes(v.impact ?? '')).map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) })), path).toEqual([]);
    }
  });
}
