import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

type FakeFile = {
  id: string; project_id: string; title: string; content: string;
  file_type: string; parent_id: string; order: number; metadata: null;
  created_at: string; updated_at: string;
};
type RequestRecord = { method: string; path: string; body: Record<string, unknown> | null };
type Fixture = {
  requests: RequestRecord[]; blocked: string[]; errors: string[];
  files: Record<string, FakeFile>; conflict: boolean;
  defer: boolean; release: () => void; observations: Record<string, unknown>; locale: 'en' | 'zh';
};
const fixtures = new WeakMap<Page, Fixture>();
const projectA = responsiveProjects[0].id;
const projectB = responsiveProjects[1].id;
const token0 = '2026-10-06T10:00:00.000Z';
const textarea = (page: Page) => page.locator('[data-editor-scroll-container="true"] textarea');
const titleInput = (page: Page) => page.getByPlaceholder('Enter title...');
const puts = (f: Fixture) => f.requests.filter(r => r.method === 'PUT');
const wordCount = (text: string) => (text.match(/[\u4e00-\u9fa5]/g) || []).length + (text.match(/[a-zA-Z]+/g) || []).length;

async function layoutStyles(page: Page, saveLabel = 'Save') {
  return page.evaluate((saveLabel) => {
    const snapshot = (el: Element | null) => {
      if (!el) return null;
      const style = getComputedStyle(el);
      const box = el.getBoundingClientRect();
      return { className: el.getAttribute('class'), display: style.display, position: style.position, width: style.width, height: style.height, zIndex: style.zIndex, overflowX: style.overflowX, overflowY: style.overflowY, flexWrap: style.flexWrap, gap: style.gap, fontSize: style.fontSize, box: { x: box.x, y: box.y, width: box.width, height: box.height } };
    };
    const header = document.querySelector('header');
    const save = Array.from(document.querySelectorAll('button')).find(el => el.textContent?.trim() === saveLabel);
    const stylesheets = Array.from(document.styleSheets).map(sheet => {
      let text = '';
      let rules = 0;
      try { rules = sheet.cssRules.length; text = Array.from(sheet.cssRules).map(rule => rule.cssText).join('\n'); } catch { /* blocked external sheet has no readable rules */ }
      return { href: sheet.href, rules, utilities: Object.fromEntries(['hidden', 'md:flex', 'w-80', 'h-12', 'z-50', 'flex'].map(name => [name, text.includes('.' + CSS.escape(name) + ' {')])) };
    });
    return {
      stylesheets,
      header: snapshot(header),
      logos: Array.from(document.querySelectorAll('[data-testid="header-logo-button"] > div')).map(snapshot),
      desktopActions: snapshot(Array.from(header?.querySelectorAll('*') ?? []).find(el => el.classList.contains('hidden') && el.classList.contains('md:flex')) ?? null),
      dropdown: snapshot(header?.querySelector('.top-full') ?? null),
      footer: snapshot(save?.parentElement?.parentElement ?? null),
      footerActions: snapshot(save?.parentElement ?? null),
      save: snapshot(save ?? null),
    };
  }, saveLabel);
}

