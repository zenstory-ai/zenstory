import { test, expect } from './fixtures/static-site';
import AxeBuilder from '@axe-core/playwright';

// Static organization build: no login, API, paid models or payment requests.
test.beforeEach(async ({ page }) => {
  await page.route('https://fonts.googleapis.com/**', route => route.fulfill({ contentType: 'text/css', body: '' }));
  await page.route('https://fonts.gstatic.com/**', route => route.abort());
});

for (const colorScheme of ['light', 'dark'] as const) {
  for (const width of [390, 1440]) {
    test(`Chinese home reads cleanly at ${width}px in ${colorScheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await page.goto('/zh/');
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('写下故事，做出作品');
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const stage = page.locator('.stage');
      if (width === 1440) {
        expect((await stage.boundingBox())!.y + (await stage.boundingBox())!.height).toBeLessThanOrEqual(900);
        await expect(page.locator('.showcase-controls')).toBeHidden();
      } else {
        const game = await page.locator('.tile-media').boundingBox();
        expect(game!.y + game!.height).toBeLessThanOrEqual(844);
        for (const control of ['.tile-play', '.lang-switch a', '.nav-app', '.library-search button']) {
          for (const box of await page.locator(control).evaluateAll(elements => elements.map(el => el.getBoundingClientRect().height))) {
            expect(box, control).toBeGreaterThanOrEqual(44);
          }
        }
      }
      const audit = await new AxeBuilder({ page }).analyze();
      expect(audit.violations.filter(v => ['critical', 'serious'].includes(v.impact ?? '')).map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`home-${colorScheme}-${width}.png`), fullPage: true });
    });
  }
}

test('mobile carousel tracks button navigation, native scrolling and its ends', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/zh/');
  const previous = page.getByRole('button', { name: '前一个演示' });
  const next = page.getByRole('button', { name: '下一个演示' });
  const position = page.locator('.showcase-position');
  await expect(position).toHaveText('1 / 4');
  await expect(previous).toBeDisabled();
  await next.click();
  await expect(position).toHaveText('2 / 4');
  await previous.click();
  await expect(position).toHaveText('1 / 4');
  // Native touch/trackpad scrolling is independent of the navigation buttons.
  await page.locator('.showcase-grid').evaluate(el => el.scrollTo({ left: el.scrollWidth, behavior: 'instant' }));
  await expect(position).toHaveText('4 / 4');
  await expect(next).toBeDisabled();
  await previous.click();
  await expect(position).toHaveText('3 / 4');
  const grid = page.locator('.showcase-grid');
  const third = page.locator('.case-card').nth(2);
  const gutter = await grid.evaluate(el => parseFloat(getComputedStyle(el).paddingLeft));
  expect(Math.abs((await third.boundingBox())!.x - ((await grid.boundingBox())!.x + gutter))).toBeLessThan(2);
});

test.describe('Without JavaScript', () => {
  test.use({ javaScriptEnabled: false });
test('English home remains readable without JavaScript and keeps native video controls', async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  // The finalized directory is served without Vercel's apex -> /org-home rewrite locally.
  await page.goto(baseURL + '/org-home/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Write stories. Make them real.');
  await expect(page.locator('.showcase-controls')).toBeHidden();
  await expect(page.locator('.case-card')).toHaveCount(4);
  for (const video of await page.locator('video').all()) {
    await expect(video).toHaveAttribute('controls', '');
    await expect(video).toHaveAttribute('preload', 'none');
    await expect(video).not.toHaveAttribute('autoplay');
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

});

});
