import { test, expect, type Page, type Route } from '@playwright/test';
import { createOwnedProject, createOwnedFile, cleanupOwnedProject, type OwnedProject } from './fixtures/project-test-data';

const ENABLED = process.env.E2E_ENABLE_ERROR_RECOVERY_E2E === 'true';
const STREAM = '**/api/v1/agent/stream';
const responseText = '重试后，这一段已经完整写好。';
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const completed = frame('content', { text: responseText }) + frame('done', {});
const fulfillStream = (route: Route, body: string) => route.fulfill({ contentType: 'text/event-stream', body });
const retryButton = (page: Page) => page.getByTestId('chat-panel').getByRole('button', { name: '重试', exact: true });
const streamError = (page: Page) => page.getByTestId('chat-panel').getByText('这次生成中断了', { exact: true });

// Faults are injected at the browser boundary. No test retries generation on its
// own, and no unmatched request may reach a paid provider (CI backend egress guard).
test.describe('Error Recovery', () => {
  test.skip(!ENABLED, 'Error recovery requires E2E_ENABLE_ERROR_RECOVERY_E2E=true.');
  let project: OwnedProject | undefined;
  const savedDraft = '雨停以后，书铺门前只剩下一盏灯。';

  test.beforeEach(async ({ page }, testInfo) => {
    project = await createOwnedProject(page, testInfo, 'Recovery');
    const file = await createOwnedFile(page, project, { title: '恢复测试', content: savedDraft });
    await page.route(STREAM, route => fulfillStream(route, completed));
    await page.goto(`/project/${project.id}?file=${file.id}`);
    await expect(page.locator('[data-editor-scroll-container] textarea').first()).toHaveValue(savedDraft);
    await expect(page.getByTestId('chat-input')).toBeEnabled();
  });

  test.afterEach(async ({ page, context }) => {
    await context.setOffline(false);
    await cleanupOwnedProject(page, project);
    project = undefined;
  });

  async function send(page: Page, text = '继续写这一段') {
    await page.getByTestId('chat-input').fill(text);
    await page.getByTestId('send-button').click();
  }

  async function expectReadyToContinue(page: Page) {
    await page.getByTestId('chat-input').fill('接下来继续写');
    await expect(page.getByTestId('send-button')).toBeEnabled();
  }

  async function expectDraftIntact(page: Page) {
    await expect(page.locator('[data-editor-scroll-container] textarea').first()).toHaveValue(savedDraft);
  }

  test('network interruption during AI stream', async ({ page }) => {
    // A readable stream fails only after the UI has rendered its first chunk.
    // This exercises reader.read() rejection, rather than failing the initial fetch.
    await page.evaluate(() => {
      const originalFetch = window.fetch.bind(window);
      let calls = 0;
      window.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!url.endsWith('/api/v1/agent/stream') || ++calls > 1) return originalFetch(input, init);
        return new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('event: content\ndata: {"text":"已经收到的半段正文"}\n\n'));
            window.addEventListener('e2e-disconnect', () => controller.error(new TypeError('Network connection lost')), { once: true });
          },
        }), { headers: { 'Content-Type': 'text/event-stream' } });
      };
    });
    await send(page);
    await expect(page.getByTestId('chat-panel').getByText('已经收到的半段正文', { exact: true })).toBeVisible();
    await page.evaluate(() => window.dispatchEvent(new Event('e2e-disconnect')));
    await expect(streamError(page)).toBeVisible();
    await expect(retryButton(page)).toBeVisible();
    await expectReadyToContinue(page);
    await expectDraftIntact(page);
    await retryButton(page).click();
    await expect(page.getByTestId('chat-panel').getByText(responseText, { exact: true })).toBeVisible();
  });

  test('backend restart handling (503 errors)', async ({ page }) => {
    let attempts = 0;
    await page.route(STREAM, route => {
      attempts++;
      return route.fulfill({ status: 503, json: { detail: '服务暂不可用，请稍后再试' } });
    });
    await send(page);
    await expect(streamError(page)).toBeVisible();
    await expect(page.getByText('服务暂不可用，请稍后再试', { exact: true })).toBeVisible();
    await expect(retryButton(page)).not.toBeVisible();
    await expectReadyToContinue(page);
    await expectDraftIntact(page);
    expect(attempts).toBe(1);
  });

  test('quota exceeded error display', async ({ page }) => {
    let attempts = 0;
    await page.route(STREAM, route => {
      attempts++;
      return route.fulfill({ status: 429, json: { error_code: 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED', detail: '今天的免费 AI 消息用完了' } });
    });
    await send(page);
    await expect(page.getByTestId('chat-panel').getByText(/今天的.*AI 消息.*用完/).first()).toBeVisible();
    await expect(page.getByTestId('chat-input')).toBeEnabled();
    await expect(retryButton(page)).not.toBeVisible();
    await expectDraftIntact(page);
    expect(attempts).toBe(1);
  });

  test('failed API call supports explicit retry without automatic backoff', async ({ page }) => {
    let attempts = 0;
    let recovering = false;
    await page.route('**/api/v1/projects', route => {
      attempts++;
      return recovering ? route.continue() : route.fulfill({ status: 503, json: { detail: '暂时不可用' } });
    });
    await page.reload();
    await expect(page.getByText('加载项目失败', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '重试', exact: true })).toBeVisible();
    const beforeRetry = attempts;
    recovering = true;
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await expectDraftIntact(page);
    expect(attempts).toBe(beforeRetry + 1);
  });

  test('invalid response handling', async ({ page }) => {
    await page.route('**/api/v1/projects', route => route.fulfill({ contentType: 'application/json', body: 'Invalid JSON{{' }));
    await page.reload();
    await expect(page.getByText('加载项目失败', { exact: true })).toBeVisible();
    await page.unroute('**/api/v1/projects');
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await expectDraftIntact(page);
  });

  test('timeout error handling', async ({ page }) => {
    // Reproduce the server's typed timeout terminal frame, not a 65-second
    // sleep beyond the test timeout. A completed timeout must not auto-replay.
    let attempts = 0;
    await page.route(STREAM, route => {
      attempts++;
      return fulfillStream(route, frame('error', { code: 'ERR_AGENT_RUN_TIMEOUT', message: '这一轮写得太久，先停下了。回复「继续」就能接着写。', retryable: false }));
    });
    await send(page);
    await expect(page.getByText('这一轮写得太久，先停下了。回复「继续」就能接着写。', { exact: true })).toBeVisible();
    await expect(retryButton(page)).not.toBeVisible();
    await expectReadyToContinue(page);
    await expectDraftIntact(page);
    expect(attempts).toBe(1);
  });

  test('authentication error redirects to login', async ({ page }) => {
    await page.route('**/api/v1/**', route => route.fulfill({ status: 401, json: { detail: 'Token expired' } }));
    await page.reload();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('#identifier')).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull();
  });

  test('network error with retry button', async ({ page }) => {
    let attempts = 0;
    await page.route(STREAM, route => ++attempts === 1 ? route.abort('failed') : fulfillStream(route, completed));
    await send(page);
    await expect(streamError(page)).toBeVisible();
    await expect(retryButton(page)).toBeVisible();
    expect(attempts).toBe(1);
    await retryButton(page).click();
    await expect(page.getByTestId('chat-panel').getByText(responseText, { exact: true })).toBeVisible();
    await expect(streamError(page)).not.toBeVisible();
    await expectDraftIntact(page);
    expect(attempts).toBe(2);
  });

  test('partial response handling', async ({ page }) => {
    await page.route(STREAM, route => fulfillStream(route, 'event: content\ndata: {"text":"unfinished'));
    await send(page);
    await expect(streamError(page)).toBeVisible();
    await expect(page.getByText('网络中断，请重试', { exact: true })).toBeVisible();
    await expect(retryButton(page)).toBeVisible();
    await expectDraftIntact(page);
  });

  test('SSE stream interruption', async ({ page }) => {
    await page.route(STREAM, route => fulfillStream(route, frame('content', { text: '连接关闭前收到的正文' })));
    await send(page);
    await expect(page.getByTestId('chat-panel').getByText('连接关闭前收到的正文', { exact: true })).toBeVisible();
    await expect(streamError(page)).toBeVisible();
    await expect(page.getByText('网络中断，请重试', { exact: true })).toBeVisible();
    await expect(retryButton(page)).toBeVisible();
    await expectReadyToContinue(page);
    await expectDraftIntact(page);
  });

  test('malformed SSE event handling', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route(STREAM, route => fulfillStream(route, 'event: content\ndata: invalid-json{{\n\n' + completed));
    await send(page);
    // The parser deliberately preserves non-JSON content frames as text.
    await expect(page.getByTestId('message-list')).toContainText(responseText);
    await expect(page.getByTestId('message-list')).toContainText('invalid-json{{');
    await expect(streamError(page)).not.toBeVisible();
    await expectReadyToContinue(page);
    await expectDraftIntact(page);
    expect(pageErrors).toEqual([]);
  });
});