async function fixture(page: Page, content = '你好 world\n\n第二段 story', locale: 'en' | 'zh' = 'en'): Promise<Fixture> {
  await mockResponsiveApp(page);
  await page.addInitScript(locale => localStorage.setItem('zenstory-language', locale), locale);
  let release = () => {};
  const gate = new Promise<void>(resolve => { release = resolve; });
  const files: Record<string, FakeFile> = {};
  for (const [id, project_id, title] of [['A', projectA, 'Chapter Alpha'], ['B', projectA, 'Chapter Beta'], ['C', projectB, 'Chapter Gamma']]) {
    files[id] = { id, project_id, title, content: id === 'A' ? content : `${title} body`, file_type: 'draft', parent_id: `folder-${project_id}`, order: 0, metadata: null, created_at: token0, updated_at: token0 };
  }
  const f: Fixture = { files, requests: [], blocked: [], errors: [], conflict: false, defer: false, release, observations: {}, locale };
  fixtures.set(page, f);
  page.on('pageerror', err => f.errors.push(err.message));
  const origin = new URL(test.info().project.use.baseURL as string).origin;
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) {
      if (url.origin === origin) return route.continue();
      f.blocked.push(`${request.method()} ${url.origin}${url.pathname}`);
      return route.abort('blockedbyclient');
    }
    const method = request.method();
    const body = request.postData() ? JSON.parse(request.postData()!) as Record<string, unknown> : null;
    f.requests.push({ method, path: url.pathname, body });
    const fulfill = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    const fileId = url.pathname.match(/^\/api\/v1\/files\/([^/]+)$/)?.[1];
    if (fileId && files[fileId]) {
      if (method === 'GET') return fulfill(files[fileId]);
      if (method === 'PUT') {
        const delayed = f.defer;
        const conflict = f.conflict;
        if (delayed) await gate;
        if (conflict) return fulfill({ error_code: 'ERR_CONFLICT', error_message: 'Fixture stale write', error_detail: { reason: 'stale_write', current_content: 'External server text', current_updated_at: '2026-10-06T10:00:02.000Z' } }, 409);
        files[fileId] = { ...files[fileId], ...body, updated_at: `2026-10-06T10:00:${String(puts(f).length).padStart(2, '0')}.000Z` } as FakeFile;
        return fulfill(files[fileId]);
      }
    }
    const treeProject = url.pathname.match(/^\/api\/v1\/projects\/([^/]+)\/file-tree$/)?.[1];
    if (treeProject && method === 'GET') {
      return fulfill({ tree: [{ id: `folder-${treeProject}`, title: 'Drafts', file_type: 'folder', metadata: { folder_type: 'draft' }, parent_id: null, order: 0, children: Object.values(files).filter(file => file.project_id === treeProject).map(file => ({ ...file, children: [] })) }] });
    }
    if (method === 'POST' && url.pathname.endsWith('/agent/suggest')) return fulfill({ suggestions: [] });
    if (method === 'POST' && url.pathname.endsWith('/stats/record')) return fulfill({ success: true });
    if (method === 'POST' && url.pathname === '/api/v1/editor/natural-polish') return fulfill({ text: 'A polished local story.' });
    if (method === 'GET' && url.pathname === '/api/v1/projects') return fulfill(responsiveProjects);
    if (method === 'GET' && /^\/api\/v1\/projects\/[^/]+$/.test(url.pathname)) return fulfill(responsiveProjects.find(p => p.id === url.pathname.split('/').at(-1)));
    // Reuse only auxiliary GET fakes from the existing safe responsive fixture.
    if (method === 'GET' && /auth\/me|subscription\/|persona|skills|points|\/recent$|\/agent\/suggest$|\/stats|materials|attachments/.test(url.pathname)) return route.fallback();
    f.blocked.push(`${method} ${url.origin}${url.pathname}`);
    return route.abort('blockedbyclient');
  });
  return f;
}
async function openA(page: Page) {
  await page.goto(`/project/${projectA}?file=A`);
  await expect(titleInput(page)).toHaveValue('Chapter Alpha');
  await expect(textarea(page)).toHaveCount(1);
}
async function selectFile(page: Page, name: string) {
  const tab = page.locator('#files-tab');
  if (await tab.count()) await tab.click();
  await page.getByText(name, { exact: true }).first().click();
}
async function receipt(page: Page, info: TestInfo) {
  const f = fixtures.get(page);
  if (!f) return;
  f.release();
  f.observations.computedStyles = await layoutStyles(page, f.locale === 'zh' ? '保存' : 'Save');
  await info.attach('offline-receipt', { body: JSON.stringify({ browser: page.context().browser()?.version(), viewport: page.viewportSize(), ...f, release: undefined }, null, 2), contentType: 'application/json' });
  expect(f.blocked.filter(url => !url.startsWith('GET https://fonts.googleapis.com/')), 'No unexpected API/static request is needed; Google Fonts remains intentionally blocked').toEqual([]);
  expect(f.errors).toEqual([]);
}
test.afterEach(async ({ page }, info) => receipt(page, info));

