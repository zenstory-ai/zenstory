import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

test.use({ hasTouch: true });

const projectId = responsiveProjects[0].id;
const token = '2026-10-13T10:00:00.000Z';
const longTitle = '长标题 Chapter with a very long file name 人物终于走进了那扇没有人敢打开的门 '.repeat(3).trim();
const body = (page: Page) => page.locator('[data-editor-scroll-container="true"] textarea');
const chat = (page: Page) => page.getByTestId('message-list');

async function fixture(page: Page, locale: 'zh' | 'en', theme: 'light' | 'dark') {
  await mockResponsiveApp(page);
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('zenstory-language', locale);
    localStorage.setItem('zenstory-theme', theme);
  }, { locale, theme });
  const makeFile = (id: string, title: string) => ({ id, title, project_id: projectId, file_type: 'draft', content: `${id} saved body`, parent_id: 'draft-folder', order: 0, metadata: null, created_at: token, updated_at: token });
  const files = { A: makeFile('A', 'Current draft'), B: makeFile('B', longTitle) };
  const calls = [
    { id: 'create', name: 'create_file', status: 'success', result: files.B },
    { id: 'update', name: 'update_file', status: 'success', result: files.A },
    { id: 'no-id', name: 'create_file', status: 'success', result: { title: 'Old record without ID' } },
    { id: 'pending', name: 'create_file', status: 'pending', arguments: { id: 'A', title: 'Unfinished file' } },
    { id: 'failed', name: 'create_file', status: 'error', result: files.B, error: 'failed' },
    { id: 'deleted', name: 'create_file', status: 'success', result: { id: 'deleted', title: 'Deleted file' } },
    { id: 'private', name: 'update_file', status: 'success', result: { id: 'private', title: 'Private file' } },
    { id: 'edit', name: 'edit_file', status: 'success', result: { id: 'A', title: 'Current draft', undo: { before_version_number: 1, expected_after_updated_at: token }, details: [{ op: 'replace', old_preview: 'before', new_preview: 'after' }] } },
  ];
  const state = { requests: [] as Array<{ method: string; path: string }>, conflict: false, errors: [] as string[] };
  page.on('pageerror', error => state.errors.push(error.message));
  const origin = new URL(test.info().project.use.baseURL as string).origin;
  await page.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (!url.pathname.startsWith('/api/')) return url.origin === origin ? route.continue() : route.abort('blockedbyclient');
    state.requests.push({ method: req.method(), path: url.pathname });
    const fulfill = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (url.pathname.endsWith('/recent')) return fulfill([{ id: 'assistant-files', session_id: 'local-session', role: 'assistant', content: '', created_at: token, metadata: null, tool_calls: JSON.stringify(calls) }]);
    const fileId = url.pathname.match(/^\/api\/v1\/files\/([^/]+)$/)?.[1];
    if (fileId === 'deleted' || fileId === 'private') return fulfill({ detail: 'File is unavailable' }, fileId === 'deleted' ? 404 : 403);
    if (fileId === 'A' || fileId === 'B') {
      if (req.method() === 'GET') return fulfill(files[fileId]);
      if (req.method() === 'PUT') {
        if (state.conflict) return fulfill({ error_code: 'ERR_CONFLICT', error_message: 'stale', error_detail: { reason: 'stale_write', current_content: 'New server body', current_updated_at: '2026-10-13T10:01:00.000Z' } }, 409);
        files[fileId] = { ...files[fileId], ...req.postDataJSON(), updated_at: '2026-10-13T10:02:00.000Z' };
        return fulfill(files[fileId]);
      }
    }
    if (url.pathname.endsWith('/file-tree')) return fulfill({ tree: [{ id: 'draft-folder', title: 'Drafts', file_type: 'folder', parent_id: null, order: 0, metadata: { folder_type: 'draft' }, children: Object.values(files).map(file => ({ ...file, children: [] })) }] });
    if (url.pathname.endsWith('/agent/suggest')) return fulfill({ suggestions: [] });
    if (url.pathname.endsWith('/versions')) return fulfill({ versions: [], total: 0 });
    if (url.pathname.endsWith('/stats/record')) return fulfill({ success: true });
    return route.fallback();
  });
  return { state, files };
}

async function showChat(page: Page, width: number) {
  if (width < 768) await page.locator('#chat-tab').click();
}

