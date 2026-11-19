import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

const tools = [1, 2].map(index => ({ id: `order-tool-${index}`, name: 'create_file', arguments: { title: `Order tool ${index}` }, status: 'success', result: { id: `order-file-${index}`, title: `Order tool ${index}`, file_type: 'draft' } }));
const liveDisplayEvents = [
  { type: 'content', content: 'Order first' },
  { type: 'tool_call', tool_call_index: 0 },
  { type: 'content', content: 'Order second' },
  { type: 'tool_call', tool_call_index: 1 },
  { type: 'content', content: 'Order third' },
];
const legacyDisplayEvents = [
  ...liveDisplayEvents.slice(0, 3),
  { type: 'handoff', data: { target_agent: 'writer', reason: 'handoff-order-marker' } },
  { type: 'agent_selected', data: { agent_type: 'order_marker_agent', agent_name: 'Writer order marker' } },
  ...liveDisplayEvents.slice(3),
];
const savedLive = { id: 'order-assistant', session_id: 'order-session', role: 'assistant', content: 'Order firstOrder secondOrder third', created_at: '2026-10-05T10:00:00Z', tool_calls: JSON.stringify(tools), metadata: JSON.stringify({ display_events: liveDisplayEvents }) };
const savedLegacy = { ...savedLive, id: 'legacy-order-assistant', metadata: JSON.stringify({ display_events: legacyDisplayEvents }) };

async function assertLiveOrder(page: Page) {
  const chat = page.locator('[data-testid="chat-panel"], #chat-panel');
  for (const text of ['Order first', 'Order tool 1', 'Order second', 'Order tool 2', 'Order third']) {
    await expect(chat.getByText(text, { exact: true })).toHaveCount(1);
  }
  await expect(chat.getByText('Writer order marker', { exact: true })).toHaveCount(0);
  await expect(chat.getByText(/handoff-order-marker/)).toHaveCount(0);
  const text = await chat.innerText();
  const markers = ['Order first', 'Order tool 1', 'Order second', 'Order tool 2', 'Order third'];
  const positions = markers.map(marker => text.indexOf(marker));
  expect(positions.every((position, index) => position >= 0 && (index === 0 || position > positions[index - 1]))).toBe(true);
}

async function assertLegacyOrder(page: Page) {
  const chat = page.locator('[data-testid="chat-panel"], #chat-panel');
  for (const text of ['Order first', 'Order tool 1', 'Order second', 'Writer order marker', 'Order tool 2', 'Order third']) {
    await expect(chat.getByText(text, { exact: true })).toHaveCount(1);
  }
  await expect(chat.getByText(/handoff-order-marker/)).toHaveCount(1);
  const text = await chat.innerText();
  const markers = ['Order first', 'Order tool 1', 'Order second', 'handoff-order-marker', 'Writer order marker', 'Order tool 2', 'Order third'];
  const positions = markers.map(marker => text.indexOf(marker));
  expect(positions.every((position, index) => position >= 0 && (index === 0 || position > positions[index - 1]))).toBe(true);
}

for (const width of [1280, 390]) {
  test(`completed and refreshed live history interleaves text and tools at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await mockResponsiveApp(page);
    let completed = false;
    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(completed ? [savedLive] : []) }));
    await page.route('**/api/v1/agent/stream', async route => {
      const events: Array<[string, unknown]> = [];
      for (const event of liveDisplayEvents) {
        if (event.type === 'content') events.push(['content_start', {}], ['content', { text: event.content }], ['content_end', {}]);
        else if (event.type === 'tool_call') {
          const tool = tools[event.tool_call_index!];
          events.push(['tool_call', { tool_use_id: tool.id, tool_name: tool.name, arguments: tool.arguments }], ['tool_result', { tool_use_id: tool.id, tool_name: tool.name, status: tool.status, data: tool.result }]);
        }
      }
      events.push(['done', { assistant_message_id: savedLive.id, session_id: savedLive.session_id }]);
      completed = true;
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('') });
    });
    await page.goto('/project/responsive-project-0');
    if (width < 768) await page.locator('#chat-tab').click();
    await page.getByTestId('chat-input').fill('Verify ordered history');
    await page.getByTestId('send-button').click();
    await expect(page.getByTestId('chat-input')).toBeEnabled();
    await assertLiveOrder(page);
    await page.reload();
    if (width < 768) await page.locator('#chat-tab').click();
    await assertLiveOrder(page);
  });

  test(`saved legacy history replays text, tools and agent handoffs at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await mockResponsiveApp(page);
    await page.route('**/api/v1/chat/session/*/recent**', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([savedLegacy]) }));
    await page.goto('/project/responsive-project-0');
    if (width < 768) await page.locator('#chat-tab').click();
    await assertLegacyOrder(page);
    await page.reload();
    if (width < 768) await page.locator('#chat-tab').click();
    await assertLegacyOrder(page);
  });
}
