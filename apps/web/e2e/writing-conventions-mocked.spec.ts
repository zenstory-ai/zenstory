import { test, expect } from '@playwright/test';
import { mockResponsiveApi, responsiveProjects, responsiveUser } from './fixtures-responsive';

const added = ['女主要到第五章才出场', '主角左手有旧伤，阴天会疼'];
const saved = `旧约定\n\n【作者确认】\n原确认\n【未标来源】\n${added.join('\n')}`;
const toolResult = { project_id: 'responsive-project-0', updated_fields: ['summary', 'notes'], notes_added: added };
const tool = { id: 'remember-tool', name: 'update_project', arguments: { notes: added.join('；') }, status: 'success', result: { status: 'success', data: toolResult } };
const reply = { id: 'remember-assistant', session_id: 'remember-session', role: 'assistant', content: 'OK', created_at: '2026-10-25T10:00:00Z', tool_calls: JSON.stringify([tool]), metadata: JSON.stringify({ display_events: [{ type: 'tool_call', tool_call_index: 0 }, { type: 'content', content: 'OK' }] }) };

for (const { width, language, theme } of [
  { width: 390, language: 'zh', theme: 'light' },
  { width: 1440, language: 'en', theme: 'dark' },
]) {
  test(`remembered entries open Writing Conventions at ${width} ${language} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await mockResponsiveApi(page);
    await page.addInitScript(({ language, theme, user }) => {
      localStorage.setItem('zenstory-language', language);
      localStorage.setItem('zenstory-theme', theme);
      localStorage.setItem('user', JSON.stringify(user));
      localStorage.setItem('access_token', 'local-notes-fixture');
      localStorage.setItem('refresh_token', 'local-notes-fixture');
      localStorage.setItem('auth_validated_at', String(Date.now()));
    }, { language, theme, user: responsiveUser });
    let completed = false;
    let patches = 0;
    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ json: completed ? [reply] : [] }));
    await page.route('**/api/v1/agent/stream', async route => {
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
      if (route.request().method() === 'PATCH') patches++;
      await route.fulfill({ json: { ...responsiveProjects[0], summary: 'Existing synopsis', notes: saved } });
    });

    const zh = language === 'zh';
    const title = zh ? '已加入写作约定' : 'Added to Writing Conventions';
    const openLabel = zh ? '查看并编辑' : 'View and edit';
    const fieldLabel = zh ? '写作约定' : 'Writing Conventions';
    const shots = process.env.AUDIT_SHOTS || test.info().outputDir;
    const chat = page.locator('[data-testid="chat-panel"], #chat-panel');
    const openChat = async () => { if (width < 768) await page.locator('#chat-tab').click(); };

    await page.goto('/project/responsive-project-0');
    await openChat();
    await page.getByTestId('chat-input').fill(zh ? '记住这两条约定' : 'Remember these two agreements');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('chat-input')).toBeEnabled();
    await expect(chat.getByText(title, { exact: true })).toBeVisible();
    for (const entry of added) await expect(chat.getByRole('listitem').filter({ hasText: entry })).toBeVisible();
    await expect(chat.getByRole('button', { name: /撤销|Undo/ })).toHaveCount(0);
    await expect(chat.getByText(zh ? '已更新项目信息' : 'Project info updated', { exact: false })).toBeVisible();
    await page.screenshot({ animations: 'disabled', path: `${shots}/writing-conventions-card-${width}-${language}-${theme}.png` });

    const trigger = chat.getByRole('button', { name: openLabel, exact: true });
    await trigger.click();
    const dialog = page.getByRole('dialog', { name: zh ? 'AI 记忆' : 'AI Memory' });
    const input = dialog.getByRole('textbox', { name: fieldLabel, exact: true });
    await expect(input).toBeFocused();
    await expect(input).toHaveValue('旧约定\n\n原确认\n女主要到第五章才出场\n主角左手有旧伤，阴天会疼');
    await page.screenshot({ animations: 'disabled', path: `${shots}/writing-conventions-dialog-${width}-${language}-${theme}.png` });
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
    expect(patches).toBe(0);

    await page.reload();
    await openChat();
    await expect(chat.getByText(title, { exact: true })).toBeVisible();
    await chat.getByRole('button', { name: openLabel, exact: true }).click();
    await expect(page.getByRole('textbox', { name: fieldLabel, exact: true })).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