for (const width of [390, 768, 1440]) for (const locale of ['zh', 'en'] as const) for (const theme of ['light', 'dark'] as const) {
  test(`file titles open exact files and retain drafts ${width} ${locale} ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const { files, state } = await fixture(page, locale, theme);
    await page.goto(`/project/${projectId}?file=A`);
    await expect(body(page)).toHaveValue('A saved body');
    const editorTitle = page.getByPlaceholder(locale === 'zh' ? '请输入标题' : 'Enter title');
    await editorTitle.fill('Unsaved title only');
    await showChat(page, width);
    const createTitle = chat(page).getByRole('button', { name: longTitle, exact: true });
    const updateTitle = chat(page).getByRole('button', { name: /Current draft/ });
    await expect(createTitle).toHaveCount(1);
    await expect(chat(page).getByRole('button', { name: /Old record without ID|Unfinished file/ })).toHaveCount(0);
    for (const title of [createTitle, updateTitle]) {
      const card = title.locator('..').locator('..');
      const titleBox = await title.boundingBox();
      const countBox = await card.getByText(locale === 'zh' ? '3 字' : '3 chars', { exact: true }).boundingBox();
      const statusBox = await card.locator(':scope > span').first().boundingBox();
      expect(Math.abs(statusBox!.y + statusBox!.height / 2 - countBox!.y - countBox!.height / 2)).toBeLessThanOrEqual(2);
      expect(statusBox!.x + statusBox!.width).toBeLessThan(countBox!.x);
      expect(countBox!.y + countBox!.height).toBeLessThanOrEqual(titleBox!.y);
    }
    if (width < 768) expect((await updateTitle.locator('..').locator('..').locator('..').boundingBox())!.height).toBeLessThan(100);
    await updateTitle.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('short-file-card.png') });
    // Opening the same ID must not fetch over a title-only draft before autosave.
    const readsBefore = state.requests.filter(r => r.method === 'GET' && r.path.endsWith('/files/A')).length;
    if (width < 768) await updateTitle.tap();
    else await updateTitle.click();
    await expect(editorTitle).toHaveValue('Unsaved title only');
    expect(state.requests.some(r => r.method === 'PUT' && r.path.endsWith('/files/A'))).toBe(false);
    expect(state.requests.filter(r => r.method === 'GET' && r.path.endsWith('/files/A'))).toHaveLength(readsBefore);
    const draft = 'Newest unsaved author draft';
    await body(page).fill(draft);
    await showChat(page, width);
    await updateTitle.click();
    await expect(body(page)).toHaveValue(draft);
    await showChat(page, width);
    await createTitle.scrollIntoViewIfNeeded();
    await updateTitle.focus();
    await page.keyboard.press('Shift+Tab');
    await expect(createTitle).toBeFocused();
    expect(await createTitle.evaluate(el => getComputedStyle(el).boxShadow)).not.toBe('none');
    const bounds = await createTitle.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    if (width < 768) expect(bounds!.height).toBeGreaterThanOrEqual(44);
    await createTitle.locator('..').locator('..').locator('..').scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath('file-title-focus.png') });
    const chatBounds = await chat(page).boundingBox();
    const scrollTop = await chat(page).evaluate(el => el.scrollTop);
    await createTitle.press('Enter');
    await expect(body(page)).toHaveValue('B saved body');
    expect(files.A.content).toBe(draft);
    expect(files.A.title).toBe('Unsaved title only');
    if (width < 768) await expect(page.locator('#editor-tab')).toHaveAttribute('aria-selected', 'true');
    else {
      expect(await chat(page).boundingBox()).toEqual(chatBounds);
      expect(await chat(page).evaluate(el => el.scrollTop)).toBe(scrollTop);
    }
    await showChat(page, width);
    // Existing undo and diff controls remain independent of file-opening buttons.
    const undo = chat(page).getByRole('button', { name: locale === 'zh' ? '撤销' : 'Undo', exact: true });
    await expect(undo).toBeVisible();
    expect(await undo.locator('button').count()).toBe(0);
    await chat(page).locator('summary').click();
    await expect(chat(page).getByText('before', { exact: true })).toBeVisible();
    expect(state.requests.some(r => /\/agent\/(stream|stop)$/.test(r.path))).toBe(false);
    expect(state.errors).toEqual([]);
  });
}

for (const width of [390, 1440]) {
  test(`failed draft save prevents file switch ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const { state } = await fixture(page, 'en', 'light');
    await page.goto(`/project/${projectId}?file=A`);
    await expect(body(page)).toHaveValue('A saved body');
    state.conflict = true;
    await body(page).fill('Unsaved conflict draft');
    await showChat(page, width);
    await chat(page).getByRole('button', { name: longTitle, exact: true }).click();
    await expect(body(page)).toHaveValue('Unsaved conflict draft');
    expect(state.requests.some(r => r.path.endsWith('/files/B'))).toBe(false);
  });

  test(`deleted and inaccessible files use existing error handling ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const { state } = await fixture(page, 'en', 'light');
    const unavailable = "This file couldn't be opened. Refresh the page and try again.";
    const deletedNotice = 'This file has been deleted';
    for (const { name, fileId, status } of [
      { name: 'Deleted file', fileId: 'deleted', status: 404 },
      { name: 'Private file', fileId: 'private', status: 403 },
    ]) {
      // A fresh document prevents the previous file's error or toast from satisfying this assertion.
      await page.goto(`/project/${projectId}?file=A`);
      await expect(body(page)).toHaveValue('A saved body');
      await expect(page.getByText(unavailable, { exact: true })).toHaveCount(0);
      await expect(page.getByText(deletedNotice, { exact: true })).toHaveCount(0);
      await showChat(page, width);
      const response = page.waitForResponse(res => res.request().method() === 'GET'
        && new URL(res.url()).pathname === `/api/v1/files/${fileId}`);
      await chat(page).getByRole('button', { name: new RegExp(name) }).click();
      expect((await response).status()).toBe(status);
      const message = fileId === 'deleted'
        ? page.getByText(unavailable, { exact: true }).or(page.getByText(deletedNotice, { exact: true }))
        : page.getByText(unavailable, { exact: true });
      await expect(message).toBeVisible();
      expect(state.requests.filter(r => r.method === 'POST' && !/suggest|stats/.test(r.path))).toEqual([]);
    }
    expect(state.errors).toEqual([]);
  });
}
