import { test, expect } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

// Runs without a backend account, model key, or generation request.
const laptops = [{ width: 768, height: 1024 }, { width: 1280, height: 720 }, { width: 1366, height: 768 }, { width: 1440, height: 900 }];

for (const viewport of laptops) {
  for (const saved of [null, 120]) {
    test(`workbench fits ${viewport.width}x${viewport.height}, input ${saved ?? 'auto'}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await mockResponsiveApp(page, saved);
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('/project/responsive-project-0');
      const input = page.getByTestId('chat-input');
      await expect(input).toBeVisible();
      const geometry = await page.evaluate(() => {
        const input = document.querySelector<HTMLTextAreaElement>('[data-testid="chat-input"]')!;
        const shell = document.querySelector('[data-testid="app-layout"]')!.getBoundingClientRect();
        const chat = document.querySelector('[data-testid="chat-panel"]')!.getBoundingClientRect();
        return { shellRight: shell.right, chatRight: chat.right, viewport: innerWidth, inputClient: input.clientHeight, inputScroll: input.scrollHeight };
      });
      expect(geometry.shellRight).toBeLessThanOrEqual(geometry.viewport);
      expect(geometry.chatRight).toBeLessThanOrEqual(geometry.viewport);
      expect(geometry.inputScroll).toBeLessThanOrEqual(geometry.inputClient);
      if (saved !== null) expect((await page.getByTestId('chat-input-panel').boundingBox())!.height).toBeGreaterThanOrEqual(144);
      const send = await page.getByTestId('send-button').boundingBox();
      expect(send!.y + send!.height).toBeLessThanOrEqual(viewport.height);
      expect(errors).toEqual([]);
    });
  }

  test(`dashboard owns its scroll at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockResponsiveApp(page);
    await page.goto('/dashboard');
    await expect(page.locator('main')).toBeVisible();
    const geometry = await page.evaluate(() => {
      const main = document.querySelector('main')!;
      main.scrollTop = main.scrollHeight;
      return { height: innerHeight, documentHeight: document.documentElement.scrollHeight, mainHeight: main.clientHeight, mainScroll: main.scrollTop, contentHeight: main.scrollHeight };
    });
    expect(geometry.documentHeight).toBeLessThanOrEqual(geometry.height);
    expect(geometry.mainHeight).toBeLessThanOrEqual(geometry.height);
    if (geometry.contentHeight > geometry.mainHeight) expect(geometry.mainScroll).toBeGreaterThan(0);
  });
}

for (const width of [768, 1280]) {
test(`panes retain usable widths when dragged to their minimum at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 720 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await expect(page.getByTestId('chat-input')).toBeVisible();
  const separators = page.locator('[role="separator"]');
  const first = await separators.nth(0).boundingBox();
  await page.mouse.move(first!.x + first!.width / 2, first!.y + first!.height / 2);
  await page.mouse.down();
  await page.mouse.move(0, first!.y + first!.height / 2, { steps: 10 });
  await page.mouse.up();
  const panels = page.locator('[data-panel]');
  const groupWidth = (await page.locator('main').boundingBox())!.width;
  expect((await panels.nth(0).boundingBox())!.width).toBeGreaterThanOrEqual(width === 768 ? groupWidth * 0.2 - 1 : 179);
  const last = await separators.nth(1).boundingBox();
  await page.mouse.move(last!.x + last!.width / 2, last!.y + last!.height / 2);
  await page.mouse.down();
  await page.mouse.move(width, last!.y + last!.height / 2, { steps: 10 });
  await page.mouse.up();
  expect((await page.getByTestId('chat-panel').boundingBox())!.width).toBeGreaterThanOrEqual(width === 768 ? 279 : 299);
});
}

test('tool status stays together and the complete filename wraps in a narrow chat pane', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await expect(page.getByTestId('chat-input')).toBeVisible();
  const separator = await page.locator('[role="separator"]').nth(1).boundingBox();
  await page.mouse.move(separator!.x + separator!.width / 2, separator!.y + separator!.height / 2);
  await page.mouse.down();
  await page.mouse.move(1280, separator!.y + separator!.height / 2, { steps: 10 });
  await page.mouse.up();
  const label = page.getByText('已创建正文', { exact: true }).first();
  await expect(label).toBeVisible();
  const geometry = await label.evaluate(element => ({ height: element.getBoundingClientRect().height, line: parseFloat(getComputedStyle(element).lineHeight) }));
  expect(geometry.height).toBeLessThanOrEqual(geometry.line + 1);
  const title = page.getByTestId('message-list').getByText('第54章 消失的第五个人', { exact: true }).first();
  await expect(title).toBeVisible();
  const bounds = await title.boundingBox();
  const chat = await page.getByTestId('chat-panel').boundingBox();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(chat!.x + chat!.width);
});

test('expanded empty-editor actions remain reachable on a short viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 480 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await page.getByText('更多选项', { exact: true }).click();
  const heading = page.getByRole('heading', { name: '开始写作' });
  const top = await heading.boundingBox();
  expect(top!.y).toBeGreaterThanOrEqual(48);
  const last = page.getByTestId('editor-panel').getByText('搜索文件', { exact: true });
  await last.scrollIntoViewIfNeeded();
  const bottom = await last.boundingBox();
  expect(bottom!.y + bottom!.height).toBeLessThanOrEqual(480);
});
