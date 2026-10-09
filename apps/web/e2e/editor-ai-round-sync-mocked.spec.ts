/**
 * One AI round writes the same chapter twice: create_file streams v1, then a
 * later edit_file in the same round lands v2. The open editor must follow v2,
 * and an author typing afterwards must neither freeze the page nor overwrite
 * v2 with the stale v1 text. All API calls are mocked; the agent stream is a
 * page-side ReadableStream that the test feeds event by event.
 */
import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

type FakeFile = {
  id: string; project_id: string; title: string; content: string;
  file_type: string; parent_id: string; order: number; metadata: null;
  created_at: string; updated_at: string;
};
type RequestRecord = { method: string; path: string; body: Record<string, unknown> | null };

const projectId = responsiveProjects[0].id;
const token0 = '2026-10-09T12:00:00.000Z';
const tokenV1 = '2026-10-09T12:38:46.000Z';
const tokenV2 = '2026-10-09T12:43:06.000Z';
const V1 = Array.from({ length: 40 }, (_, i) => `中午十二点，老周还在摊上，第${i + 1}段。`).join('\n\n');
const V2 = `第二天，${V1}`;

const textarea = (page: Page) => page.locator('[data-editor-scroll-container="true"] textarea');
const titleInput = (page: Page) => page.getByPlaceholder('请输入标题...');

