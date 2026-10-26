import { expect, test, type Page } from '@playwright/test';
import { mockResponsiveApi, responsiveUser } from './fixtures-responsive';

type Language = 'zh' | 'en';
type Theme = 'light' | 'dark';

type VisualCase = {
  width: 390 | 1440;
  language: Language;
  theme: Theme;
};

type QuietCase = VisualCase & {
  savedOutput: boolean;
  layer?: 'tool_call' | 'collaboration';
  limit?: 'wall_clock';
};

const PROJECT_ID = 'responsive-project-0';
const CACHE_KEY = `zenstory_suggestions_cache_${PROJECT_ID}`;
const staleSuggestion = 'STALE-SUGGESTION-MUST-NOT-RETURN';
const placeholderTitle = 'ROLLED-BACK-PLACEHOLDER-MUST-STAY-HIDDEN';
const internalReason = 'INTERNAL empty_file_correction rollback budget topology';

const quietCases: QuietCase[] = [
  { width: 390, language: 'zh', theme: 'light', savedOutput: true },
  { width: 1440, language: 'en', theme: 'dark', savedOutput: true },
  { width: 390, language: 'en', theme: 'dark', savedOutput: false },
  { width: 1440, language: 'zh', theme: 'light', savedOutput: false },
  { width: 390, language: 'zh', theme: 'dark', savedOutput: true, layer: 'tool_call', limit: 'wall_clock' },
  { width: 1440, language: 'en', theme: 'light', savedOutput: true, layer: 'collaboration' },
];

const riskCases: VisualCase[] = [
  { width: 390, language: 'zh', theme: 'dark' },
  { width: 1440, language: 'en', theme: 'light' },
  { width: 390, language: 'en', theme: 'light' },
  { width: 1440, language: 'zh', theme: 'dark' },
];

const sse = (events: Array<[string, unknown]>) => events
  .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
  .join('');

const progressTool = (language: Language) => {
  const title = language === 'zh' ? '第七章 当前修改进度' : 'Chapter Seven current progress';
  return {
    title,
    history: {
      id: 'progress-query',
      name: 'query_files',
      arguments: { id: 'draft-6' },
      status: 'success',
      result: [{ id: 'draft-6', title, file_type: 'draft', content: 'synthetic fixture content' }],
    },
  };
};

const displayEvents = (toolIndex = 0) => [
  { type: 'tool_call', tool_call_index: toolIndex },
  {
    type: 'router_decided',
    data: {
      initial_agent: 'planner',
      workflow_plan: 'INTERNAL planner -> writer topology',
      workflow_agents: ['planner', 'writer'],
    },
  },
];

async function prepareMockedApp(page: Page, visual: VisualCase) {
  await page.setViewportSize({ width: visual.width, height: visual.width === 390 ? 844 : 900 });
  await mockResponsiveApi(page);
  await page.addInitScript(({ user, language, theme, cacheKey, suggestion }) => {
    localStorage.setItem('zenstory-language', language);
    localStorage.setItem('zenstory-theme', theme);
    localStorage.setItem('user', JSON.stringify(user));
    localStorage.setItem('access_token', 'quiet-delivery-fixture');
    localStorage.setItem('refresh_token', 'quiet-delivery-fixture');
    localStorage.setItem('auth_validated_at', String(Date.now()));
    localStorage.setItem(cacheKey, JSON.stringify({ suggestions: [suggestion], updatedAt: Date.now() }));
  }, {
    user: responsiveUser,
    language: visual.language,
    theme: visual.theme,
    cacheKey: CACHE_KEY,
    suggestion: staleSuggestion,
  });
}

async function openChat(page: Page, width: number) {
  if (width < 768) await page.locator('#chat-tab').click();
  await expect(page.getByTestId('chat-input')).toBeVisible();
}

async function assertNoSuggestions(page: Page, suggestRequests: () => number) {
  await page.clock.runFor(12_000);
  expect(suggestRequests()).toBe(0);
  await expect(page.getByRole('button', { name: staleSuggestion, exact: true })).toHaveCount(0);
  expect(await page.evaluate(key => localStorage.getItem(key), CACHE_KEY)).toBeNull();
}

