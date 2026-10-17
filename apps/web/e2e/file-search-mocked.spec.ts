import { test, expect } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

for (const width of [390, 1440]) {
  for (const action of ['select', 'clear']) {
  test(`file search preserves ranking, paths, limit and ${action} at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await mockResponsiveApp(page);
    const projectId = responsiveProjects[0].id;
    const suffixes = ['章节十', '章节二', 'Alpha', 'alpha', 'é', 'e\u0301', '😀', '!draft', 'file2', 'file10'];
    const titles = ['needle', ...Array.from({ length: 70 }, (_, index) => `needle ${suffixes[index % suffixes.length]} ${index}`), 'xneedle tail'];
    const files = titles.map((title, index) => ({
      id: `search-${index}`, project_id: projectId, title, content: `Local saved body ${index}.`,
      file_type: 'draft', parent_id: 'search-folder', order: index, metadata: null, file_metadata: null,
      created_at: '2026-10-17T00:00:00Z', updated_at: '2026-10-17T00:00:00Z',
    }));
    const origin = new URL(info.project.use.baseURL!).origin;
    const unexpected: string[] = [];
    let mockedSuggestions = 0;
    const fileReads: string[] = [];
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    // No live account, backend, model endpoint, fonts or telemetry is needed.
    await page.route('**/*', route => {
      const request = route.request();
      const url = new URL(request.url());
      // Dev builds call the API on its own origin (localhost:8000), so only
      // non-API requests are pinned to the page origin.
      if (!url.pathname.startsWith('/api/')) {
        return url.origin === origin ? route.fallback() : route.abort('blockedbyclient');
      }
      const json = (data: unknown) => route.fulfill({ json: data });
      if (request.method() === 'GET' && url.pathname.endsWith('/file-tree')) {
        return json({ tree: [{ id: 'search-folder', title: 'Drafts', file_type: 'folder', parent_id: null, order: 0, metadata: { folder_type: 'draft' }, children: files.map(file => ({ ...file, children: [] })) }] });
      }
      const id = url.pathname.match(/^\/api\/v1\/files\/([^/]+)$/)?.[1];
      if (request.method() === 'GET' && id) {
        fileReads.push(id);
        return json(files.find(file => file.id === id));
      }
      if (request.method() === 'POST' && url.pathname.endsWith('/stats/record')) return json({ success: true });
      if (request.method() === 'POST' && url.pathname === '/api/v1/agent/suggest') {
        mockedSuggestions++;
        return json({ suggestions: [] });
      }
      if (request.method() !== 'GET') {
        unexpected.push(`${request.method()} ${url.pathname}`);
        return route.abort('blockedbyclient');
      }
      return route.fallback();
    });

    await page.goto(`/project/${projectId}`);
    if (width < 768) await page.locator('#files-tab').click();
    const input = page.locator('[data-testid="file-search-input"]:visible');
    await expect(input).toBeEnabled();
    await input.fill('NEEDLE');
    const dropdown = page.getByTestId('search-results-dropdown');
    const limit = width < 768 ? 20 : 50;
    await expect(dropdown.getByRole('option')).toHaveCount(limit);
    // Use the original browser localeCompare as a same-runtime compatibility
    // oracle, including exact-before-prefix ranking and ICU-specific order.
    const expectedTitles = await page.evaluate(({ titles, limit }) => [...titles].sort((a, b) => {
      const score = (title: string) => title.toLowerCase() === 'needle' ? 3 : title.toLowerCase().startsWith('needle') ? 2 : 1;
      return score(b) - score(a) || a.localeCompare(b, 'zh-CN');
    }).slice(0, limit), { titles, limit });
    await expect(dropdown.getByRole('option').locator('.text-sm')).toHaveText(expectedTitles);
    await expect(dropdown.getByRole('option').locator('.text-xs')).toHaveText(Array(limit).fill('Drafts'));
    if (action === 'clear') {
      await input.fill('no-such-file');
      await expect(page.getByText('没有找到匹配内容', { exact: true })).toBeVisible();
      await page.locator('[data-testid="file-search-clear-button"]:visible').click();
      await expect(input).toHaveValue('');
      await expect(dropdown).toHaveCount(0);
    } else {
      await dropdown.getByRole('option').first().click();
      await expect(dropdown).toHaveCount(0);
      await expect(page.locator('[data-editor-scroll-container="true"] textarea')).toHaveValue(files[0].content);
      expect(fileReads).toContain(files[0].id);
    }
    expect(unexpected).toEqual([]);
    expect(errors).toEqual([]);
    await info.attach('offline-search-receipt', { body: JSON.stringify({ width, action, limit, expectedTitles, fileReads, mockedSuggestions, unexpected, errors }), contentType: 'application/json' });
  });
  }
}
