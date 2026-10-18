import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

async function firstWorkFixture(page: Page, language: 'zh' | 'en', options: { deleted?: boolean; deletedFile?: boolean; alreadyCreated?: boolean; failInitialize?: boolean } = {}) {
  await mockResponsiveApp(page, null, 'free', language);
  const project = { ...responsiveProjects[0], name: language === 'zh' ? '我的首部作品' : 'My First Work' };
  let initialized = options.alreadyCreated ?? false;
  let failInitialize = options.failInitialize;
  let content = '';
  let openedAt: string | null = null;
  const calls: { path: string; method: string; body: unknown }[] = [];
  const state = () => ({ status: initialized ? 'created' : 'pending', project_id: initialized ? project.id : null,
    file_id: initialized ? 'draft-0' : null, available: initialized && !options.deleted && !options.deletedFile, static_guidance: initialized && !options.deleted,
    opened_at: openedAt, first_manual_write_at: content ? '2026-10-14T10:00:00Z' : null, first_ai_requested_at: null });
  const file = () => ({ id: 'draft-0', project_id: project.id, title: language === 'zh' ? '第一章' : 'Chapter 1', content,
    file_type: 'draft', parent_id: 'folder-drafts', order: 0, metadata: { first_work: true }, created_at: '2026-10-14T09:00:00Z', updated_at: '2026-10-14T09:00:00Z' });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = request.postDataJSON();
    calls.push({ path, method, body });
    const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/api/v1/workspace/first-work') {
      if (method === 'POST') {
        expect(request.headers()['accept-language']).toBe(language);
        expect(body.preferred_type).toBe('novel');
        if (failInitialize) { failInitialize = false; return json({ detail: 'Temporary failure' }, 503); }
        initialized = true;
      }
      return json(state());
    }
    if (path === '/api/v1/workspace/first-work/open') { openedAt = '2026-10-14T09:05:00Z'; return json(state()); }
    if (path === '/api/v1/projects') return json(initialized && !options.deleted ? [project] : []);
    if (path.endsWith('/recent')) return json([]);
    if (path === '/api/v1/files/draft-0') {
      if (method === 'PUT') content = body.content;
      return json(file());
    }
    if (path.includes('/subscription/quota')) return json({ ai_conversations: { used: 3, limit: 3, remaining: 0, reset_at: '2026-10-15T16:00:00Z' }, projects: { used: 1, limit: 3 }, material_decompositions: { used: 0, limit: 0 }, skill_creates: { used: 0, limit: 3 }, inspiration_copies: { used: 0, limit: 0 } });
    if (path.includes('/agent/stream') || path.includes('/agent/suggest')) return json({ detail: 'UNEXPECTED_AUTOMATIC_AI' }, 500);
    return route.fallback();
  });
  return { calls, getContent: () => content };
}
const aiCalls = (calls: { path: string }[]) => calls.filter(call => /\/agent\/(suggest|stream)/.test(call.path));

