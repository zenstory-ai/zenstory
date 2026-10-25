import { test, expect } from '@playwright/test';
import { mockResponsiveApi, responsiveProjects, responsiveUser } from './fixtures-responsive';
import { authorNotesText as shown } from '../src/lib/projectNotes';

for (const { width, language, theme } of [
  { width: 390, language: 'zh', theme: 'light' },
  { width: 768, language: 'en', theme: 'dark' },
  { width: 1440, language: 'en', theme: 'light' },
]) {
  test(`human notes adoption and stale-save recovery at ${width} ${language} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockResponsiveApi(page);
    await page.addInitScript(({ language, theme, user }) => {
      localStorage.setItem('zenstory-language', language);
      localStorage.setItem('zenstory-theme', theme);
      localStorage.setItem('user', JSON.stringify(user));
      localStorage.setItem('access_token', 'local-notes-fixture');
      localStorage.setItem('refresh_token', 'local-notes-fixture');
      localStorage.setItem('auth_validated_at', String(Date.now()));
    }, { language, theme, user: responsiveUser });
    let notes = '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n第一组；保留补充\n第二组；保留结尾';
    const originalNotes = notes;
    // Stored notes keep fixed Chinese section headers; the textarea shows only the author's entries.
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
    const adopt = language === 'zh' ? '全部采纳' : 'Adopt all';
    const adoptOne = (entry: string) => language === 'zh' ? `采纳：${entry}` : `Adopt: ${entry}`;
    const save = language === 'zh' ? '保存' : 'Save';
    const viewLatest = language === 'zh' ? '查看最新备注' : 'View latest notes';
    const useLatest = language === 'zh' ? '使用最新备注' : 'Use latest notes';
    await page.goto('/project/responsive-project-0');
    if (width < 768) await page.locator('#chat-tab').click();
    await page.getByTitle(memory, { exact: true }).click();
    const dialog = page.getByRole('dialog', { name: memory });
    const notesInput = dialog.getByRole('textbox', { name: language === 'zh' ? '备注' : 'Notes', exact: true });
    const adoptButton = dialog.getByRole('button', { name: adopt, exact: true });
    await expect(notesInput).toHaveValue(shown(originalNotes));
    await expect(notesInput).not.toHaveValue(/【/);
    // Four pending proposals: three rows, the notes field right after them, one behind the disclosure.
    // The disclosure renames itself when open, so it is addressed by either label.
    const showMore = dialog.getByRole('button', { name: language === 'zh' ? /^(展开其余 1 条|收起)$/ : /^(Show 1 more|Show less)$/ });
    await expect(dialog.getByRole('listitem')).toHaveCount(3);
    await expect(showMore).toHaveAttribute('aria-expanded', 'false');
    await showMore.click();
    await expect(dialog.getByRole('listitem')).toHaveCount(4);
    await expect(showMore).toHaveAttribute('aria-expanded', 'true');
    await showMore.click();
    await expect(dialog.getByRole('listitem')).toHaveCount(3);
    await adoptButton.scrollIntoViewIfNeeded();
    // Compact 32px toolbar action on desktop; 44px touch target below 768px.
    expect((await adoptButton.boundingBox())!.height).toBeGreaterThanOrEqual(width < 768 ? 44 : 32);
    await page.screenshot({ path: `${process.env.AUDIT_SHOTS || test.info().outputDir}/notes-${width}-${language}-${theme}-ready.jpg`, type: 'jpeg' });
    await adoptButton.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => patches.length).toBe(1);
    expect(patches[0]).toEqual({ expected_notes: originalNotes, notes: '旧约定\n\n【作者确认】\n原确认\n第一组；保留补充\n第二组；保留结尾' });
    // The emptied list unmounts; focus lands on the notes label, not in the textarea (which would
    // open the on-screen keyboard on touch devices).
    await expect(adoptButton).toHaveCount(0);
    await expect(notesInput).not.toBeFocused();
    await expect(dialog.locator('label', { hasText: language === 'zh' ? /^备注$/ : /^Notes$/ })).toBeFocused();
    // A separate client changes the server while this editor still owns an older draft base.
    notes += '\n\n【AI 提议·待作者确认】\n另一端新提议';
    await notesInput.fill(shown(patches[0].notes as string) + '\n本地新约定');
    await dialog.getByRole('button', { name: save, exact: true }).click();
    await expect(dialog.getByRole('alert')).toBeVisible();
    await expect(notesInput).toHaveValue(shown(patches[0].notes as string) + '\n本地新约定');
    expect(patches).toHaveLength(2);
    expect(patches[1].notes, 'the save keeps confirmed entries under their header').toBe(patches[0].notes + '\n【未标来源】\n本地新约定');
    await page.screenshot({ path: `${process.env.AUDIT_SHOTS || test.info().outputDir}/notes-${width}-${language}-${theme}-conflict.jpg`, type: 'jpeg' });
    await dialog.getByRole('button', { name: viewLatest, exact: true }).click();
    await expect(dialog.getByRole('textbox', { name: language === 'zh' ? '最新备注' : 'Latest notes', exact: true })).toHaveValue(shown(notes));
    await expect(notesInput).toHaveValue(shown(patches[0].notes as string) + '\n本地新约定');
    expect(patches).toHaveLength(2);
    await page.screenshot({ path: `${process.env.AUDIT_SHOTS || test.info().outputDir}/notes-${width}-${language}-${theme}-latest.jpg`, type: 'jpeg' });
    await dialog.getByRole('button', { name: useLatest, exact: true }).click();
    await expect(notesInput).toHaveValue(shown(notes));
    await dialog.getByRole('button', { name: adoptOne('另一端新提议'), exact: true }).click();
    await expect.poll(() => patches.length).toBe(3);
    await expect(dialog.getByRole('button', { name: adoptOne('另一端新提议'), exact: true })).toHaveCount(0);
    await expect(notesInput).toHaveValue(shown(notes));
    expect(notes).toContain('另一端新提议');
    expect(notes).not.toContain('本地新约定');
    expect(notes).not.toContain('【AI 提议');
    await page.reload();
    if (width < 768) await page.locator('#chat-tab').click();
    await page.getByTitle(memory, { exact: true }).click();
    await expect(notesInput).toHaveValue(shown(notes));
    expect(modelRequests).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `${process.env.AUDIT_SHOTS || test.info().outputDir}/notes-${width}-${language}-${theme}-persisted.jpg`, type: 'jpeg' });
  });
}
