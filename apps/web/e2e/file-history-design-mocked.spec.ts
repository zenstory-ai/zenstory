import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mockResponsiveApp } from './fixtures-responsive';

async function openHistory(page: Page, language: 'zh' | 'en', theme: 'light' | 'dark', count = 1) {
  await mockResponsiveApp(page, null, 'free', language);
  await page.addInitScript(theme => localStorage.setItem('zenstory-theme', theme), theme);
  const file = { id: 'draft-0', project_id: 'responsive-project-0', title: '第二章 雨夜接印：一个很长的章节名称与人物的选择', content: '雨水沿着屋檐落下来。', file_type: 'draft', parent_id: 'folder-drafts', order: 0, metadata: null, created_at: '2026-10-23T02:53:00Z', updated_at: '2026-10-23T02:53:00Z' };
  await page.route('**/api/v1/files/draft-0', route => route.fulfill({ json: file }));
  await page.route('**/api/v1/files/draft-0/versions*', route => route.fulfill({ json: {
    total: count,
    current_version_number: count,
    versions: Array.from({ length: count }, (_, i) => ({ id: `version-${count - i}`, version_number: count - i, change_type: 'ai_edit', change_source: 'ai', created_at: file.created_at, word_count: 2638, lines_added: 189, lines_removed: 0 })),
  } }));
  await page.route('**/api/v1/files/draft-0/versions/*/content', route => route.fulfill({ json: { content: '雨水沿着屋檐落下来。'.repeat(60) } }));
  await page.goto('/project/responsive-project-0?file=draft-0');
  await page.getByRole('button', { name: /^(历史版本|历史|Versions|History)$/ }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText(`v${count}`, { exact: true })).toBeVisible();
  return dialog;
}

for (const { width, language, theme } of [
  { width: 390, language: 'zh', theme: 'light' },
  { width: 390, language: 'en', theme: 'dark' },
  { width: 768, language: 'en', theme: 'light' },
  { width: 1440, language: 'zh', theme: 'dark' },
] as const) {
  test(`${width}px ${language} ${theme}: history labels and actions fit the dialog`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const dialog = await openHistory(page, language, theme);
    await expect(dialog.getByText(/共 \d+ 个版本|\d+ versions? total/)).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath('history.png') });
    const current = dialog.getByText(language === 'zh' ? '当前内容' : 'Current text', { exact: true });
    await expect(current).toHaveCSS('font-size', '12px');
    const currentBox = await current.evaluate(el => {
      const badge = el.parentElement!;
      const style = getComputedStyle(badge);
      return { padding: parseFloat(style.paddingLeft), radius: parseFloat(style.borderRadius) };
    });
    expect(currentBox.padding).toBeGreaterThanOrEqual(6);
    expect(currentBox.radius).toBeGreaterThanOrEqual(4);
    const bounds = await dialog.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.height).toBeLessThan(450);
    const overflows = await dialog.evaluate(el => Array.from(el.querySelectorAll('button, h2, [data-testid="version-list"]')).filter(child => {
      const box = child.getBoundingClientRect();
      const parent = el.getBoundingClientRect();
      return box.left < parent.left || box.right > parent.right || child.scrollWidth > child.clientWidth + 1;
    }).map(child => child.textContent));
    expect(overflows).toEqual([]);
    const accessibility = await new AxeBuilder({ page }).include('[role="dialog"]').withRules(['color-contrast']).analyze();
    expect(accessibility.violations).toEqual([]);
    if (width < 768) {
      for (const button of await dialog.locator('button').all()) {
        const box = await button.boundingBox();
        expect(box!.width).toBeGreaterThanOrEqual(44);
        expect(box!.height).toBeGreaterThanOrEqual(44);
      }
    }
  });
}

