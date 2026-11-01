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
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('从一页故事，走进一个世界');
      await expect(page.locator('.source-leaf blockquote')).toBeVisible();
      await expect(page.locator('.home-system svg')).toBeVisible();
      await expect(page.locator('.connection-details')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const outcome = await page.locator('.tile-media').boundingBox();
      expect(outcome!.y).toBeLessThan(width === 1440 ? 900 : 844);
      if (width === 1440) {
        await expect(page.locator('.showcase-controls')).toBeHidden();
      } else {
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

test('expanded case evidence and learning references keep accessible heading order', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/zh/');
  await page.locator('.stage-evidence summary').click();
  for (const summary of await page.locator('.knowledge-level summary').all()) await summary.click();
  const audit = await new AxeBuilder({ page }).analyze();
  expect(audit.violations.filter(v => v.id === 'heading-order' || ['critical', 'serious'].includes(v.impact ?? '')).map(v => v.id)).toEqual([]);
});

test.describe('Without JavaScript', () => {
  test.use({ javaScriptEnabled: false });
test('English home remains readable without JavaScript and keeps native video controls', async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(baseURL + '/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('From one page, into a world.');
  await expect(page.locator('.showcase-controls')).toBeHidden();
  await expect(page.locator('.case-card')).toHaveCount(4);
  for (const video of await page.locator('video').all()) {
    await expect(video).toHaveAttribute('controls', '');
    await expect(video).toHaveAttribute('preload', 'none');
    await expect(video).not.toHaveAttribute('autoplay');
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  // Native disclosures are operable even with no scripts: sources and learning links stay usable.
  const evidence = page.locator('.stage-evidence');
  await evidence.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(evidence).toHaveAttribute('open', '');
  await expect(evidence.locator('blockquote')).toBeVisible();
  await page.keyboard.press('Space');
  await expect(evidence).not.toHaveAttribute('open');
  const level = page.locator('.knowledge-level').first();
  await expect(level.locator('a').first()).toBeHidden();
  await level.locator('summary').focus();
  await page.keyboard.press('Enter');
  await expect(level.locator('a').first()).toBeVisible();

  await expect(page.locator('.home-system svg')).toBeVisible();

});

});
