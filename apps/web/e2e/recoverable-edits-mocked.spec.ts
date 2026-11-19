import { expect, test, type Page } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

const file = {
  id: 'draft-0', project_id: 'responsive-project-0', title: '新标题',
  content: '她向西走。', file_type: 'draft', parent_id: 'folder-drafts', order: 0,
  metadata: null, created_at: '2026-11-19T04:00:00Z', updated_at: '2026-11-19T04:05:00Z',
};

const operation = {
  id: 'operation-1', file_id: file.id, kind: 'edit_file', before_title: '旧标题',
  after_title: '新标题', before_version_number: 2, created_at: '2026-11-19T04:00:00Z', undone_at: null,
};

async function openOperationHistory(page: Page, conflict: boolean) {
  await mockResponsiveApp(page, null, 'free', 'zh');
  let undone = false;
  await page.route('**/api/v1/files/draft-0', route => route.fulfill({ json: file }));
  await page.route('**/api/v1/files/draft-0/versions*', route => route.fulfill({ json: { total: 0, current_version_number: null, versions: [] } }));
  await page.route('**/api/v1/files/draft-0/edit-operations**', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: { operations: [{ ...operation, undone_at: undone ? '2026-11-19T04:10:00Z' : null }], total: 1 } });
      return;
    }
    const rawBody = route.request().postData();
    const body = rawBody ? JSON.parse(rawBody) as Record<string, unknown> : null;
    if (!conflict || body?.expected_updated_at) {
      undone = true;
      await route.fulfill({ json: { status: 'undone', operation_id: operation.id, file_id: file.id, updated_at: '2026-11-19T04:10:00Z' } });
      return;
    }
    await route.fulfill({ json: {
      status: 'needs_merge', operation_id: operation.id, file_id: file.id,
      before: { content: '她留在原地。', title: '旧标题', order: 3 },
      after: { content: '她向东走。', title: '新标题', order: 4 },
      current: { content: '她向西走。', title: '作者标题', order: 7, updated_at: file.updated_at, content_sha256: 'current-sha' },
      content_conflict: true, metadata_conflict: true,
    } });
  });
  await page.goto('/project/responsive-project-0?file=draft-0');
  await page.getByRole('button', { name: /^(历史版本|历史)$/ }).first().click();
  return page.getByRole('dialog', { name: /历史版本/ });
}

test('AI operation undo is one click when there is no overlap', async ({ page }) => {
  const history = await openOperationHistory(page, false);
  await history.getByRole('button', { name: '撤销' }).click();
  await expect(history.getByText('已撤销')).toBeVisible();
  await expect(page.getByText(/恢复到版本/)).toHaveCount(0);
});

test('saved chapter replacement stays a compact undoable card without a review diff', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockResponsiveApp(page, null, 'free', 'zh');
  const replacement = {
    id: file.id, title: '第十二章 潮汐尽头', file_type: 'draft', chapter: 12,
    action: 'replaced', mutation_applied: true, body_saved: true,
    previous_word_count: 2380, new_word_count: 2614,
    updated_at: file.updated_at,
    undo: {
      before_version_number: 2, expected_after_updated_at: file.updated_at,
      expected_after_content_sha256: 'a'.repeat(64), operation_id: operation.id,
    },
  };
  await page.route('**/api/v1/chat/session/*/recent?*', route => route.fulfill({ json: [{
    id: 'assistant-replacement', session_id: 'local-session', role: 'assistant', content: '',
    created_at: '2026-11-19T04:06:00Z', metadata: null,
    tool_calls: JSON.stringify([{ id: 'replace-chapter-12', name: 'write_chapter', status: 'success', result: replacement }]),
  }] }));
  await page.route('**/api/v1/files/draft-0/edit-operations**', route => route.fulfill({ json: {
    operations: [{ ...operation, kind: 'write_chapter', before_title: replacement.title, after_title: replacement.title }], total: 1,
  } }));
  await page.goto('/project/responsive-project-0?file=draft-0');
  const messages = page.getByTestId('message-list');
  const undo = messages.getByRole('button', { name: '撤销', exact: true });
  await expect(messages.getByText('已整章替换第12章')).toBeVisible();
  await expect(messages.getByText('替换了原来的 2.4 千字')).toBeVisible();
  await expect(messages.getByText('2.6 千字')).toBeVisible();
  await expect(undo).toBeVisible();
  await expect(messages.locator('[data-testid="diff-review-split-view"]')).toHaveCount(0);
  await undo.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('chapter-replaced-compact-card-1440.png'), animations: 'disabled' });
});