async function assertNoInternalDeliveryText(page: Page) {
  const chat = page.locator('[data-testid="chat-panel"], #chat-panel');
  for (const text of [placeholderTitle, internalReason, 'planner -> writer', 'empty_file_correction']) {
    await expect(chat.getByText(text, { exact: false })).toHaveCount(0);
  }
  await expect(chat.getByText(/11\s*\/\s*12|remaining\s*1|剩余\s*1|回滚|rollback|rolled back/i)).toHaveCount(0);
}

for (const visual of quietCases) {
  const state = visual.layer ?? (visual.savedOutput ? 'saved unnamed output' : 'all placeholders removed');
  test(`${state} is canonical live and after reload at ${visual.width}px ${visual.language} ${visual.theme}`, async ({ page }) => {
    await prepareMockedApp(page, visual);
    const tool = progressTool(visual.language);
    const layer = visual.layer ?? 'empty_file_correction';
    const removedFiles = visual.layer ? [] : [placeholderTitle];
    const rolledBackFiles = removedFiles.map(title => ({ id: 'placeholder-1', title }));
    const writtenFiles = visual.layer ? [{ id: 'draft-6', title: tool.title }] : [];
    let finished = false;
    let streamRequests = 0;
    let suggestRequests = 0;

    const statusCard = {
      type: 'iteration_exhausted',
      layer,
      limit: visual.limit,
      iterationsUsed: 11,
      maxIterations: 12,
      reason: internalReason,
      lastAgent: 'writer',
      rolledBackFiles: removedFiles,
    };
    const history = {
      id: `quiet-${visual.language}-${visual.savedOutput ? 'saved' : 'empty'}`,
      session_id: 'quiet-session',
      role: 'assistant',
      content: '',
      created_at: '2026-10-26T12:00:00Z',
      tool_calls: JSON.stringify([tool.history]),
      metadata: JSON.stringify({
        stop_reason: visual.limit === 'wall_clock' ? 'run_deadline_exceeded' : undefined,
        display_events: [
          ...displayEvents(),
          {
            type: 'iteration_exhausted',
            data: {
              layer,
              limit: visual.limit,
              iterations_used: 11,
              max_iterations: 12,
              reason: internalReason,
              last_agent: 'writer',
              rolled_back_files: rolledBackFiles,
            },
          },
        ],
        status_cards: [statusCard],
        stop_outcome: {
          reason: 'incomplete',
          charged: true,
          saved_output: visual.savedOutput,
          no_file_changes: !visual.savedOutput,
          written_files: writtenFiles,
          removed_files: removedFiles,
        },
      }),
    };

    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ json: finished ? [history] : [] }));
    await page.route('**/api/v1/agent/suggest', route => {
      suggestRequests += 1;
      return route.fulfill({ json: { suggestions: [staleSuggestion] } });
    });
    await page.route('**/api/v1/agent/stream', async route => {
      streamRequests += 1;
      finished = true;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([
          ['tool_call', { tool_use_id: tool.history.id, tool_name: tool.history.name, arguments: tool.history.arguments }],
          ['tool_result', { tool_use_id: tool.history.id, tool_name: tool.history.name, status: 'success', data: tool.history.result }],
          ['router_decided', displayEvents()[1].data],
          ['iteration_exhausted', {
            layer, limit: visual.limit, iterations_used: 11, max_iterations: 12,
            reason: internalReason, last_agent: 'writer',
            rolled_back_files: rolledBackFiles,
          }],
          ['done', {
            assistant_message_id: history.id,
            session_id: history.session_id,
            delivery_incomplete: true,
            saved_output: visual.savedOutput,
            no_file_changes: !visual.savedOutput,
            written_files: writtenFiles,
            unwritten_files: [],
          }],
        ]),
      });
    });

    await page.goto(`/project/${PROJECT_ID}`);
    await openChat(page, visual.width);
    await page.getByTestId('chat-input').fill(visual.language === 'zh' ? '继续处理当前章节' : 'Continue the current chapter');
    await page.getByTestId('send-button').click();

    const expected = visual.savedOutput
      ? visual.language === 'zh'
        ? '已保存本轮完成的修改，可以继续写后续内容。'
        : "This round's completed changes were saved. You can continue with the remaining work."
      : visual.language === 'zh'
        ? '本轮尚未写入新内容，可以继续完成。'
        : 'Nothing new was written in this round. You can continue the work.';
    const continueLabel = visual.language === 'zh' ? '继续完成' : 'Keep going';
    const note = page.getByTestId('round-end-note');

    await expect(note).toHaveText(expected);
    await expect(note).toHaveCount(1);
    await expect(page.getByText(tool.title, { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: continueLabel, exact: true })).toHaveCount(1);
    await expect(page.getByTestId('next-step-card')).toHaveCount(0);
    await assertNoInternalDeliveryText(page);
    await assertNoSuggestions(page, () => suggestRequests);
    expect(streamRequests).toBe(1);

    await page.screenshot({
      animations: 'disabled',
      path: test.info().outputPath(`quiet-${visual.width}-${visual.language}-${visual.theme}-${visual.savedOutput ? 'saved' : 'empty'}-live.png`),
    });

    await page.reload();
    await openChat(page, visual.width);
    await expect(page.getByTestId('round-end-note')).toHaveText(expected);
    await expect(page.getByTestId('round-end-note')).toHaveCount(1);
    await expect(page.getByText(tool.title, { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: continueLabel, exact: true })).toHaveCount(1);
    await expect(page.getByTestId('next-step-card')).toHaveCount(0);
    await assertNoInternalDeliveryText(page);
    await assertNoSuggestions(page, () => suggestRequests);
    expect(streamRequests).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

