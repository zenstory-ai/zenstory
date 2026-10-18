import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

const ENABLED = process.env.E2E_ENABLE_VISUAL_REGRESSION_E2E === 'true';
const NOW = '2026-10-17T08:00:00Z';
const project = { ...responsiveProjects[0], name: '长安夜雨', description: '城中旧案与人物选择', updated_at: NOW };
const manuscript = '雨停以后，沈砚才推开书铺的门。\n\n掌柜把灯移近了些，桌上那封旧信仍没有拆。';
const chapter = { id: 'visual-draft-1', project_id: project.id, title: '第一章 灯下', file_type: 'draft', parent_id: 'visual-drafts', order: 0, content: manuscript, metadata: null, version: 1, created_at: NOW, updated_at: NOW };
const session = { id: 'visual-session', project_id: project.id, user_id: 'responsive-user', title: '新对话', is_active: true, message_count: 0, created_at: NOW, updated_at: NOW };
const answer = '可以从旧信的落款切入，让沈砚发现寄信人正是三年前失踪的掌柜。';

async function prepareVisualData(page: Page) {
  await page.clock.setFixedTime(new Date(NOW));
  await mockResponsiveApp(page);
  let completedRounds = 0;
  await page.route('**/api/v1/subscription/quota*', route => route.fulfill({ json: {
    ai_conversations: { used: completedRounds, limit: 3 },
    projects: { used: 1, limit: 3 },
    material_decompositions: { used: 0, limit: 5 },
    skill_creates: { used: 0, limit: 3 },
    inspiration_copies: { used: 0, limit: 10 },
  } }));
  await page.addInitScript(() => localStorage.setItem('zenstory-theme', 'light'));
  await page.route('**/api/v1/projects', route => route.fulfill({ json: [project] }));
  await page.route('**/api/v1/projects/*/file-tree', route => route.fulfill({ json: { tree: [
    { id: 'visual-lore', title: '世界观', file_type: 'folder', parent_id: null, order: 0, metadata: { folder_type: 'lore' }, children: [] },
    { id: 'visual-characters', title: '角色卡', file_type: 'folder', parent_id: null, order: 1, metadata: { folder_type: 'character' }, children: [] },
    { id: 'visual-outlines', title: '大纲', file_type: 'folder', parent_id: null, order: 2, metadata: { folder_type: 'outline' }, children: [] },
    { id: 'visual-drafts', title: '正文', file_type: 'folder', parent_id: null, order: 3, metadata: { folder_type: 'draft' }, children: [{ ...chapter, children: [] }] },
  ] } }));
  await page.route('**/api/v1/files/visual-draft-1', route => route.fulfill({ json: chapter }));
  await page.route('**/api/v1/projects/*/files*', route => route.fulfill({ json: [chapter] }));
  await page.route('**/api/v1/chat/session/*/recent*', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/chat/session/*/new*', route => route.fulfill({ json: session }));
  await page.route('**/api/v1/agent/stream', async route => {
    expect(route.request().method()).toBe('POST');
    completedRounds++;
    await route.fulfill({
      contentType: 'text/event-stream',
      body: `event: content\ndata: ${JSON.stringify({ text: answer })}\n\nevent: done\ndata: ${JSON.stringify({ assistant_message_id: 'visual-assistant-1', session_id: session.id })}\n\n`,
    });
  });
}

async function openVisualProject(page: Page) {
  await page.goto(`/project/${project.id}?file=${chapter.id}`);
  await expect(page.getByTestId('editor-panel')).toBeVisible();
  await expect(page.locator('[data-editor-scroll-container] textarea').first()).toHaveValue(manuscript);
  await expect(page.getByTestId('chat-input')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

test.describe('Visual Regression', () => {
  test.skip(!ENABLED, 'Visual comparisons require the explicitly enabled regression lane.');
  test.use({ locale: 'zh-CN', timezoneId: 'Asia/Shanghai', colorScheme: 'light', reducedMotion: 'reduce' });

  test.beforeEach(async ({ page }) => {
    await prepareVisualData(page);
  });

  test('dashboard matches snapshot', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page.getByTestId('dashboard-composer')).toBeVisible();
    await expect(page.getByTestId('project-card')).toHaveCount(1);
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot('dashboard.png', { maxDiffPixels: 100 });
  });

  test('project page layout', async ({ page }) => {
    await openVisualProject(page);
    await expect(page).toHaveScreenshot('project-page.png', { fullPage: true, maxDiffPixels: 100 });
  });

  test('chat panel empty state', async ({ page }) => {
    await openVisualProject(page);
    await expect(page.getByTestId('chat-panel').getByTestId('chat-message')).toHaveCount(0);
    await expect(page.getByTestId('chat-panel')).toHaveScreenshot('chat-empty.png', { maxDiffPixels: 100 });
  });

  test('chat panel with messages', async ({ page }) => {
    await openVisualProject(page);
    await page.getByTestId('chat-input').fill('这封旧信可以怎样推动剧情？');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('chat-panel').getByText(answer, { exact: true })).toBeVisible();
    await expect(page.getByTestId('chat-panel').getByTestId('quota-badge-compact')).toHaveAttribute('aria-label', '今日 AI 消息 1/3 条');
    await page.getByTestId('chat-input').fill('让沈砚先查看信封上的字迹。');
    await expect(page.getByTestId('send-button')).toBeEnabled();
    await expect(page.getByTestId('chat-panel')).toHaveScreenshot('chat-with-messages.png', { maxDiffPixels: 100 });
  });

  test('mobile layout at 375px', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/dashboard');
    await expect(page.getByTestId('dashboard-composer')).toBeVisible();
    await expect(page.getByTestId('project-card')).toHaveCount(1);
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot('mobile-layout.png', { fullPage: true, maxDiffPixels: 100 });
  });
});
