import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';
import { bootstrapAdminSession } from './fixtures/admin-responsive';

test.use({ hasTouch: true });

// Headless browsers do not open an OS keyboard. Model Safari's independently
// resized/panned visual viewport; all API data stays local and no form is sent.
declare global {
  interface Window {
    keyboardViewport: EventTarget & { height: number; offsetTop: number; scale: number };
  }
}

test.beforeEach(async ({ page }) => {
  await page.route(/^https?:\/\/(?!127\.0\.0\.1(?::|\/)|localhost(?::|\/))/, route => route.abort());
  await page.addInitScript(() => {
    const viewport = Object.assign(new EventTarget(), {
      height: innerHeight, width: innerWidth, offsetTop: 0, offsetLeft: 0, scale: 1,
    });
    window.keyboardViewport = viewport;
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: viewport });
  });
});

async function keyboard(page: Page, height: number, offsetTop = 0) {
  await page.evaluate(async ({ height, offsetTop }) => {
    Object.assign(window.keyboardViewport, { height, offsetTop });
    window.keyboardViewport.dispatchEvent(new Event('resize'));
    window.keyboardViewport.dispatchEvent(new Event('scroll'));
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  }, { height, offsetTop });
}

async function expectViewport(shell: Locator, height: number, top: number) {
  await expect.poll(async () => {
    const rect = (await shell.boundingBox())!;
    return { height: Math.round(rect.height), top: Math.round(rect.y) };
  }).toEqual({ height, top });
}