for (const width of [1440, 390] as const) {
  test(`${width}px: overlap undo shows only the compact conflict choices`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const history = await openOperationHistory(page, true);
    await history.getByRole('button', { name: '撤销' }).click();
    const conflict = page.getByRole('dialog', { name: '这几处和你后写的内容重叠' });
    await expect(conflict).toBeVisible();
    await expect(conflict.getByRole('checkbox', { name: /恢复这处 AI 修改前的文字/ })).not.toBeChecked();
    await expect(conflict.getByRole('checkbox', { name: /同时恢复这次修改前的标题/ })).not.toBeChecked();
    await expect(conflict.getByText('她向西走。')).toBeVisible();
    await expect(conflict.getByText('她留在原地。')).toBeVisible();
    await expect(conflict.getByText('当前文字')).toBeVisible();
    await expect(conflict.getByText('恢复为').first()).toBeVisible();
    await expect(conflict.getByText('当前标题')).toBeVisible();
    await expect(conflict.getByText('作者标题')).toBeVisible();
    await expect(conflict.getByText('旧标题')).toBeVisible();
    await expect(conflict.getByText('当前排序位置')).toBeVisible();
    await expect(conflict.getByText('恢复排序位置')).toBeVisible();
    await expect(conflict.getByText('7')).toBeVisible();
    await expect(conflict.getByText('3')).toBeVisible();
    await expect(conflict.locator('[data-testid="diff-review-split-view"]')).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`undo-conflict-${width}.png`), animations: 'disabled' });
  });
}

test('file tree previews the exact delete set once and restores the resulting trash batch', async ({ page }, testInfo) => {
  await mockResponsiveApp(page, null, 'free', 'zh');
  let deleted = false;
  let restored = false;
  const folderTarget = { id: 'arc-1', title: '第一卷 风暴将至', file_type: 'folder', parent_id: 'folder-drafts', order: 0 };
  const target = { id: 'draft-0', title: '第一章 很长的标题和人物的选择', file_type: 'draft', parent_id: 'arc-1', order: 0 };
  await page.route('**/api/v1/projects/responsive-project-0/file-tree', route => route.fulfill({ json: { tree: [{
    id: 'folder-drafts', title: '正文', file_type: 'folder', parent_id: null, order: 0,
    children: deleted && !restored ? [] : [{ ...folderTarget, children: [{ ...target, children: [] }] }],
  }] } }));
  await page.route('**/api/v1/projects/responsive-project-0/files/deletion-preview', route => route.fulfill({ json: {
    root_ids: ['arc-1'], recursive: true, targets: [folderTarget, target], count: 2, target_hash: 'target-hash-1',
  } }));
  await page.route('**/api/v1/projects/responsive-project-0/files/delete-batch', async route => {
    expect(route.request().postDataJSON()).toMatchObject({ ids: ['arc-1'], recursive: true, target_hash: 'target-hash-1' });
    deleted = true;
    await route.fulfill({ json: { batch_id: 'batch-1', root_ids: ['arc-1'], targets: [folderTarget, target], count: 2, restored: false, mutation_applied: true } });
  });
  await page.route('**/api/v1/projects/responsive-project-0/files/trash**', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: { batches: restored ? [] : [{ batch_id: 'batch-1', root_ids: ['arc-1'], titles: [folderTarget.title, target.title], count: 2, source: 'agent_confirmed', created_at: '2026-11-19T04:00:00Z', restored: false }], total: restored ? 0 : 1, limit: 50, offset: 0, next_offset: null } });
      return;
    }
    restored = true;
    await route.fulfill({ json: { batch_id: 'batch-1', file_ids: ['draft-0'], restored: true, already_restored: false } });
  });
  await page.goto('/project/responsive-project-0?file=draft-0');
  const row = page.locator('[data-file-id="arc-1"]');
  await row.getByRole('button', { name: '删除' }).click();
  const confirm = page.getByRole('dialog', { name: '确定要删除吗？' });
  await expect(confirm.getByText(/第一卷.*2 项目文件/)).toBeVisible();
  const scope = confirm.getByRole('list', { name: '将删除的内容' });
  await expect(scope.getByText('第一卷 风暴将至 / 第一章 很长的标题和人物的选择')).toBeVisible();
  await expect(scope.getByText('正文')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('delete-exact-preview.png'), animations: 'disabled' });
  await confirm.getByRole('button', { name: '删除' }).click();
  await page.getByRole('button', { name: '回收站' }).first().click();
  const trash = page.getByRole('dialog', { name: '回收站' });
  await trash.getByRole('button', { name: '恢复这批文件' }).click();
  await expect(trash.getByText(target.title)).toHaveCount(0);
  expect(restored).toBe(true);
});