for (const theme of ['light', 'dark'] as const) {
for (const width of [390, 768, 1440]) {
  for (const language of ['zh', 'en'] as const) {
    test(`static first work ${language} ${width} ${theme}: direct manuscript, focus only, exhausted quota manual save`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const fixture = await firstWorkFixture(page, language);
      await page.addInitScript(theme => localStorage.setItem('zenstory-theme', theme), theme);
      const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto('/dashboard');
      await expect(page.locator('html')).toHaveClass(new RegExp(theme));
      const entry = page.getByTestId('first-work-entry');
      await expect(entry).toBeVisible();
      const open = entry.getByRole('button', { name: language === 'zh' ? '开始写作' : 'Start writing' });
      expect(fixture.calls.filter(call => call.path.endsWith('/first-work/open'))).toHaveLength(0);
      await page.screenshot({ path: test.info().outputPath('dashboard.png') });
      await open.click();
      await expect(page).toHaveURL(/\/project\/responsive-project-0\?file=draft-0/);
      const guidance = page.getByTestId('first-work-guidance');
      await expect(guidance).toBeVisible();
      const heading = await page.getByTestId('chat-panel-header-title').locator('span').first().boundingBox();
      const actions = await page.getByTestId('chat-panel-header-actions').boundingBox();
      expect(heading!.x + heading!.width).toBeLessThanOrEqual(actions!.x);
      const paste = guidance.getByRole('button', { name: language === 'zh' ? '自己写' : 'Write on my own' });
      const ai = guidance.getByRole('button', { name: language === 'zh' ? '与 AI 创作' : 'Write with AI' });
      for (const button of [paste, ai]) {
        const box = (await button.boundingBox())!;
        expect(box.width).toBeGreaterThan(44); expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
        expect(await button.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      }
      await page.screenshot({ path: test.info().outputPath('manuscript.png') });
      await paste.click();
      const body = page.locator('[data-editor-scroll-container] textarea');
      await expect(body).toBeFocused(); await expect(body).toHaveValue('');
      await ai.click();
      const chat = page.getByTestId('chat-input');
      await expect(chat).toBeFocused(); await expect(chat).toHaveValue('');
      await chat.fill('Preserve my existing chat draft');
      if (width < 768) await page.getByRole('tab', { name: language === 'zh' ? '编辑' : 'Editor', exact: true }).click();
      await paste.click(); await ai.click();
      await expect(chat).toBeFocused(); await expect(chat).toHaveValue('Preserve my existing chat draft');
      expect(aiCalls(fixture.calls)).toHaveLength(0);
      if (width < 768) await page.getByRole('tab', { name: language === 'zh' ? '编辑' : 'Editor', exact: true }).click();
      await body.fill('The author’s first paragraph, without AI.');
      await expect(guidance).toHaveCount(0);
      await page.getByRole('button', { name: language === 'zh' ? '保存' : 'Save', exact: true }).click();
      await expect.poll(fixture.getContent).toBe('The author’s first paragraph, without AI.');
      expect(aiCalls(fixture.calls)).toHaveLength(0);
      await page.reload();
      await expect(body).toHaveValue('The author’s first paragraph, without AI.');
      await page.goto('/dashboard');
      await expect(page.getByTestId('dashboard-inspiration-input')).toBeVisible();
      await expect(entry).toHaveCount(0);
      await expect(page.getByRole('heading', { name: language === 'zh' ? '我的首部作品' : 'My First Work', exact: true })).toBeVisible();
      await page.screenshot({ path: test.info().outputPath('dashboard-after-writing.png') });
      expect(fixture.calls.filter(call => call.path === '/api/v1/workspace/first-work' && call.method === 'POST')).toHaveLength(1);
      expect(aiCalls(fixture.calls)).toHaveLength(0); expect(errors).toEqual([]);
    });
  }
}
}

test('first-work initialization failure retries explicitly; a deleted first work is never recreated', async ({ page }) => {
  const fixture = await firstWorkFixture(page, 'en', { failInitialize: true, deleted: true });
  await page.goto('/dashboard');
  await expect(page.getByText('Your writing space is not ready yet.', { exact: false })).toBeVisible();
  expect(fixture.calls.filter(call => call.method === 'POST' && call.path.endsWith('/first-work'))).toHaveLength(1);
  await expect(page.getByTestId('dashboard-inspiration-input')).toBeEnabled();
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByText('Your writing space is not ready yet.', { exact: false })).toHaveCount(0);
  await expect(page.getByTestId('first-work-entry')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId('dashboard-inspiration-input')).toBeVisible();
  expect(fixture.calls.filter(call => call.method === 'POST' && call.path.endsWith('/first-work'))).toHaveLength(2);
  expect(aiCalls(fixture.calls)).toHaveLength(0);
});


test('opening a first-work project after its original manuscript was deleted records open without requesting AI or recreating work', async ({ page }) => {
  const fixture = await firstWorkFixture(page, 'en', { alreadyCreated: true, deletedFile: true });
  await page.goto('/project/responsive-project-0');
  await expect(page.getByTestId('app-layout')).toBeVisible();
  await expect.poll(() => fixture.calls.filter(call => call.path.endsWith('/first-work/open')).length).toBe(1);
  await expect(page.getByTestId('first-work-guidance')).toHaveCount(0);
  expect(fixture.calls.filter(call => call.method === 'POST' && call.path.endsWith('/first-work'))).toHaveLength(0);
  expect(aiCalls(fixture.calls)).toHaveLength(0);
});

test('billing continuation keeps its route and does not initialize first work', async ({ page }) => {
  const fixture = await firstWorkFixture(page, 'en');
  await page.goto('/dashboard/billing?plan=pro&cycle=yearly&source=landing');
  await expect(page.getByRole('heading', { name: /subscription|plan|billing/i }).first()).toBeVisible();
  expect(page.url()).toContain('/dashboard/billing?plan=pro&cycle=yearly&source=landing');
  expect(fixture.calls.filter(call => call.path.endsWith('/first-work'))).toHaveLength(0);
  expect(aiCalls(fixture.calls)).toHaveLength(0);
});
