import { test, expect } from '@playwright/test';
import { mockResponsiveApi, responsiveProjects, responsiveUser } from './fixtures-responsive';

for (const { width, language, theme } of [
  { width: 390, language: 'zh', theme: 'light' },
  { width: 768, language: 'en', theme: 'dark' },
  { width: 1440, language: 'en', theme: 'dark' },
]) {
  test(`author notes hide legacy AI proposals and recover a stale save at ${width} ${language} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await mockResponsiveApi(page);
    await page.addInitScript(({ language, theme, user }) => {
      localStorage.setItem('zenstory-language', language);
      localStorage.setItem('zenstory-theme', theme);
      localStorage.setItem('user', JSON.stringify(user));
      localStorage.setItem('access_token', 'local-notes-fixture');
      localStorage.setItem('refresh_token', 'local-notes-fixture');
      localStorage.setItem('auth_validated_at', String(Date.now()));
    }, { language, theme, user: responsiveUser });
    // Notes saved before the AI-proposal removal. This fixture returns them raw (as a server
    // without the read-side cleanup would), so the panel itself must not show the legacy block.
    let notes = '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n第一组；保留补充\n第二组；保留结尾';
    const originalNotes = notes;
    const patches: Array<Record<string, unknown>> = [];
    let modelRequests = 0;
    await page.route('**/api/v1/agent/stream', route => { modelRequests++; return route.abort(); });
    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ json: [] }));
    await page.route('**/api/v1/projects/responsive-project-0', async route => {
      const request = route.request();
      if (request.method() === 'PATCH') {
        const body = request.postDataJSON();
        patches.push(body);
        if (body.expected_notes !== notes) {
          await route.fulfill({ status: 409, json: { detail: 'ERR_RESOURCE_CONFLICT', error_code: 'ERR_RESOURCE_CONFLICT' } });
          return;
        }
        notes = body.notes;
      }
      await route.fulfill({ json: { ...responsiveProjects[0], summary: 'Existing synopsis', notes } });
    });
    const memory = language === 'zh' ? 'AI 记忆' : 'AI Memory';
    const save = language === 'zh' ? '保存' : 'Save';
    const viewLatest = language === 'zh' ? '查看最新版本' : 'View latest version';
    const saveMine = language === 'zh' ? '覆盖并保存我的版本' : 'Overwrite and save my version';
    const shots = process.env.AUDIT_SHOTS || test.info().outputDir;
    const openMemory = async () => {
      if (width < 768) await page.locator('#chat-tab').click();
      await page.getByTitle(memory, { exact: true }).click();
    };
    await page.goto('/project/responsive-project-0');
    await openMemory();
    const dialog = page.getByRole('dialog', { name: memory });
    const notesInput = dialog.getByRole('textbox', { name: language === 'zh' ? '写作约定' : 'Writing Conventions', exact: true });
    await expect(notesInput).toHaveValue('旧约定\n\n原确认');
    await expect(dialog.getByText(/提议|proposal/i)).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: /采纳|忽略|Adopt|Ignore/ })).toHaveCount(0);
    await page.screenshot({ path: `${shots}/notes-${width}-${language}-${theme}-ready.jpg`, type: 'jpeg' });

    // The first author save drops the legacy block.
    await notesInput.fill('旧约定\n\n原确认\n本地新约定');
    await dialog.getByRole('button', { name: save, exact: true }).click();
    await expect.poll(() => patches.length).toBe(1);
    expect(patches[0]).toEqual({ expected_notes: originalNotes, notes: '旧约定\n\n【作者确认】\n原确认\n【未标来源】\n本地新约定' });
    expect(notes).not.toContain('提议');
    await expect(dialog).toHaveCount(0);

    // Another client saves while this editor still owns an older draft base.
    await openMemory();
    await expect(notesInput).toHaveValue('旧约定\n\n原确认\n本地新约定');
    await notesInput.fill('旧约定\n\n原确认\n本地新约定\n再补一条');
    notes = '旧约定\n\n【作者确认】\n原确认\n【未标来源】\n另一端新约定';
    await dialog.getByRole('button', { name: save, exact: true }).click();
    await expect(dialog.getByRole('alert')).toBeInViewport({ ratio: 1 });
    await expect(dialog.getByRole('button', { name: viewLatest, exact: true })).toBeInViewport({ ratio: 1 });
    await expect(dialog.getByRole('button', { name: viewLatest, exact: true })).toBeFocused();
    await expect(notesInput).toHaveValue('旧约定\n\n原确认\n本地新约定\n再补一条');
    expect(patches).toHaveLength(2);
    await page.screenshot({ path: `${shots}/notes-${width}-${language}-${theme}-conflict.jpg`, type: 'jpeg' });
    await dialog.getByRole('button', { name: viewLatest, exact: true }).click();
    await expect(dialog.getByRole('textbox', { name: language === 'zh' ? '最新保存版本' : 'Latest saved version', exact: true }))
      .toHaveValue('旧约定\n\n原确认\n另一端新约定');
    await dialog.getByRole('button', { name: saveMine, exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ animations: 'disabled', path: `${shots}/notes-${width}-${language}-${theme}-latest.jpg`, type: 'jpeg' });
    await dialog.getByRole('button', { name: saveMine, exact: true }).click();
    await expect.poll(() => patches.length).toBe(3);
    expect(patches[2].expected_notes).toBe('旧约定\n\n【作者确认】\n原确认\n【未标来源】\n另一端新约定');
    expect(notes).toContain('再补一条');

    await page.reload();
    await openMemory();
    await expect(notesInput).toHaveValue('旧约定\n\n原确认\n本地新约定\n再补一条');
    expect(modelRequests).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `${shots}/notes-${width}-${language}-${theme}-persisted.jpg`, type: 'jpeg' });
  });
}
