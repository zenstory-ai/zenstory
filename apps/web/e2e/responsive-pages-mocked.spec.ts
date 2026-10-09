import { test, expect } from '@playwright/test';
import { mockResponsiveApp, mockResponsiveApi } from './fixtures-responsive';

const sizes = [{ width: 1280, height: 720 }, { width: 1366, height: 768 }, { width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 768, height: 1024 }];
const publicRoutes = ['/', '/privacy-policy', '/terms-of-service', '/docs', '/docs/getting-started/quick-start', '/pricing', '/auth/callback', '/login', '/register', '/forgot-password', '/verify-email?email=responsive%40example.com'];
const protectedRoutes = ['/onboarding/persona', '/dashboard', '/dashboard/projects', '/dashboard/materials', '/dashboard/skills', '/dashboard/billing', '/project/responsive-project-0', '/project/responsive-project-0/dashboard', '/materials/responsive-material'];

const readyHeadings: Record<string, string> = {
  "/": "从灵感到连载，一套工具写完整部作品",
  "/privacy-policy": "隐私政策",
  "/terms-of-service": "服务条款",
  "/docs": "ZenStory 工作台帮助文档",
  "/docs/getting-started/quick-start": "ZenStory 工作台快速开始：从空项目到第一份可修改提纲",
  "/pricing": "选一个适合你的方案",
  "/auth/callback": "登录 ZenStory",
  "/login": "登录 ZenStory",
  "/register": "创建账号",
  "/forgot-password": "找回密码",
  "/verify-email?email=responsive%40example.com": "验证你的邮箱",
  "/onboarding/persona": "告诉我们你怎么写作",
  "/dashboard": "你好，responsive-user",
  "/dashboard/projects": "我的项目",
  "/dashboard/materials": "素材库",
  "/dashboard/skills": "技能管理",
  "/dashboard/billing": "订阅权益",
  "/project/responsive-project-0": "开始写作",
  "/project/responsive-project-0/dashboard": "小屏布局回归项目",
  "/materials/responsive-material": "素材标题：很长的人物选择与情节发展"
};

for (const viewport of sizes) {
  test(`public and protected pages stay within ${viewport.width}x${viewport.height}`, async ({ browser, baseURL }) => {
    test.setTimeout(180_000);
    for (const route of [...publicRoutes, ...protectedRoutes]) {
      const context = await browser.newContext({ viewport });
      try {
        const page = await context.newPage();
        const errors: string[] = [];
        page.on('pageerror', error => errors.push(error.message));
        if (protectedRoutes.includes(route)) await mockResponsiveApp(page, null, route.includes('materials') ? 'pro' : 'free');
        else {
          await page.addInitScript(() => localStorage.setItem('zenstory-language', 'zh'));
          await mockResponsiveApi(page);
        }
        await page.goto(new URL(route, baseURL).href);
        await expect(page.locator('h1,h2').filter({ hasText: readyHeadings[route] }).first(), route).toBeVisible();
        const expectedPath = route === '/auth/callback' ? '/login' : route.split('?')[0];
        expect(new URL(page.url()).pathname, route).toBe(expectedPath);
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth), { message: route }).toBeLessThanOrEqual(viewport.width);
        // Hidden overflow can mask an oversized fixed shell; inspect its actual bounds too.
        const shell = page.getByTestId('app-layout');
        if (await shell.isVisible()) expect((await shell.boundingBox())!.x + (await shell.boundingBox())!.width, route).toBeLessThanOrEqual(viewport.width);
        expect(errors, route).toEqual([]);
      } finally {
        await context.close();
      }
    }
  });
}

for (const viewport of sizes) {
  test(`settings tabs and material/skill detail controls fit ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockResponsiveApp(page, null, 'pro');
    await page.goto('/dashboard');
    if (viewport.width < 768) {
      await page.getByRole('button', { name: '打开菜单', exact: true }).click();
      await page.getByRole('button', { name: '设置', exact: true }).click();
    } else {
      await page.getByTestId('dashboard-user-panel-toggle').first().click();
      await page.getByTestId('dashboard-open-settings-button').click();
    }
    const dialog = page.getByRole('dialog', { name: '设置', exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveCSS('animation-name', 'scale-in');
    await page.waitForTimeout(250); // Wait for the existing scale-in transition before physical bounds checks.
    const bounds = await dialog.boundingBox();
    for (const id of ['profile', 'general', 'subscription', 'points', 'agent', 'referral']) {
      const tab = page.getByTestId('settings-tab-' + id);
      if (await tab.count() === 0) continue; // Optional points panel is disabled in some product builds.
      await tab.click();
      const box = await tab.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
      expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width);
    }
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await page.goto('/materials/responsive-material');
    const search = page.getByPlaceholder('搜索...', { exact: true });
    if (viewport.width < 768) expect((await search.boundingBox())!.width).toBeGreaterThanOrEqual(200);
    await page.getByRole('button', { name: '章节', exact: true }).click();
    await page.getByText('第1章 很长的标题与人物选择', { exact: true }).click();
    await expect(page.getByText('章节摘要。'.repeat(30), { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    await page.goto('/dashboard/skills');
    await page.getByRole('button', { name: '我的技能', exact: true }).click();
    await page.getByRole('button', { name: '编辑技能', exact: true }).click();
    const edit = page.getByRole('dialog', { name: '编辑技能', exact: true });
    await expect(edit).toBeVisible();
    const save = edit.getByRole('button', { name: '保存', exact: true });
    await save.scrollIntoViewIfNeeded();
    expect((await save.boundingBox())!.y + (await save.boundingBox())!.height).toBeLessThanOrEqual(viewport.height);
  });
}
