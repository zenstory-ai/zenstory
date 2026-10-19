import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mockResponsiveApp } from './fixtures-responsive';

const sizes = [{ width: 1440, height: 900 }, { width: 768, height: 1024 }, { width: 390, height: 844 }];
const accents = ['#4a9eff', '#22c55e', '#fbbf24', '#f87171', '#ec4899', '#8b5cf6'];

test('project opening and deletion have independent native keyboard targets', async ({ page }) => {
  await mockResponsiveApp(page);
  await page.goto('/dashboard');
  const card = page.getByTestId('project-card').first();
  await card.hover();
  await card.getByRole('button', { name: '删除项目', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  // Inspect the settled dialog, rather than blended colors during its entrance fade.
  await expect(page.getByRole('dialog')).toHaveCSS('opacity', '1');
  const dialogCheck = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(dialogCheck.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) }))).toEqual([]);
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  await card.getByRole('button', { name: '打开项目「小屏布局回归项目」', exact: true }).focus();
  await page.keyboard.press('Space');
  await expect(page).toHaveURL(/\/project\/responsive-project-0$/);
  await expect(page.getByTestId('chat-input')).toBeVisible();
  const workspaceCheck = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(workspaceCheck.violations.filter(violation => violation.id === 'color-contrast')).toEqual([]);
});

test('mobile navigation remains on screen after scrolling and keeps page changes at the top', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResponsiveApp(page);
  await page.goto('/dashboard');
  await page.getByTestId('project-card').last().scrollIntoViewIfNeeded();
  await page.getByRole('button', { name: '打开菜单', exact: true }).click();
  const navigation = page.getByRole('navigation', { name: '移动端导航菜单' });
  await expect(navigation).toBeInViewport();
  await expect(page.locator('.dashboard-content')).toHaveAttribute('inert');
  await navigation.getByRole('link', { name: '我的项目', exact: true }).click();
  await expect(page.getByRole('heading', { name: '我的项目', exact: true })).toBeInViewport();
});

for (const size of sizes) {
  test(`shared surfaces and writing controls fit ${size.width}px`, async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize(size);
    await mockResponsiveApp(page);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const route of ['/dashboard', '/dashboard/projects', '/dashboard/materials', '/dashboard/skills', '/dashboard/billing', '/project/responsive-project-0', '/', '/pricing', '/login']) {
      await page.goto(route);
      await expect(page.locator('h1,h2').first()).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(size.width);
      if (route === '/dashboard') {
        const composer = page.getByTestId('dashboard-composer');
        await expect(page.getByRole('textbox', { name: '创作灵感' })).toBeVisible();
        const input = await page.getByTestId('dashboard-inspiration-input').boundingBox();
        const action = await page.getByTestId('create-project-button').boundingBox();
        expect(input!.y + input!.height).toBeLessThanOrEqual(action!.y);
        expect(action!.height).toBeGreaterThanOrEqual(size.width < 768 ? 44 : 40);
        expect(action!.y + action!.height).toBeLessThan(size.height);
        await page.getByTestId('dashboard-inspiration-input').focus();
        await expect(composer).not.toHaveCSS('box-shadow', 'none');
        await page.screenshot({ path: testInfo.outputPath(`home-${size.width}.png`) });
        if (size.width >= 768) {
          const home = page.getByRole('link', { name: '首页', exact: true });
          await expect(home).toHaveAttribute('aria-current', 'page');
          await expect(home).toHaveAttribute('href', '/dashboard');
        }
      }
      if (route === '/project/responsive-project-0') await expect(page.getByTestId('chat-input')).toBeVisible();
    }
    expect(errors).toEqual([]);
  });
}

for (const theme of ['light', 'dark']) {
  test(`${theme}: all six accent choices keep readable text and primary actions`, async ({ browser, baseURL }) => {
    test.setTimeout(120_000);
    for (const accent of accents) {
      const context = await browser.newContext({ viewport: sizes[0] });
      try {
        const page = await context.newPage();
        await mockResponsiveApp(page);
        await page.addInitScript(({ theme, accent }) => {
          localStorage.setItem('zenstory-theme', theme);
          localStorage.setItem('zenstory-accent-color', accent);
        }, { theme, accent });
        await page.goto(new URL('/dashboard', baseURL).href);
        await expect(page.getByTestId('create-project-button')).toBeVisible();
        const ratios = await page.evaluate(() => {
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = 1;
          const ctx = canvas.getContext('2d')!;
          const rgba = (css: string) => {
            ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = css; ctx.fillRect(0, 0, 1, 1);
            return Array.from(ctx.getImageData(0, 0, 1, 1).data);
          };
          const luminance = (rgb: number[]) => rgb.slice(0, 3).map(c => {
            const s = c / 255; return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4;
          }).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
          const contrast = (front: number[], back: number[]) => {
            const a = luminance(front), b = luminance(back);
            return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
          };
          const measure = (selector: string, surface: string) => {
            const el = document.querySelector(selector)!;
            const style = getComputedStyle(el);
            const ancestor = rgba(getComputedStyle(document.querySelector(surface)!).backgroundColor);
            const bg = rgba(style.backgroundColor);
            const alpha = bg[3] / 255;
            return contrast(rgba(style.color), bg.map((c, i) => c * alpha + ancestor[i] * (1 - alpha)));
          };
          return {
            primary: measure('[data-testid="create-project-button"]', '[data-testid="dashboard-composer"]'),
            selected: measure('[data-testid="dashboard-project-type-tabs"] [aria-pressed="true"]', '[data-testid="dashboard-composer"]'),
            secondary: measure('.dashboard-start p', '.dashboard-shell'),
            headingFont: getComputedStyle(document.querySelector('h1')!).fontFamily,
            secondarySurface: getComputedStyle(document.querySelector('[data-testid="dashboard-composer"]')!).backgroundColor,
          };
        });
        for (const key of ['primary', 'selected', 'secondary'] as const) expect(ratios[key], `${theme} ${accent} ${key}`).toBeGreaterThanOrEqual(4.5);
        expect(ratios.headingFont).not.toMatch(/Crimson|Serif/);
        if (theme === 'light') expect(ratios.secondarySurface).toBe('rgb(255, 255, 255)');
      } finally { await context.close(); }
    }
  });

  test(`${theme}: dashboard and skill controls meet accessibility checks`, async ({ page }) => {
    test.setTimeout(90_000);
    await mockResponsiveApp(page);
    await page.addInitScript(theme => localStorage.setItem('zenstory-theme', theme), theme);
    for (const route of ['/dashboard', '/dashboard/skills', '/dashboard/billing', '/dashboard/materials']) {
      await page.goto(route);
      await expect(page.locator('h1')).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(results.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
    }
  });
}