test('phone history scrolls and returns to the same row after preview', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await openHistory(page, 'zh', 'light', 30);
  const list = page.getByTestId('version-list');
  await list.evaluate(el => { el.scrollTop = 300; });
  let releasePreview!: () => void;
  const previewResponse = new Promise<void>(resolve => { releasePreview = resolve; });
  await page.route('**/api/v1/files/draft-0/versions/*/content', async route => {
    await previewResponse;
    await route.fulfill({ json: { content: '历史正文。'.repeat(100) } });
  });
  const row = dialog.locator('div[tabindex="0"]').filter({ has: page.getByText('v26', { exact: true }) });
  await row.getByRole('button', { name: '查看内容' }).click();
  // Clicking can scroll the target into view. Preserve where the author leaves
  // the list, measured before the response hides it, rather than the setup offset.
  const previousScroll = await list.evaluate(el => new Promise<number>(resolve => requestAnimationFrame(() => resolve(el.scrollTop))));
  expect(previousScroll).toBeGreaterThan(0);
  releasePreview();
  await expect(page.getByTestId('version-preview')).toBeVisible();
  await expect(list).toBeHidden();
  await page.getByRole('button', { name: '关闭预览' }).click();
  await expect(list).toBeVisible();
  expect(await list.evaluate(el => el.scrollTop)).toBe(previousScroll);
  await row.click();
  await expect(dialog.getByText('已选择 1 个版本', { exact: true })).toBeVisible();
  const secondRow = dialog.locator('div[tabindex="0"]').filter({ has: page.getByText('v25', { exact: true }) });
  await secondRow.click();
  await expect(dialog.getByText('已选择 2 个版本', { exact: true })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '对比版本', exact: true })).toBeEnabled();
  await secondRow.click();
  await row.click();
  await expect(dialog.getByText(/已选择 \d+ 个版本/)).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: '对比版本', exact: true })).toHaveCount(0);
});

for (const theme of ['light', 'dark'] as const) {
  test(`${theme}: chat reference priorities use readable quiet labels`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockResponsiveApp(page);
    await page.addInitScript(theme => localStorage.setItem('zenstory-theme', theme), theme);
    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ json: [] }));
    await page.route('**/api/v1/agent/stream', route => route.fulfill({
      contentType: 'text/event-stream',
      body: [
        ['context', { items: ['critical', 'constraint', 'relevant', 'inspiration', 'default'].map(priority => ({ id: priority, type: 'draft', title: '雨夜接印的剧情与人物关系', content: '作者提供的参考正文。', priority })) }],
        ['content_start', {}], ['content', { text: '已阅读参考正文。' }], ['content_end', {}],
        ['done', { assistant_message_id: 'reference-message', session_id: 'reference-session' }],
      ].map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
    }));
    await page.goto('/project/responsive-project-0');
    await page.locator('#chat-tab').click();
    await page.getByTestId('chat-input').fill('查看参考正文');
    await page.getByTestId('send-button').click();
    await page.getByRole('button', { name: /^来源/ }).click();
    const rows = page.locator('#chat-panel details');
    await expect(rows).toHaveCount(5);
    for (const [index, row] of (await rows.all()).entries()) {
      const badge = row.locator('summary > span').last();
      const style = await badge.evaluate(el => {
        const computed = getComputedStyle(el);
        return { fontSize: computed.fontSize, background: computed.backgroundColor, color: computed.color };
      });
      expect(style.fontSize).toBe('12px');
      if (index < 4) {
        // Semantic metadata should be a light tint, never a solid action button.
        expect(style.background).toMatch(/^rgba\(/);
        expect(parseFloat(style.background.split(',').at(-1)!)).toBeLessThanOrEqual(0.2);
        expect(style.color).not.toBe('rgb(255, 255, 255)');
      }
      await row.locator('summary').click();
      await expect(row.getByText('作者提供的参考正文。', { exact: true })).toBeVisible();
      await row.locator('summary').click();
    }
    await page.screenshot({ path: testInfo.outputPath('context-labels.png') });
    const accessibility = await new AxeBuilder({ page }).include('#chat-panel details').withRules(['color-contrast']).analyze();
    expect(accessibility.violations).toEqual([]);
  });
}
