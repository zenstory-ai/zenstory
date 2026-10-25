import { test, expect } from '@playwright/test';
import { mockResponsiveApi, responsiveProjects, responsiveUser } from './fixtures-responsive';

// The author asked the AI to remember two facts; update_project appended them to the notes
// (notes_added). The chat card lists them with one undo, live and when loaded from history.
const before = '旧约定\n\n【作者确认】\n原确认';
const added = ['女主要到第五章才出场', '主角左手有旧伤，阴天会疼'];
const saved = `${before}\n【未标来源】\n${added.join('\n')}`;
const toolResult = { project_id: 'responsive-project-0', updated_fields: ['notes'], notes_added: added };
const tool = { id: 'remember-tool', name: 'update_project', arguments: { notes: added.join('；') }, status: 'success', result: { status: 'success', data: toolResult } };
const reply = { id: 'remember-assistant', session_id: 'remember-session', role: 'assistant', content: 'OK', created_at: '2026-10-25T10:00:00Z', tool_calls: JSON.stringify([tool]), metadata: JSON.stringify({ display_events: [{ type: 'tool_call', tool_call_index: 0 }, { type: 'content', content: 'OK' }] }) };

for (const { width, language, theme } of [
  { width: 390, language: 'zh', theme: 'light' },
  { width: 1440, language: 'en', theme: 'dark' },
]) {
  test(`remembered notes card undoes exactly its entries at ${width} ${language} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockResponsiveApi(page);
    await page.addInitScript(({ language, theme, user }) => {
      localStorage.setItem('zenstory-language', language);
      localStorage.setItem('zenstory-theme', theme);
      localStorage.setItem('user', JSON.stringify(user));
      localStorage.setItem('access_token', 'local-notes-fixture');
      localStorage.setItem('refresh_token', 'local-notes-fixture');
      localStorage.setItem('auth_validated_at', String(Date.now()));
    }, { language, theme, user: responsiveUser });
    let notes = before;
    let completed = false;
    let streams = 0;
    const patches: Array<Record<string, unknown>> = [];
    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ json: completed ? [reply] : [] }));
    await page.route('**/api/v1/agent/stream', async route => {
      streams++;
      notes = saved;
      completed = true;
      const events: Array<[string, unknown]> = [
        ['tool_call', { tool_use_id: tool.id, tool_name: tool.name, arguments: tool.arguments }],
        ['tool_result', { tool_use_id: tool.id, tool_name: tool.name, status: 'success', data: toolResult }],
        ['content_start', {}], ['content', { text: 'OK' }], ['content_end', {}],
        ['done', { assistant_message_id: reply.id, session_id: reply.session_id }],
      ];
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('') });
    });
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
    const zh = language === 'zh';
    const title = zh ? '已记入 AI 记忆' : 'Saved to AI Memory';
    const undo = zh ? '撤销' : 'Undo';
    const undone = zh ? '已撤销' : 'Undone';
    const gone = zh ? '这些内容已不在 AI 记忆中，无需撤销' : 'These notes are no longer in AI Memory; nothing to undo';
    const memory = zh ? 'AI 记忆' : 'AI Memory';
    const shots = process.env.AUDIT_SHOTS || test.info().outputDir;
    const chatTab = async () => { if (width < 768) await page.locator('#chat-tab').click(); };
    const chat = page.locator('[data-testid="chat-panel"], #chat-panel');

    await page.goto('/project/responsive-project-0');
    await chatTab();
    await page.getByTestId('chat-input').fill(zh ? '记住：女主要到第五章才出场；主角左手有旧伤，阴天会疼' : 'Remember: two facts');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('chat-input')).toBeEnabled();
    await expect(chat.getByText(title, { exact: true })).toBeVisible();
    for (const entry of added) await expect(chat.getByRole('listitem').filter({ hasText: entry })).toBeVisible();
    const undoButton = chat.getByRole('button', { name: undo, exact: true });
    await undoButton.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shots}/remember-${width}-${language}-${theme}-saved.png` });

    // The author edits one entry elsewhere first: undo removes only the exact remaining match.
    const edited = saved.replace('阴天会疼', '雨天会疼');
    notes = edited;
    await undoButton.focus();
    await page.keyboard.press('Enter');
    await expect(chat.getByText(undone, { exact: true })).toBeVisible();
    await expect(chat.getByText(zh ? '有 1 条已被你改过或删掉，保留原样' : '1 note you edited or removed was left as is')).toBeVisible();
    expect(patches).toEqual([{ notes: `${before}\n【未标来源】\n主角左手有旧伤，雨天会疼`, expected_notes: edited }]);
    expect(notes).toBe(`${before}\n【未标来源】\n主角左手有旧伤，雨天会疼`);
    await expect(undoButton).toHaveCount(0);
    await page.screenshot({ path: `${shots}/remember-${width}-${language}-${theme}-undone.png` });

    // The AI 记忆 panel shows the notes after undo.
    await page.getByTitle(memory, { exact: true }).click();
    const dialog = page.getByRole('dialog', { name: memory });
    await expect(dialog.getByRole('textbox', { name: zh ? '备注' : 'Notes', exact: true }))
      .toHaveValue('旧约定\n\n原确认\n主角左手有旧伤，雨天会疼');
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    // History-loaded card: the entry it would remove is already gone, so nothing is written.
    await page.reload();
    await chatTab();
    await expect(chat.getByText(title, { exact: true })).toBeVisible();
    await chat.getByRole('button', { name: undo, exact: true }).click();
    await expect(chat.getByText(gone, { exact: true })).toBeVisible();
    expect(patches).toHaveLength(1);

    // A history card whose entries are still stored undoes them via the guarded PATCH.
    notes = saved;
    await page.reload();
    await chatTab();
    await chat.getByRole('button', { name: undo, exact: true }).click();
    await expect(chat.getByText(undone, { exact: true })).toBeVisible();
    expect(patches[1]).toEqual({ notes: before, expected_notes: saved });
    expect(streams).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `${shots}/remember-${width}-${language}-${theme}-history-undone.png` });
  });
}
