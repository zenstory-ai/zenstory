import { expect, test, type Page } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

const processing = {
  id: 'polling-material', title: '正在拆解的素材', status: 'processing',
  chapters_count: 0, characters_count: 1, updated_at: '2026-10-16T00:00:00Z',
};
const completed = {
  ...processing, title: '已完成的素材', status: 'completed',
  characters_count: 2, updated_at: '2026-10-16T00:00:03Z',
};
const early = { id: 'early', name: '先出现的角色' };
const late = { id: 'late', name: '最终出现的角色' };

async function mockCompletion(page: Page, holdFirstRequest = false) {
  await mockResponsiveApp(page, null, 'pro');
  const origin = new URL(test.info().project.use.baseURL as string).origin;
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/')) return route.fallback();
    return url.origin === origin ? route.continue() : route.abort('blockedbyclient');
  });
  let done = false;
  let characterRequests = 0;
  let releaseOld = () => {};
  const oldResponse = new Promise<void>(resolve => { releaseOld = resolve; });
  await page.route('**/api/v1/materials/polling-material', route => route.fulfill({ json: done ? completed : processing }));
  await page.route('**/api/v1/materials/polling-material/characters', async route => {
    const first = ++characterRequests === 1;
    if (first && holdFirstRequest) await oldResponse;
    await route.fulfill({ json: first ? (holdFirstRequest ? [] : [early]) : [early, late] });
  });
  await page.goto('/materials/polling-material');
  await expect(page.getByRole('heading', { name: processing.title, exact: true })).toBeVisible();
  return { complete: () => { done = true; }, requests: () => characterRequests, releaseOld };
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test.describe(`material final results at ${viewport.width}px`, () => {
    test.use({ viewport });

    test('reopening a collapsed folder fetches the completed results', async ({ page }) => {
      const state = await mockCompletion(page);
      const folder = page.getByRole('button', { name: /^角色(?:\s+\d+)?$/ });
      await folder.focus();
      await page.keyboard.press('Enter');
      await expect(page.getByText(early.name, { exact: true })).toBeVisible();
      await folder.click();
      state.complete();
      await expect(page.getByRole('heading', { name: completed.title, exact: true })).toBeVisible({ timeout: 8_000 });
      expect(state.requests()).toBe(1);
      await folder.click();
      await expect(page.getByText(late.name, { exact: true })).toBeVisible();
      expect(state.requests()).toBe(2);
    });

    test('an old in-flight response cannot replace the completed results', async ({ page }) => {
      const state = await mockCompletion(page, true);
      try {
        await page.getByRole('button', { name: /^角色(?:\s+\d+)?$/ }).click();
        await expect.poll(state.requests).toBe(1);
        state.complete();
        await expect(page.getByRole('heading', { name: completed.title, exact: true })).toBeVisible({ timeout: 8_000 });
        await expect(page.getByText(late.name, { exact: true })).toBeVisible();
        expect(state.requests()).toBe(2);
        const oldFinished = page.waitForResponse(response => response.url().endsWith('/polling-material/characters'));
        state.releaseOld();
        await oldFinished;
        await expect(page.getByText(late.name, { exact: true })).toBeVisible();
      } finally {
        state.releaseOld();
      }
    });
  });
}