async function setup(page: Page) {
  await mockResponsiveApp(page);
  const files: Record<string, FakeFile> = {
    ch1: { id: 'ch1', project_id: projectId, title: '第1章 头顶的钟', content: '第一章正文。', file_type: 'draft', parent_id: `folder-${projectId}`, order: 0, metadata: null, created_at: token0, updated_at: token0 },
  };
  const requests: RequestRecord[] = [];
  const errors: string[] = [];
  page.on('pageerror', err => errors.push(err.message));
  // Page-side agent stream the test can feed one SSE event at a time.
  await page.addInitScript(() => {
    const encoder = new TextEncoder();
    let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
    const w = window as unknown as Record<string, unknown>;
    // Main-thread tasks over 50 ms; the audited freeze blocked the page for minutes.
    const longTasks: number[] = [];
    w.__longTasks = longTasks;
    try {
      new PerformanceObserver(list => { for (const entry of list.getEntries()) longTasks.push(entry.duration); })
        .observe({ type: 'longtask', buffered: true });
    } catch { /* longtask timing unavailable: the responsiveness probe still runs */ }
    w.__sse = {
      push: (type: string, data: unknown) => controller?.enqueue(encoder.encode(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`)),
      close: () => controller?.close(),
      opened: () => controller !== null,
    };
    const realFetch = window.fetch.bind(window);
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.includes('/api/v1/agent/stream')) {
        const body = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
        return Promise.resolve(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      }
      return realFetch(input, init);
    };
  });
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const body = request.postData() ? JSON.parse(request.postData()!) as Record<string, unknown> : null;
    requests.push({ method, path: url.pathname, body });
    const fulfill = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const fileId = url.pathname.match(/^\/api\/v1\/files\/([^/]+)$/)?.[1];
    if (fileId && files[fileId]) {
      if (method === 'GET') return fulfill(files[fileId]);
      if (method === 'PUT') {
        if (body?.base_updated_at && body.base_updated_at !== files[fileId].updated_at) {
          return fulfill({ error_code: 'ERR_CONFLICT', error_message: 'stale', error_detail: { reason: 'stale_write', current_content: files[fileId].content, current_updated_at: files[fileId].updated_at } }, 409);
        }
        files[fileId] = { ...files[fileId], ...body, updated_at: '2026-10-09T12:50:00.000Z' } as FakeFile;
        return fulfill(files[fileId]);
      }
    }
    if (url.pathname === `/api/v1/projects/${projectId}/file-tree`) {
      return fulfill({ tree: [{ id: `folder-${projectId}`, title: '正文', file_type: 'folder', metadata: { folder_type: 'draft' }, parent_id: null, order: 0, children: Object.values(files).map(file => ({ ...file, children: [] })) }] });
    }
    if (method === 'POST' && url.pathname.endsWith('/stats/record')) return fulfill({ success: true });
    if (/\/versions$|\/snapshots$/.test(url.pathname)) return fulfill(method === 'GET' ? { versions: [], total: 0 } : { id: 'snap-1' });
    return route.fallback();
  });
  return { files, requests, errors };
}

async function push(page: Page, type: string, data: unknown) {
  await page.evaluate(([t, d]) => (window as unknown as { __sse: { push: (t: string, d: unknown) => void } }).__sse.push(t as string, d), [type, data] as const);
}

type Fixture = Awaited<ReturnType<typeof setup>>;
const putsToCh2 = (f: Fixture) => f.requests.filter(r => r.method === 'PUT' && r.path.endsWith('/files/ch2'));

/** Sends "write chapter 2"; the AI creates and streams v1, then (optionally after `beforeSecondWrite`) writes v2. */
async function runRound(page: Page, f: Fixture, secondWrite: 'edit_file' | 'parallel_execute', beforeSecondWrite?: () => Promise<void>) {
  await page.goto(`/project/${projectId}?file=ch1`);
  await expect(titleInput(page)).toHaveValue('第1章 头顶的钟');

  await page.getByTestId('chat-input').fill('接着写第二章');
  await page.getByTestId('send-button').click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __sse: { opened: () => boolean } }).__sse.opened())).toBe(true);

  // create_file (empty) → <file> stream → saved as v1.
  f.files.ch2 = { ...f.files.ch1, id: 'ch2', title: '第2章 倒着拨的表', content: '', order: 1, updated_at: token0 };
  await push(page, 'tool_call', { tool_use_id: 't1', tool_name: 'create_file', arguments: { title: '第2章 倒着拨的表' } });
  await push(page, 'tool_result', { tool_use_id: 't1', tool_name: 'create_file', status: 'success', data: { id: 'ch2', title: '第2章 倒着拨的表', file_type: 'draft', content: '' } });
  await push(page, 'file_created', { file_id: 'ch2', file_type: 'draft', title: '第2章 倒着拨的表' });
  for (let i = 0; i < V1.length; i += 120) {
    await push(page, 'file_content', { file_id: 'ch2', chunk: V1.slice(i, i + 120) });
    await page.waitForTimeout(30);
  }
  f.files.ch2 = { ...f.files.ch2, content: V1, updated_at: tokenV1 };
  await push(page, 'file_content_end', { file_id: 'ch2' });
  await expect(textarea(page)).toHaveValue(V1);
  await beforeSecondWrite?.();

  // Later in the same round the agent patches the chapter again (v2).
  f.files.ch2 = { ...f.files.ch2, content: V2, updated_at: tokenV2 };
  if (secondWrite === 'edit_file') {
    await push(page, 'tool_call', { tool_use_id: 't2', tool_name: 'edit_file', arguments: { id: 'ch2' } });
    await push(page, 'tool_result', { tool_use_id: 't2', tool_name: 'edit_file', status: 'success', data: { id: 'ch2', title: '第2章 倒着拨的表', file_type: 'draft', new_length: V2.length } });
    await push(page, 'file_edit_start', { file_id: 'ch2', title: '第2章 倒着拨的表', total_edits: 1, file_type: 'draft' });
    await push(page, 'file_edit_applied', { file_id: 'ch2', edit_index: 0, op: 'prepend', success: true });
    await push(page, 'file_edit_end', { file_id: 'ch2', edits_applied: 1, new_length: V2.length, file_type: 'draft', title: '第2章 倒着拨的表' });
  } else {
    // Edits made inside parallel_execute sub-tasks emit no file_edit_* events
    // (the audited round wrote v2 this way).
    await push(page, 'tool_call', { tool_use_id: 't2', tool_name: 'parallel_execute', arguments: { tasks: [] } });
    await push(page, 'tool_result', { tool_use_id: 't2', tool_name: 'parallel_execute', status: 'success', data: { total_tasks: 1, completed: 1, failed: 0, all_completed: true, tasks: [{ id: 'task-1', type: 'edit_file', status: 'completed' }] } });
  }
  await push(page, 'content_start', {});
  await push(page, 'content', { text: '第2章写好了，开头补了「第二天」。' });
  await push(page, 'content_end', {});
  await push(page, 'done', { assistant_message_id: 'a1', session_id: 's1', file_mutated: true });
  await page.evaluate(() => (window as unknown as { __sse: { close: () => void } }).__sse.close());
}

for (const secondWrite of ['edit_file', 'parallel_execute'] as const) {
  test(`editor follows a second ${secondWrite} write of the same round and typing stays safe`, async ({ page }) => {
    const f = await setup(page);
    await runRound(page, f, secondWrite);
    await expect(textarea(page)).toHaveValue(V2);

    // The author types one character at the end.
    await textarea(page).click();
    await textarea(page).evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(el.value.length, el.value.length));
    await page.evaluate(() => { (window as unknown as { __longTasks: number[] }).__longTasks.length = 0; });
    await page.keyboard.type('好');
    await expect(textarea(page)).toHaveValue(`${V2}好`);
    // The page stays responsive through the autosave window and the autosave
    // lands on top of v2.
    for (let probe = 0; probe < 5; probe += 1) {
      const started = Date.now();
      await page.evaluate(() => 1);
      expect(Date.now() - started).toBeLessThan(2000);
      await page.waitForTimeout(700);
    }
    await expect.poll(() => putsToCh2(f).length, { timeout: 8000 }).toBeGreaterThan(0);
    const longTasks = await page.evaluate(() => (window as unknown as { __longTasks: number[] }).__longTasks.slice());
    expect(Math.max(0, ...longTasks)).toBeLessThan(1000);
    expect(putsToCh2(f)[0].body).toMatchObject({ content: `${V2}好`, base_updated_at: tokenV2 });
    expect(f.files.ch2.content).toBe(`${V2}好`);
    expect(f.errors).toEqual([]);
  });
}

test('unsaved author text is kept and compared when the AI writes the same chapter again', async ({ page }) => {
  const f = await setup(page);
  await runRound(page, f, 'parallel_execute', async () => {
    await textarea(page).click();
    await textarea(page).evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(el.value.length, el.value.length));
    await page.keyboard.type('作者补一句');
    await expect(page.getByText('未保存', { exact: true })).toBeVisible();
  });

  // The comparison replaces the plain textarea; v2 stays on the server untouched.
  await expect(page.getByText('AI 刚改过这个文件，你还有没保存的修改。', { exact: false })).toBeVisible();
  await expect(textarea(page)).toHaveCount(0);
  await page.waitForTimeout(3500);
  expect(putsToCh2(f)).toHaveLength(0);
  expect(f.files.ch2.content).toBe(V2);
  expect(f.errors).toEqual([]);
});

test('finishing the comparison keeps the AI second write and adds only the author text', async ({ page }) => {
  const f = await setup(page);
  await runRound(page, f, 'parallel_execute', async () => {
    await textarea(page).click();
    await textarea(page).evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(el.value.length, el.value.length));
    await page.keyboard.type('作者补一句');
    await expect(page.getByText('未保存', { exact: true })).toBeVisible();
  });
  await expect(page.getByText('AI 刚改过这个文件，你还有没保存的修改。', { exact: false })).toBeVisible();

  // The author just presses finish without choosing anything.
  await page.getByTitle(/完成审阅|应用更改/).click();
  await expect.poll(() => putsToCh2(f).length).toBe(1);
  expect(putsToCh2(f)[0].body).toMatchObject({ content: `${V2}作者补一句`, base_updated_at: tokenV2 });
  expect(f.files.ch2.content.startsWith('第二天，')).toBe(true);
  await expect(textarea(page)).toHaveValue(`${V2}作者补一句`);
  expect(f.errors).toEqual([]);
});

test('leaving the page during the comparison keeps the author text on top of the AI second write', async ({ page }) => {
  const f = await setup(page);
  await runRound(page, f, 'parallel_execute', async () => {
    await textarea(page).click();
    await textarea(page).evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(el.value.length, el.value.length));
    await page.keyboard.type('作者补一句');
    await expect(page.getByText('未保存', { exact: true })).toBeVisible();
  });
  await expect(page.getByText('AI 刚改过这个文件，你还有没保存的修改。', { exact: false })).toBeVisible();

  // The author walks away mid-comparison: nothing stale is sent.
  await page.goto('/dashboard');
  expect(putsToCh2(f)).toHaveLength(0);
  expect(f.files.ch2.content).toBe(V2);

  // Back on the chapter, the kept text is v2 plus only the author's sentence.
  await page.goto(`/project/${projectId}?file=ch2`);
  await expect(textarea(page)).toHaveValue(`${V2}作者补一句`);

  // Carrying on writing saves on top of v2 (dev StrictMode may already have
  // flushed the restored text once): the AI's 第二天 survives, no 409.
  await textarea(page).click();
  await textarea(page).evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(el.value.length, el.value.length));
  await page.keyboard.type('好');
  await expect.poll(() => f.files.ch2.content, { timeout: 8000 }).toBe(`${V2}作者补一句好`);
  expect(putsToCh2(f)[0].body).toMatchObject({ base_updated_at: tokenV2 });
  await expect(page.getByText('文件在别处有了新改动', { exact: false })).toHaveCount(0);
  expect(f.errors).toEqual([]);
});

/** The author rewrites the first paragraph, which the AI's v2 also changes. */
const firstParagraph = V1.split('\n\n')[0]!;
const authorRewrite = V1.replace(firstParagraph, '傍晚，老周收摊了。');
async function rewriteFirstParagraph(page: Page) {
  await textarea(page).click();
  await textarea(page).evaluate((el: HTMLTextAreaElement, length: number) => el.setSelectionRange(0, length), firstParagraph.length);
  await page.keyboard.type('傍晚，老周收摊了。');
  await expect(page.getByText('未保存', { exact: true })).toBeVisible();
}

test("pressing finish where both rewrote the same words keeps the author's side there", async ({ page }) => {
  const f = await setup(page);
  await runRound(page, f, 'parallel_execute', () => rewriteFirstParagraph(page));
  await expect(page.getByText('AI 刚改过这个文件，你还有没保存的修改。', { exact: false })).toBeVisible();
  // The AI's rewrite is listed already rejected (the author's words kept), not hidden
  // behind the "pending" filter.
  await expect(page.getByTitle(/^拒绝 \(N\)$/).first()).toBeDisabled();

  await page.getByTitle(/完成审阅|应用更改/).click();
  await expect(textarea(page)).toHaveValue(authorRewrite);
  await expect.poll(() => f.files.ch2.content).toBe(authorRewrite);
  // Saved as the author's edit, on v2's token.
  expect(putsToCh2(f)[0].body).toMatchObject({ change_type: 'edit', change_source: 'user', base_updated_at: tokenV2 });
  expect(f.errors).toEqual([]);
});

test("accepting the AI's rewrite in the comparison takes the AI's words there", async ({ page }) => {
  const f = await setup(page);
  await runRound(page, f, 'parallel_execute', () => rewriteFirstParagraph(page));
  await expect(page.getByText('AI 刚改过这个文件，你还有没保存的修改。', { exact: false })).toBeVisible();

  await page.getByTitle('全部接受').click();
  await page.getByTitle(/完成审阅|应用更改/).click();
  await expect(textarea(page)).toHaveValue(V2);
  await expect.poll(() => f.files.ch2.content).toBe(V2);
  expect(f.errors).toEqual([]);
});

test('leaving before deciding where both rewrote the same words keeps the author rewrite to restore', async ({ page }) => {
  const f = await setup(page);
  await runRound(page, f, 'parallel_execute', () => rewriteFirstParagraph(page));
  await expect(page.getByText('AI 刚改过这个文件，你还有没保存的修改。', { exact: false })).toBeVisible();

  // The author walks away mid-comparison: nothing is sent, and v2 is untouched.
  await page.goto('/dashboard');
  expect(putsToCh2(f)).toHaveLength(0);
  expect(f.files.ch2.content).toBe(V2);

  // Back on the chapter: v2 is shown and the author is asked about the draft.
  await page.goto(`/project/${projectId}?file=ch2`);
  await expect(textarea(page)).toHaveValue(V2);
  await expect(page.getByText('你上次没保存的草稿和服务器最新版本不同', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '恢复本地草稿' }).click();
  await expect(textarea(page)).toHaveValue(authorRewrite);

  await textarea(page).click();
  await textarea(page).evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(el.value.length, el.value.length));
  await page.keyboard.type('好');
  await expect.poll(() => f.files.ch2.content, { timeout: 8000 }).toBe(`${authorRewrite}好`);
  expect(putsToCh2(f).at(-1)?.body).toMatchObject({ base_updated_at: tokenV2 });
  expect(f.errors).toEqual([]);
});
