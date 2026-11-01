import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

// A work that already holds the author's chapters (imported, no AI reply yet): the first chat
// offers manuscript requests, each button the full sentence it sends.
// See .agents/notes/implemented/feature/2026-10-31-manuscript-starter-cards.md.
async function manuscriptFixture(page: Page, language: 'zh' | 'en') {
  await mockResponsiveApp(page, null, 'free', language);
  const project = responsiveProjects[0];
  const streamed: { message: string; metadata: Record<string, unknown> }[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (data: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
    if (path.endsWith('/recent')) return json([]);
    if (path === '/api/v1/projects/progress') return json([{ project_id: project.id, written_units: 25, word_count: 60000, framework_ready: false }]);
    if (path.endsWith('/agent/suggest')) return json({ detail: 'UNEXPECTED_AUTOMATIC_AI' });
    if (path.includes('/agent/stream')) {
      streamed.push(request.postDataJSON());
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    }
    return route.fallback();
  });
  return { project, streamed };
}

for (const language of ['zh', 'en'] as const) {
  for (const width of [320, 390, 1440]) {
    test(`manuscript starter cards ${language} ${width}: fit the panel and send the read-only request as shown`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      const fixture = await manuscriptFixture(page, language);
      const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(`/project/${fixture.project.id}`);
      if (width < 768) await page.getByRole('tab', { name: 'AI', exact: true }).click();

      const cards = page.getByTestId('starter-cards');
      await expect(cards).toBeVisible();
      await expect(cards).toHaveAttribute('data-variant', 'manuscript');
      await expect(page.getByTestId('starter-send')).toHaveCount(0);

      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const panel = (await cards.boundingBox())!;
      for (const kind of ['review', 'characters', 'continue']) {
        const button = page.getByTestId(`starter-manuscript-${kind}`);
        const box = (await button.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(Math.min(width, panel.x + panel.width) + 0.5);
        if (width < 768) expect(box.height).toBeGreaterThanOrEqual(44);
        expect(await button.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      }
      await page.screenshot({ path: test.info().outputPath('manuscript-starter.png') });

      const review = page.getByTestId('starter-manuscript-review');
      const shown = (await review.textContent())!;
      await review.click();
      await expect.poll(() => fixture.streamed.length).toBe(1);
      expect(fixture.streamed[0].message).toBe(shown);
      expect(fixture.streamed[0].metadata.entry).toBe('starter_manuscript');
      expect(errors).toEqual([]);
    });
  }
}