for (const terminal of ['done', 'error'] as const) {
  test(`empty-file internals stay hidden while the terminal is delayed, then recover on ${terminal}`, async ({ page }) => {
    await prepareMockedApp(page, { width: 390, language: 'zh', theme: 'dark' });
    const tool = progressTool('zh');
    const early = sse([
      ['tool_call', { tool_use_id: tool.history.id, tool_name: tool.history.name, arguments: tool.history.arguments }],
      ['tool_result', { tool_use_id: tool.history.id, tool_name: tool.history.name, status: 'success', data: tool.history.result }],
      ['iteration_exhausted', { layer: 'empty_file_correction', iterations_used: 11, max_iterations: 12,
        reason: internalReason, rolled_back_files: [{ id: 'placeholder-1', title: placeholderTitle }] }],
    ]);
    const ending = terminal === 'done'
      ? sse([['done', { delivery_incomplete: true, saved_output: true, written_files: [], unwritten_files: [] }]])
      : sse([['error', { code: 'ERR_AGENT_FILE_SAVE_FAILED', retryable: true,
        unwritten_files: [{ id: 'draft-risk', title: '仍待确认的正文' }] }]]);
    // route.fulfill buffers its whole body. A gated browser stream exposes the
    // actual pre-terminal interval instead of asserting only the final screen.
    await page.addInitScript(({ early, ending }) => {
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : input instanceof Request ? input.url : input.href;
        if (!url.split('?')[0].endsWith('/api/v1/agent/stream')) return originalFetch(input, init);
        const encoder = new TextEncoder();
        const stream = new ReadableStream({ start(controller) {
          controller.enqueue(encoder.encode(early));
          (window as unknown as { releaseQuietTerminal: () => void }).releaseQuietTerminal = () => {
            controller.enqueue(encoder.encode(ending));
            controller.close();
          };
        } });
        return new Response(stream, { headers: { 'content-type': 'text/event-stream' } });
      };
    }, { early, ending });
    await page.goto(`/project/${PROJECT_ID}`);
    await openChat(page, 390);
    await page.getByTestId('chat-input').fill('继续处理当前章节');
    await page.getByTestId('send-button').click();
    await expect(page.getByText(tool.title, { exact: true })).toBeVisible();
    await assertNoInternalDeliveryText(page);
    await expect(page.getByTestId('round-end-note')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '继续完成', exact: true })).toHaveCount(0);

    await page.evaluate(() => (window as unknown as { releaseQuietTerminal: () => void }).releaseQuietTerminal());
    if (terminal === 'done') {
      await expect(page.getByTestId('round-end-note')).toHaveText('已保存本轮完成的修改，可以继续写后续内容。');
      await assertNoInternalDeliveryText(page);
    } else {
      await expect(page.locator('[data-testid="chat-panel"], #chat-panel')).toContainText('AI 生成的内容没能保存到文件，请重试');
      await expect(page.locator('[data-testid="chat-panel"], #chat-panel')).toContainText('未确认保存《仍待确认的正文》');
    }
    await expect(page.getByRole('button', { name: '继续完成', exact: true })).toHaveCount(1);
  });
}