test('actual textarea: counts, native undo, clean shortcut, manual/debounce saves and file switch', async ({ page }) => {
  const f = await fixture(page);
  await openA(page);
  const input = textarea(page);
  await expect(page.getByText('Words 7', { exact: true })).toBeVisible();
  await expect(page.getByText('Paragraphs 2', { exact: true })).toBeVisible();
  await input.click();
  await input.press('ControlOrMeta+s');
  expect(puts(f)).toHaveLength(0);
  await input.press('ControlOrMeta+End');
  await page.keyboard.type('x');
  await input.press('ControlOrMeta+z');
  await expect(input).toHaveValue('你好 world\n\n第二段 story');
  await titleInput(page).fill('Renamed Alpha');
  await input.fill('Local draft 一 two');
  await expect(page.getByText('Unsaved', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => puts(f).length).toBe(1);
  expect(puts(f)[0].body).toMatchObject({ title: 'Renamed Alpha', content: 'Local draft 一 two', base_updated_at: token0 });
  await expect(page.getByText('Saved just now', { exact: true })).toBeVisible();
  await input.fill('Debounced draft 二 three');
  await expect.poll(() => puts(f).length, { timeout: 6000 }).toBe(2);
  expect(puts(f)[1].body).toMatchObject({ content: 'Debounced draft 二 three', base_updated_at: '2026-10-06T10:00:01.000Z' });
  await selectFile(page, 'Chapter Beta');
  await expect(titleInput(page)).toHaveValue('Chapter Beta');
  await selectFile(page, 'Renamed Alpha');
  await expect(input).toHaveValue('Debounced draft 二 three');
  f.observations = { nativeUndo: true, cleanShortcutPutCount: 0, manualAndDebouncePutCount: 2, returnedTokenAdvanced: true };
});

for (const conflict of [false, true]) {
  test(`humanize real selection and diff apply ${conflict ? '409 retains review' : 'success'}`, async ({ page }) => {
    const f = await fixture(page, 'A local story.');
    await openA(page);
    await textarea(page).click();
    await textarea(page).press('ControlOrMeta+a');
    await page.getByRole('button', { name: 'Humanize', exact: true }).click();
    await expect(page.getByRole('button', { name: /^(Apply changes|Finish review)$/ })).toBeVisible();
    expect(f.requests.find(r => r.path.endsWith('natural-polish'))?.body).toMatchObject({ selected_text: 'A local story.', project_id: projectA });
    f.conflict = conflict;
    await page.getByRole('button', { name: /^(Apply changes|Finish review)$/ }).click();
    await expect.poll(() => puts(f).length).toBe(1);
    expect(puts(f)[0].body).toMatchObject({ content: 'A polished local story.', base_updated_at: token0, change_type: 'ai_edit' });
    if (conflict) {
      await expect(page.getByText('This file changed elsewhere. Your text is safe — choose what to keep in the comparison view.', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: /^(Apply changes|Finish review)$/ })).toBeVisible();
    }
    else await expect(textarea(page)).toHaveValue('A polished local story.');
    f.observations = { conflict, reviewAfterCompletion: await page.getByRole('button', { name: /^(Apply changes|Finish review)$/ }).count() };
  });
}

