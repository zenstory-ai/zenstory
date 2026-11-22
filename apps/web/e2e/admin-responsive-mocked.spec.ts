import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { bootstrapAdminSession, longText, users } from './fixtures/admin-responsive';

const routes = [
  ['dashboard', '/admin', '1,289'], ['ux-health', '/admin/ux-health', '88.0%'], ['acquisition', '/admin/acquisition', 'www.google.com'],
  ['users', '/admin/users', users[0].username], ['prompts', '/admin/prompts', 'novel'],
  ['prompt-new', '/admin/prompts/new', null], ['prompt-novel', '/admin/prompts/novel', null],
  ['skills', '/admin/skills', 'Long-form narrative continuity reviewer'], ['codes', '/admin/codes', 'RESPONSIVE-AUDIT-LONG-CODE-2026'],
  ['subscriptions', '/admin/subscriptions', users[0].email], ['plans', '/admin/plans', 'Professional Writer Plan'],
  ['audit-logs', '/admin/audit-logs', 'super-administrator-with-long-name'], ['feedback', '/admin/feedback', users[0].email],
] as const;

const viewports = [
  { name: 'small-laptop', width: 1280, height: 720 },
  { name: 'laptop', width: 1366, height: 768 },
  { name: 'large-laptop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
  { name: 'tablet', width: 768, height: 1024 },
] as const;

test.describe('Admin responsive routes with populated mocked data', () => {
  test.setTimeout(180_000);

  for (const viewport of viewports) {
    test(`${viewport.name}: all admin pages stay within the document viewport`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await bootstrapAdminSession(page);
      const pageErrors: string[] = [];
      page.on('pageerror', (error) => pageErrors.push(error.message));

      for (const [name, path, readyText] of routes) {
        await page.goto(path);
        await expect(page.locator('.admin-page')).toBeVisible();
        if (name === 'prompt-new') {
          await expect(page.locator('textarea').first()).toBeVisible();
        } else if (name === 'prompt-novel') {
          await expect(page.locator('textarea').first()).toHaveValue(longText);
        } else {
          await expect.poll(() => page.getByText(readyText!, { exact: false }).evaluateAll((elements) =>
            elements.some((element) => {
              const style = getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
            }),
          )).toBe(true);
        }
        if (name === 'dashboard') {
          const growth = page.locator('section[aria-labelledby="growth-title"]');
          await expect(growth).toBeVisible();
          await expect(growth.getByText('¥1234.56', { exact: true })).toBeVisible();
          const dailyRow = growth.getByRole('row').filter({ hasText: '2026-03-07' });
          await expect(dailyRow).toBeVisible();
          await expect(dailyRow).toContainText('17');
          await expect(dailyRow).toContainText('13');
          await expect(dailyRow).toContainText('4');
          await expect(dailyRow).toContainText('¥321.09');
        }
        await expect(page.locator('body')).not.toContainText(/something went wrong|页面加载失败/i);
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

        const artifactDir = process.env.ADMIN_RESPONSIVE_ARTIFACT_DIR;
        if (artifactDir) {
          await page.screenshot({ path: `${artifactDir}/${viewport.name}-${name}.png`, fullPage: true });
        }
      }

      expect(pageErrors, `page errors at ${viewport.name}`).toEqual([]);
      await testInfo.attach(`${viewport.name}-coverage`, { body: Buffer.from(routes.map(([, path]) => path).join('\n')), contentType: 'text/plain' });
    });
  }

  test('removed admin routes redirect to their canonical destination', async ({ page }) => {
    await bootstrapAdminSession(page);
    await page.goto('/admin/inspirations');
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.locator('.admin-page')).toBeVisible();
  });

  test('tablet uses compact cards and readable metric widths in the narrowed admin content area', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await bootstrapAdminSession(page);

    for (const path of ['/admin/users', '/admin/codes', '/admin/subscriptions', '/admin/audit-logs', '/admin/feedback']) {
      await page.goto(path);
      await expect(page.locator('.admin-page')).toBeVisible();
      await expect(page.locator('table')).toBeHidden();
    }

    await page.goto('/admin');
    const metricGrid = page.locator('.admin-page .grid.grid-cols-2').first();
    await expect(metricGrid).toBeVisible();
    const minimumCardWidth = await metricGrid.locator(':scope > *').evaluateAll((cards) =>
      Math.min(...cards.map((card) => card.getBoundingClientRect().width)),
    );
    expect(minimumCardWidth).toBeGreaterThan(150);
  });

  test('representative mobile dialogs and forms fit and keep their actions reachable', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await bootstrapAdminSession(page);

    for (const scenario of [
      { path: '/admin/users', open: () => page.getByRole('button', { name: /^编辑$|^edit$/i }).first().click() },
      { path: '/admin/codes', open: () => page.getByRole('button', { name: /创建|create/i }).first().click() },
      { path: '/admin/plans', open: () => page.getByRole('button', { name: /编辑|edit/i }).first().click() },
      { path: '/admin/audit-logs', open: () => page.getByRole('button', { name: /查看详情|view details/i }).first().click() },
    ]) {
      await page.goto(scenario.path);
      await scenario.open();
      const modal = page.getByRole('dialog');
      await expect(modal).toBeVisible();
      const finalAction = modal.getByRole('button').last();
      await finalAction.scrollIntoViewIfNeeded();
      await expect(finalAction).toBeVisible();
      const geometry = await modal.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: innerWidth };
      });
      expect(geometry.left).toBeGreaterThanOrEqual(0);
      expect(geometry.right).toBeLessThanOrEqual(geometry.width);
      await expect(modal).toHaveAttribute('role', 'dialog');
      const audit = await new AxeBuilder({ page }).include('[role="dialog"][aria-modal="true"]').analyze();
      expect(audit.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? '')).map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) }))).toEqual([]);
      await finalAction.focus();
      await page.keyboard.press('Tab');
      expect(await modal.evaluate(el => el.contains(document.activeElement))).toBe(true);
      await page.keyboard.press('Escape');
      await expect(modal).toBeHidden();
    }
  });

  test('admin navigation closes accessibly, returns to the app and resets page scroll', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await bootstrapAdminSession(page);
    await page.goto('/admin');
    await expect(page.locator('.admin-page')).toBeVisible();
    await page.locator('main').evaluate(el => { el.scrollTop = 500; });
    await page.getByRole('button', { name: '打开管理菜单' }).click();
    const drawer = page.getByRole('dialog', { name: '管理后台' });
    await expect(drawer.getByRole('link', { name: '概览', exact: true })).toBeFocused();
    await expect(page.locator('main')).toHaveAttribute('inert', '');
    await expect(drawer.getByRole('link', { name: '返回应用' })).toHaveAttribute('href', '/dashboard');
    await drawer.getByRole('button', { name: '关闭管理菜单' }).click();
    await expect(page.getByRole('button', { name: '打开管理菜单' })).toBeFocused();
    await page.getByRole('button', { name: '打开管理菜单' }).click();
    await drawer.getByRole('link', { name: '用户', exact: true }).click();
    await expect(page.getByRole('heading', { name: '用户', level: 1 })).toBeVisible();
    await expect.poll(() => page.locator('main').evaluate(el => el.scrollTop)).toBe(0);
    await page.getByRole('button', { name: '返回应用', exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('prompt labels focus their fields and user states stay on one line', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await bootstrapAdminSession(page);
    await page.goto('/admin/prompts/new');
    await expect(page.locator('textarea').first()).toBeVisible();
    for (const id of ['role-definition', 'capabilities', 'directory-structure', 'content-structure', 'file-types', 'writing-guidelines']) {
      await page.locator(`label[for="prompt-${id}"]`).click();
      await expect(page.locator(`#prompt-${id}`)).toBeFocused();
    }
    await page.goto('/admin/users');
    await expect(page.locator('tbody tr')).toHaveCount(3);
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
    const status = page.locator('tbody tr').first().locator('td').nth(2).locator('span');
    expect((await status.boundingBox())!.width).toBeGreaterThan(30);
    await expect(status).toHaveCSS('white-space', 'nowrap');
  });

  for (const theme of ['light', 'dark'] as const) {
    test(`${theme}: representative admin pages have accessible controls and contrast`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.addInitScript(value => localStorage.setItem('zenstory-theme', value), theme);
      await bootstrapAdminSession(page);
      for (const path of ['/admin', '/admin/ux-health', '/admin/users', '/admin/prompts/new', '/admin/plans']) {
        await page.goto(path);
        await expect(page.locator('.admin-page')).toBeVisible();
        if (path === '/admin') await expect(page.getByText('¥1234.56', { exact: true })).toBeVisible();
        if (path === '/admin/ux-health') await expect(page.getByText('88.0%', { exact: true })).toBeVisible();
        if (path === '/admin/users') await expect(page.getByRole('button', { name: '编辑', exact: true }).first()).toBeVisible();
        if (path === '/admin/plans') await expect(page.getByText('Professional Writer Plan').first()).toBeVisible();
        const result = await new AxeBuilder({ page }).analyze();
        expect(result.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? '')).map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), path).toEqual([]);
      }
    });
  }
});