for (const visual of riskCases) {
  test(`real save failure stays visible live and after reload at ${visual.width}px ${visual.language} ${visual.theme}`, async ({ page }) => {
    await prepareMockedApp(page, visual);
    const tool = progressTool(visual.language);
    const atRiskTitle = visual.language === 'zh' ? '第九章 未确认稿' : 'Chapter Nine unconfirmed draft';
    let finished = false;
    let streamRequests = 0;
    let suggestRequests = 0;

    const history = {
      id: `risk-${visual.language}-${visual.width}`,
      session_id: 'risk-session',
      role: 'assistant',
      content: '',
      created_at: '2026-10-26T12:05:00Z',
      tool_calls: JSON.stringify([tool.history]),
      metadata: JSON.stringify({
        display_events: displayEvents(),
        stop_outcome: {
          reason: 'error',
          charged: true,
          saved_output: false,
          written_files: [],
          unwritten_files: [{ id: 'draft-8', title: atRiskTitle }],
          removed_files: [],
        },
      }),
    };

    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ json: finished ? [history] : [] }));
    await page.route('**/api/v1/agent/suggest', route => {
      suggestRequests += 1;
      return route.fulfill({ json: { suggestions: [staleSuggestion] } });
    });
    await page.route('**/api/v1/agent/stream', async route => {
      streamRequests += 1;
      finished = true;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([
          ['tool_call', { tool_use_id: tool.history.id, tool_name: tool.history.name, arguments: tool.history.arguments }],
          ['tool_result', { tool_use_id: tool.history.id, tool_name: tool.history.name, status: 'success', data: tool.history.result }],
          ['error', {
            code: 'ERR_AGENT_FILE_SAVE_FAILED',
            message: 'internal save exception',
            retryable: true,
            written_files: [],
            unwritten_files: [{ id: 'draft-8', title: atRiskTitle }],
          }],
        ]),
      });
    });

    await page.goto(`/project/${PROJECT_ID}`);
    await openChat(page, visual.width);
    await page.getByTestId('chat-input').fill(visual.language === 'zh' ? '保存这一章' : 'Save this chapter');
    await page.getByTestId('send-button').click();

    const errorCopy = visual.language === 'zh'
      ? 'AI 生成的内容没能保存到文件，请重试'
      : 'The AI’s content couldn’t be saved to the file. Please try again.';
    const notConfirmed = visual.language === 'zh'
      ? `未确认保存《${atRiskTitle}》`
      : `Save not confirmed: "${atRiskTitle}"`;
    const retryLabel = visual.language === 'zh' ? '重试' : 'Retry';
    const chat = page.locator('[data-testid="chat-panel"], #chat-panel');

    await expect(chat).toContainText(errorCopy);
    await expect(chat).toContainText(notConfirmed);
    await expect(page.getByText(tool.title, { exact: true })).toBeVisible();
    await expect(chat.getByRole('button', { name: retryLabel, exact: true })).toBeVisible();
    await expect(page.getByTestId('next-step-card')).toHaveCount(0);
    await assertNoInternalDeliveryText(page);
    await assertNoSuggestions(page, () => suggestRequests);
    expect(streamRequests).toBe(1);

    await page.screenshot({
      animations: 'disabled',
      path: test.info().outputPath(`risk-${visual.width}-${visual.language}-${visual.theme}-live.png`),
    });

    await page.reload();
    await openChat(page, visual.width);
    await expect(page.getByTestId('round-end-note')).toContainText(notConfirmed);
    await expect(page.getByText(tool.title, { exact: true })).toBeVisible();
    await expect(page.getByTestId('next-step-card')).toHaveCount(0);
    await assertNoInternalDeliveryText(page);
    await assertNoSuggestions(page, () => suggestRequests);
    expect(streamRequests).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