test('narrow mobile panels retain actual textarea and responsive remount loads saved content', async ({ page }) => {
  const f = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openA(page);
  await page.locator('#editor-tab').click();
  await textarea(page).evaluate(el => el.setAttribute('data-fixture-identity', 'original'));
  await textarea(page).fill('Mobile local draft');
  await page.locator('#chat-tab').click();
  await page.locator('#editor-tab').click();
  await expect(textarea(page)).toHaveAttribute('data-fixture-identity', 'original');
  await expect(textarea(page)).toHaveValue('Mobile local draft');
  const buttons = await page.getByRole('button', { name: 'Save', exact: true }).boundingBox();
  f.observations.mobileSaveGeometry = buttons;
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => puts(f).length).toBe(1);
  await expect(page.getByText('Saved just now', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(textarea(page)).not.toHaveAttribute('data-fixture-identity', 'original');
  await expect(textarea(page)).toHaveValue('Mobile local draft');
  f.observations.mobileRetainedNode = true;
  f.observations.responsiveReplacedNode = true;
});

test('short empty editor keeps start actions and search shortcut hint reachable', async ({ page }) => {
  const f = await fixture(page);
  await page.setViewportSize({ width: 1024, height: 480 });
  await page.goto(`/project/${projectA}`);
  await page.getByRole('button', { name: 'More options', exact: true }).click();
  const heading = page.getByRole('heading', { name: 'Start writing', exact: true });
  await expect(heading).toBeVisible();
  const headingInitial = await heading.boundingBox();
  expect(headingInitial!.y).toBeGreaterThanOrEqual(48);
  const search = page.getByTestId('editor-panel').getByText('Search files', { exact: true });
  await search.scrollIntoViewIfNeeded();
  const box = await search.boundingBox();
  expect(box!.y + box!.height).toBeLessThanOrEqual(480);
  f.observations = { headingInitial, headingAfterHintScroll: await heading.boundingBox(), search: box };
});

for (const size of [1000, 10000, 100000]) {
  test(`actual renderer performance ${size} bilingual characters`, async ({ page }) => {
    const seed = '你好 story world。\n\n';
    const content = seed.repeat(Math.ceil(size / seed.length)).slice(0, size);
    const f = await fixture(page, content);
    await openA(page);
    const input = textarea(page);
    await expect(input).toHaveValue(content);
    await expect(page.getByText(`Words ${wordCount(content)}`, { exact: true })).toBeVisible();
    const before = { editorNodes: await input.evaluate(el => el.closest('[data-testid=editor-panel]')!.querySelectorAll('*').length), actualWordFooter: await page.getByText(`Words ${wordCount(content)}`, { exact: true }).textContent(), actualParagraphFooter: await page.getByText(`Paragraphs ${content.split(/\n\n+/).filter(Boolean).length}`, { exact: true }).textContent(), nodes: await page.locator('*').count(), textareas: await page.locator('textarea').count(), requests: f.requests.length, words: wordCount(content), paragraphs: content.split(/\n\n+/).filter(Boolean).length };
    await input.click();
    await input.evaluate(el => { const input = el as HTMLTextAreaElement; input.setSelectionRange(input.value.length, input.value.length); });
    const samples = [];
    for (let i = 0; i < 5; i++) {
      await input.evaluate(el => {
        const w = window as Window & { editorFrame?: Promise<number> };
        w.editorFrame = new Promise(resolve => el.addEventListener('input', () => {
          const start = performance.now();
          requestAnimationFrame(() => resolve(performance.now() - start));
        }, { once: true }));
      });
      await page.keyboard.type('x');
      samples.push(await page.evaluate(() => (window as Window & { editorFrame?: Promise<number> }).editorFrame));
    }
    await expect(input).toHaveValue(content + 'xxxxx');
    await expect(page.getByText(`Words ${wordCount(content + 'xxxxx')}`, { exact: true })).toBeVisible();
    const after = { editorNodes: await input.evaluate(el => el.closest('[data-testid=editor-panel]')!.querySelectorAll('*').length), actualWordFooter: await page.getByText(`Words ${wordCount(content + 'xxxxx')}`, { exact: true }).textContent(), nodes: await page.locator('*').count(), textareas: await page.locator('textarea').count(), requests: f.requests.length, words: wordCount(content + 'xxxxx') };
    f.observations = { size, before, after, inputEventToNextFrameMs: samples, viewport: page.viewportSize() };
    expect(puts(f)).toHaveLength(0);
  });
}

for (const departure of [false, true]) {
  test(`@watch final unmount rename must preserve replacement B selection (${departure ? 'project departure' : 'same project'})`, async ({ page }) => {
    const f = await fixture(page);
    await openA(page);
    await titleInput(page).fill('Late rename Alpha');
    await textarea(page).fill('Final old A draft');
    f.defer = true;
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => puts(f).length).toBe(1);
    expect(puts(f)[0].body).toMatchObject({ title: 'Late rename Alpha', content: 'Final old A draft', base_updated_at: token0 });
    if (departure) {
      // The mobile project dropdown is separately probed below; use the ordinary desktop switcher here.
      await page.setViewportSize({ width: 1440, height: 900 });
      await expect(textarea(page)).toHaveCount(1);
      await page.getByRole('button', { name: responsiveProjects[0].name, exact: true }).click();
      await page.getByText(responsiveProjects[1].name, { exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/project/${projectB}$`));
    }
    await selectFile(page, departure ? 'Chapter Gamma' : 'Chapter Beta');
    await expect(titleInput(page)).toHaveValue(departure ? 'Chapter Gamma' : 'Chapter Beta');
    f.observations.beforeOldCompletion = await titleInput(page).inputValue();
    f.release();
    await expect.poll(() => f.files.A.title).toBe('Late rename Alpha');
    await page.waitForTimeout(500); // allow the completed request's shared callbacks and resulting loader
    f.observations.afterOldCompletion = await titleInput(page).inputValue();
    f.observations.originalWriteCommitted = true;
    await expect(titleInput(page), 'Original A write may commit; replacement selection must remain B/C').toHaveValue(departure ? 'Chapter Gamma' : 'Chapter Beta');
  });
}


test('@watch mobile project menu item must accept ordinary pointer input', async ({ page }) => {
  const f = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openA(page);
  await page.locator('#editor-tab').click();
  await page.getByRole('button', { name: responsiveProjects[0].name, exact: true }).click();
  const choice = page.getByText(responsiveProjects[1].name, { exact: true });
  await expect(choice).toBeVisible();
  f.observations.stylesAtMenuOpen = await layoutStyles(page);
  f.observations.menuChoice = await choice.boundingBox();
  f.observations.hitTarget = await choice.evaluate(el => {
    const box = el.getBoundingClientRect();
    return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.outerHTML.slice(0, 300);
  });
  await choice.click({ timeout: 3000 });
  await expect(page).toHaveURL(new RegExp(`/project/${projectB}$`));
});

test('@watch final unmount 409 records review affinity while replacement B is selected', async ({ page }) => {
  const f = await fixture(page);
  await openA(page);
  await textarea(page).fill('Final old A draft');
  f.defer = true;
  f.conflict = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => puts(f).length).toBe(1);
  await selectFile(page, 'Chapter Beta');
  await expect(titleInput(page)).toHaveValue('Chapter Beta');
  f.observations.beforeOldCompletion = await titleInput(page).inputValue();
  f.release();
  await expect(page.getByText('This file changed elsewhere. Your text is safe — choose what to keep in the comparison view.', { exact: true })).toBeVisible();
  await expect(titleInput(page)).toHaveValue('Chapter Beta');
  const reviewButton = page.getByRole('button', { name: /^(Apply changes|Finish review)$/ });
  expect(await reviewButton.count()).toBe(0);
  f.observations.afterOldCompletion = await titleInput(page).inputValue();
  await selectFile(page, 'Chapter Alpha');
  await expect(reviewButton).toBeVisible();
  f.observations.oldReviewVisibleWhenReturningToA = true;
  f.observations.originalWriteCommitted = false;
  expect(f.files.A.content).toBe('你好 world\n\n第二段 story');
});


test('@watch dirty narrow editor Save control must fit the visible viewport', async ({ page }) => {
  const f = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openA(page);
  await page.locator('#editor-tab').click();
  await textarea(page).fill('Mobile local draft');
  await expect(page.getByText('Unsaved', { exact: true })).toBeVisible();
  const box = await page.getByRole('button', { name: 'Save', exact: true }).boundingBox();
  f.observations = { save: box, viewport: page.viewportSize(), dirtyStatusVisible: true, stylesWhileDirty: await layoutStyles(page) };
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width, 'The whole Save control should fit the 390px viewport while dirty').toBeLessThanOrEqual(390);
});


async function footerGeometry(page: Page, saveLabel: string) {
  return page.getByRole('button', { name: saveLabel, exact: true }).evaluate(save => {
    const footer = save.parentElement!.parentElement!;
    const actions = save.parentElement!;
    const box = (el: Element) => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height, bottom: b.bottom, right: b.right }; };
    const nav = document.querySelector('[data-testid="bottom-tabs"]');
    const scroll = document.querySelector('[data-editor-scroll-container="true"]')!;
    return { footer: { ...box(footer), scrollWidth: footer.scrollWidth, clientWidth: footer.clientWidth }, actions: { ...box(actions), scrollWidth: actions.scrollWidth, clientWidth: actions.clientWidth }, controls: Array.from(footer.querySelectorAll('button')).filter(el => getComputedStyle(el).display !== 'none').map(el => ({ label: el.textContent!.trim(), disabled: el.disabled, ...box(el) })), navTop: nav?.getBoundingClientRect().y, scroll: { clientHeight: scroll.clientHeight, scrollHeight: scroll.scrollHeight, scrollTop: scroll.scrollTop } };
  });
}
async function checkFooter(page: Page, f: Fixture, label: string, stage: string, desktop = false) {
  const geometry = await footerGeometry(page, label);
  f.observations[stage] = geometry;
  expect.soft(geometry.footer.scrollWidth, `${stage}: footer must not overflow`).toBeLessThanOrEqual(geometry.footer.clientWidth);
  expect.soft(geometry.actions.scrollWidth, `${stage}: actions must not overflow`).toBeLessThanOrEqual(geometry.actions.clientWidth);
  for (const control of geometry.controls) {
    expect.soft(control.x, `${stage}: ${control.label} left`).toBeGreaterThanOrEqual(0);
    expect.soft(control.right, `${stage}: ${control.label} right`).toBeLessThanOrEqual(page.viewportSize()!.width);
  }
  if (geometry.navTop !== undefined) expect.soft(geometry.footer.bottom).toBeLessThanOrEqual(geometry.navTop);
  if (desktop) {
    const centers = geometry.controls.map(c => c.y + c.height / 2);
    expect.soft(Math.max(...centers) - Math.min(...centers), 'Desktop footer keeps one visual row').toBeLessThanOrEqual(1);
  }
}

test.describe('footer locale and viewport matrix', () => {
  test.use({ hasTouch: true });
  for (const width of [320, 390, 1440]) {
    for (const locale of ['en', 'zh'] as const) {
      test(`footer ${locale} ${width}: dirty saving saved selection zoom and scroll`, async ({ page }) => {
        const labels = locale === 'en' ? { save: 'Save', dirty: 'Unsaved', saving: 'Saving...', saved: 'Saved just now', title: 'Enter title...', humanize: 'Humanize', zoom: 'Reset zoom' } : { save: '保存', dirty: '未保存', saving: '保存中...', saved: '刚刚保存', title: '请输入标题...', humanize: '去AI味', zoom: '重置缩放' };
        const content = '你好 story text。\n\n'.repeat(80);
        const f = await fixture(page, content, locale);
        await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
        await page.goto(`/project/${projectA}?file=A`);
        await expect(page.getByPlaceholder(labels.title)).toHaveValue('Chapter Alpha');
        if (width < 768) await page.locator('#editor-tab').click();
        const input = textarea(page);
        const saveButton = page.getByRole('button', { name: labels.save, exact: true });
        const humanize = page.getByRole('button', { name: labels.humanize, exact: true });
        await expect(saveButton).toBeDisabled(); await expect(humanize).toBeDisabled();
        await checkFooter(page, f, labels.save, 'initial', width === 1440);
        const draft = content + 'additional local draft';
        await input.fill(draft);
        await expect(page.getByText(labels.dirty, { exact: true })).toBeVisible();
        await expect(saveButton).toBeEnabled();
        await checkFooter(page, f, labels.save, 'dirty', width === 1440);
        f.defer = true;
        await saveButton.click();
        await expect.poll(() => puts(f).length).toBe(1);
        expect(puts(f)[0].body).toMatchObject({ content: draft, base_updated_at: token0 });
        await expect(page.getByText(labels.saving, { exact: true })).toBeVisible();
        await expect(saveButton).toBeDisabled();
        await checkFooter(page, f, labels.save, 'saving', width === 1440);
        f.release();
        await expect(page.getByText(labels.saved, { exact: true })).toBeVisible();
        await expect(saveButton).toBeDisabled();
        await checkFooter(page, f, labels.save, 'saved', width === 1440);
        const scroll = page.locator('[data-editor-scroll-container="true"]');
        await scroll.hover(); await page.mouse.wheel(0, 300);
        await expect.poll(() => scroll.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
        expect(await scroll.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
        f.observations.verticalScroll = await scroll.evaluate(el => ({ top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight }));
        await input.click(); await input.press('ControlOrMeta+a');
        await expect(humanize).toBeEnabled();
        await checkFooter(page, f, labels.save, 'selected', width === 1440);
        await input.press('ArrowRight'); await expect(humanize).toBeDisabled();
        // Fresh owned Chrome context only: browser-level emulated pinch, never hook/state injection.
        const area = await scroll.boundingBox();
        const session = await page.context().newCDPSession(page);
        try {
          // Keep both final coordinates within the outer panel's 50px swipe threshold.
          await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ id: 0, x: area!.x + 80, y: area!.y + 80 }, { id: 1, x: area!.x + 100, y: area!.y + 80 }] });
          await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ id: 0, x: area!.x + 70, y: area!.y + 80 }, { id: 1, x: area!.x + 110, y: area!.y + 80 }] });
          await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        } finally { await session.detach(); }
        const reset = page.getByTitle(labels.zoom, { exact: true });
        await expect(reset).toBeVisible();
        await checkFooter(page, f, labels.save, 'zoomed', width === 1440);
        await reset.click(); await expect(reset).toHaveCount(0);
        await checkFooter(page, f, labels.save, 'reset', width === 1440);
        expect(puts(f)).toHaveLength(1);
        expect(f.requests.filter(r => r.path.endsWith('natural-polish'))).toHaveLength(0);
      });
    }
  }
});