for (const width of [320, 390]) {
  test(`chat follows keyboard resize and Safari pan, then restores at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await mockResponsiveApp(page);
    await page.goto('/project/responsive-project-0');
    await page.getByRole('tab', { name: 'AI', exact: true }).click();
    const input = page.getByTestId('chat-input');
    await input.fill('键盘打开时保留这段草稿');
    const shell = page.locator('header').first().locator('..');
    await keyboard(page, 400, 160);
    await expectViewport(shell, 400, 160);
    await expect(page.getByTestId('bottom-tabs')).toHaveCount(0);
    const inputBox = (await input.boundingBox())!;
    expect(inputBox.y).toBeGreaterThanOrEqual(160);
    expect(inputBox.y + inputBox.height).toBeLessThanOrEqual(560);
    expect(560 - inputBox.y - inputBox.height).toBeLessThanOrEqual(40);
    await page.screenshot({ path: test.info().outputPath(`chat-keyboard-${width}.png`), clip: { x: 0, y: 160, width, height: 400 } });
    await expect(page.locator('html')).toHaveCSS('overflow-y', 'hidden');
    await expect(page.locator('body')).toHaveCSS('overflow-y', 'hidden');
    // A scroll-only event must also reposition the shell, without clearing input.
    await page.evaluate(() => {
      window.keyboardViewport.offsetTop = 200;
      window.keyboardViewport.dispatchEvent(new Event('scroll'));
    });
    await expectViewport(shell, 400, 200);
    await input.blur();
    await keyboard(page, 844);
    await expectViewport(shell, 844, 0);
    await expect(page.getByTestId('bottom-tabs')).toBeVisible();
    await expect(input).toHaveValue('键盘打开时保留这段草稿');
    await page.screenshot({ path: test.info().outputPath(`chat-restored-${width}.png`) });
  });
}

test('landscape phone workspace follows the keyboard without changing the tablet panel layout', async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await page.getByTestId('chat-input').focus();
  await expect(page.getByTestId('chat-input')).toHaveCSS('font-size', '16px');
  await keyboard(page, 240, 40);
  await expectViewport(page.getByTestId('app-layout'), 240, 40);
  await expect(page.locator('html')).toHaveCSS('overflow-y', 'hidden');
  await expect(page.locator('[data-panel]')).toHaveCount(3);
  await page.getByTestId('chat-input').blur();
  await keyboard(page, 390);
  await expectViewport(page.getByTestId('app-layout'), 390, 0);
});

test('dismissing the keyboard while pinch-zoomed restores the shell and bottom tabs', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await page.locator('#chat-tab').click();
  await page.getByTestId('chat-input').focus();
  await keyboard(page, 400, 160);
  await expect(page.getByTestId('bottom-tabs')).toHaveCount(0);
  await page.evaluate(() => { window.keyboardViewport.scale = 2; });
  await keyboard(page, 200, 100);
  // Keep focus, as the iOS keyboard-dismiss button does; closing it must not
  // retain a shrunken shell until the user happens to zoom back to exactly 1.
  await keyboard(page, 422);
  await expectViewport(page.locator('header').first().locator('..'), 844, 0);
  await expect(page.getByTestId('bottom-tabs')).toBeVisible();
});

for (const surface of ['dashboard', 'projects', 'material', 'admin'] as const) {
  test(`${surface} input keeps its viewport shell and internal scrolling above the keyboard`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    if (surface === 'admin') await bootstrapAdminSession(page);
    else await mockResponsiveApp(page, null, 'pro');
    const route = { dashboard: '/dashboard', projects: '/dashboard/projects', material: '/materials/responsive-material', admin: '/admin/users' }[surface];
    await page.goto(route);
    const shell = page.locator(surface === 'admin' ? '.admin-shell' : surface === 'material' ? '#root .ui-sans-headings' : '.dashboard-shell').first();
    const input = surface === 'dashboard' ? page.getByTestId('dashboard-inspiration-input') : page.locator('main input, #root input').filter({ visible: true }).first();
    await expect(input).toBeVisible();
    await input.focus();
    await keyboard(page, 420, 120);
    await expectViewport(shell, 420, 120);
    await expect(page.locator('html')).toHaveCSS('overflow-y', 'hidden');
    await expect(input).toHaveCSS('font-size', '16px');
    await input.scrollIntoViewIfNeeded();
    const rect = (await input.boundingBox())!;
    expect(rect.y).toBeGreaterThanOrEqual(120);
    expect(rect.y + rect.height).toBeLessThanOrEqual(540);
    await input.blur();
    await keyboard(page, 844);
    await expectViewport(shell, 844, 0);
  });
}

test('project search list and actions remain inside the keyboard viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await page.locator('header').getByText('小屏布局回归项目', { exact: true }).click();
  const input = page.getByPlaceholder('搜索项目', { exact: true });
  await expect(input).toBeFocused();
  await keyboard(page, 400, 120);
  const dropdown = input.locator('xpath=ancestor::div[contains(@class,"rounded-xl")]');
  await expect.poll(async () => {
    const rect = (await dropdown.boundingBox())!;
    return rect.y + rect.height;
  }).toBeLessThanOrEqual(520);
  const lastProject = dropdown.getByText(responsiveProjects.at(-1)!.name, { exact: true });
  await lastProject.scrollIntoViewIfNeeded();
  const last = (await lastProject.boundingBox())!;
  expect(last.y + last.height).toBeLessThanOrEqual(520);
});

test('file search results scroll inside the keyboard viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await page.locator('#files-tab').click();
  const search = page.getByPlaceholder('搜索文件', { exact: true }).filter({ visible: true });
  await search.fill('章');
  await keyboard(page, 400, 120);
  const results = page.getByTestId('search-results-dropdown');
  await expect(results).toBeVisible();
  const rect = (await results.boundingBox())!;
  expect(rect.y).toBeGreaterThanOrEqual(120);
  expect(rect.y + rect.height).toBeLessThanOrEqual(520);
  const last = results.getByRole('option').last();
  await last.scrollIntoViewIfNeeded();
  expect((await last.boundingBox())!.y + (await last.boundingBox())!.height).toBeLessThanOrEqual(520);
});

test('editor title and manuscript drafts survive keyboard changes and mobile panel switches', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResponsiveApp(page);
  const file = { id: 'draft-0', project_id: responsiveProjects[0].id, title: '键盘回归正文', content: '作者原有正文。', file_type: 'draft', parent_id: 'folder-drafts', order: 0, metadata: null, created_at: '2026-10-15T00:00:00Z', updated_at: '2026-10-15T00:00:00Z' };
  await page.route('**/api/v1/files/draft-0', async route => {
    if (route.request().method() === 'PUT') Object.assign(file, route.request().postDataJSON());
    await route.fulfill({ json: file });
  });
  await page.goto('/project/responsive-project-0?file=draft-0');
  const title = page.getByPlaceholder('请输入标题', { exact: true });
  const manuscript = page.locator('[data-editor-scroll-container="true"] textarea');
  await expect(manuscript).toHaveValue(file.content);
  await title.fill('正在输入的新标题');
  await keyboard(page, 400, 120);
  const titleRect = (await title.boundingBox())!;
  expect(titleRect.y).toBeGreaterThanOrEqual(120);
  expect(titleRect.y + titleRect.height).toBeLessThanOrEqual(520);
  await manuscript.fill('作者在手机键盘里输入的新正文。');
  await expect(manuscript).toBeFocused();
  await expect(manuscript).toBeInViewport();
  await manuscript.blur();
  await keyboard(page, 844);
  await page.locator('#chat-tab').click();
  await page.locator('#editor-tab').click();
  await expect(title).toHaveValue('正在输入的新标题');
  await expect(manuscript).toHaveValue('作者在手机键盘里输入的新正文。');
});

test('documentation search drawer fits the keyboard without locking the reader after close', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem('zenstory-language', 'en'));
  await page.goto('/docs/user-guide/editor/');
  const trigger = page.getByRole('button', { name: 'Menu', exact: true });
  await trigger.click();
  const drawer = page.getByRole('dialog', { name: 'Documentation Center', exact: true });
  await drawer.getByRole('textbox', { name: 'Search documentation' }).fill('editor');
  await keyboard(page, 400, 120);
  await expectViewport(drawer, 400, 120);
  const lastLink = drawer.getByRole('navigation').getByRole('link').last();
  await lastLink.focus();
  expect((await lastLink.boundingBox())!.y + (await lastLink.boundingBox())!.height).toBeLessThanOrEqual(520);
  await page.keyboard.press('Escape');
  await keyboard(page, 844);
  await expect(drawer).toHaveCount(0);
  await expect(page.locator('html')).not.toHaveCSS('overflow-y', 'hidden');
  await expect(page.locator('body')).not.toHaveCSS('overflow-y', 'hidden');
});

test('settings and skill edit dialogs fit the visual viewport with scrollable fields and actions', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockResponsiveApp(page, null, 'pro');
  await page.goto('/dashboard/skills');
  await page.getByRole('button', { name: '打开菜单', exact: true }).click();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  const settings = page.getByRole('dialog', { name: '设置', exact: true });
  await expect(settings).toBeVisible();
  await keyboard(page, 400, 120);
  const settingsRect = (await settings.boundingBox())!;
  expect(settingsRect.y).toBeGreaterThanOrEqual(120);
  expect(settingsRect.y + settingsRect.height).toBeLessThanOrEqual(520);
  await page.keyboard.press('Escape');
  await keyboard(page, 844);
  await page.getByRole('button', { name: '我的技能', exact: true }).click();
  await page.getByRole('button', { name: '编辑技能', exact: true }).click();
  const edit = page.getByRole('dialog', { name: '编辑技能', exact: true });
  const input = edit.locator('textarea').first();
  await input.focus();
  await keyboard(page, 400, 120);
  const rect = (await edit.boundingBox())!;
  expect(rect.y).toBeGreaterThanOrEqual(120);
  expect(rect.y + rect.height).toBeLessThanOrEqual(520);
  await input.scrollIntoViewIfNeeded();
  await expect(input).toBeInViewport();
  const save = edit.getByRole('button', { name: '保存', exact: true });
  await save.scrollIntoViewIfNeeded();
  expect((await save.boundingBox())!.y + (await save.boundingBox())!.height).toBeLessThanOrEqual(520);
  await page.screenshot({ path: test.info().outputPath('skill-keyboard.png') });
});

for (const path of ['/login', '/register', '/forgot-password', '/verify-email?email=reader%40example.com']) {
  test(`${path} keeps natural document scrolling for short keyboard-height screens`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 420 });
    await page.goto(path);
    const input = page.locator('input:not([type="checkbox"]):visible').last();
    await expect(input).toBeVisible();
    await input.focus();
    await input.scrollIntoViewIfNeeded();
    await expect(input).toBeInViewport();
    await expect(input).toHaveCSS('font-size', '16px');
    await expect(page.locator('html')).not.toHaveCSS('overflow-y', 'hidden');
    const submit = page.locator('button[type="submit"]');
    if (await submit.count()) {
      await submit.scrollIntoViewIfNeeded();
      await expect(submit).toBeInViewport();
    }
  });
}
