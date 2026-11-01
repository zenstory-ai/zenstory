import { test, expect } from './fixtures/static-site';
import AxeBuilder from '@axe-core/playwright';
import { readFileSync } from 'node:fs';

const projects = JSON.parse(readFileSync(new URL('../content/projects.json', import.meta.url), 'utf8')) as { slug: string; stars: number }[];
const org = JSON.parse(readFileSync(new URL('../content/org.json', import.meta.url), 'utf8')) as { proof: { as_of: string } };
const layers = ['get-started', 'learning-paths', 'browse-topics', 'revise-and-look-up'];

test.beforeEach(async ({ page }) => {
  await page.route('https://fonts.googleapis.com/**', route => route.fulfill({ contentType: 'text/css', body: '' }));
  await page.route('https://fonts.gstatic.com/**', route => route.abort());
});

for (const lang of ['en', 'zh']) for (const route of ['projects', 'guides']) {
  for (const colorScheme of ['light', 'dark'] as const) for (const width of [390, 1440]) {
    test(`${lang} ${route} ${width} ${colorScheme} keeps a readable open index`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      const prefix = lang === 'zh' ? '/zh' : '';
      await page.goto(`${prefix}/${route}`);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expect(page.locator('body')).toHaveAttribute('data-page', route);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      for (const control of ['.lang-switch a', '.nav-app']) {
        for (const height of await page.locator(control).evaluateAll(els => els.map(el => el.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44);
      }
      if (route === 'projects') {
        await expect(page.locator('.project-entry')).toHaveCount(6);
        await expect(page.locator('.star-snapshot')).toHaveCount(1);
        await expect(page.locator('.star-snapshot')).toContainText(org.proof.as_of);
        for (const project of projects) {
          const entry = page.locator('.project-entry').filter({ has: page.locator(`h3 a[href="${prefix}/${project.slug}"]`) });
          await expect(entry).toContainText(project.stars.toLocaleString('en-US'));
        }
        await expect(page.locator('.project-relations > div')).toHaveCount(3);
        await expect(page.locator('.project-hosts li')).toHaveCount(9);
        for (const height of await page.locator('.project-actions a').evaluateAll(els => els.map(el => el.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44);
      } else {
        await expect(page.locator('.layer-jump a')).toHaveCount(4);
        await expect(page.locator('.layer-step')).toHaveCount(0);
        await expect(page.locator('.learning-path')).toHaveCount(4);
        await expect(page.locator('.learning-path li')).toHaveCount(16);
        await expect(page.locator('.task-index li')).toHaveCount(10);
        for (const selector of ['.layer-jump a', '.task-index a', '.term-row a']) {
          const rows = await page.locator(selector).evaluateAll(els => els.map(el => {
            const style = getComputedStyle(el);
            return { height: el.getBoundingClientRect().height, border: style.borderRadius, background: style.backgroundColor };
          }));
          for (const row of rows) {
            expect(row.height, selector).toBeGreaterThanOrEqual(44);
            expect(row.border, `${selector}: no pill/card frames`).toBe('0px');
            expect(row.background, `${selector}: preserve the paper surface`).toBe('rgba(0, 0, 0, 0)');
          }
        }
      }
      const audit = await new AxeBuilder({ page }).analyze();
      expect(audit.violations.filter(v => ['serious', 'critical'].includes(v.impact ?? '')).map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) }))).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`${lang}-${route}-${width}-${colorScheme}.png`), fullPage: true });
    });
  }
}

for (const lang of ['en', 'zh']) {
  test(`${lang} guides search, query refresh, reset and empty state remain usable`, async ({ page }) => {
    const prefix = lang === 'zh' ? '/zh' : '';
    const query = lang === 'zh' ? '对白' : 'dialogue';
    await page.goto(`${prefix}/guides?q=${encodeURIComponent(query)}`);
    const input = page.locator('#guide-query');
    await expect(input).toHaveValue(query);
    const results = page.locator('[data-search-results] li:visible');
    await expect(results.first()).toBeVisible();
    await expect(page.locator('[data-library-browse]').first()).toBeHidden();
    const firstHref = await results.first().locator('a').first().getAttribute('href');
    await page.reload();
    await expect(results.first().locator('a').first()).toHaveAttribute('href', firstHref!);
    await input.fill('zzzz-nonexistent-creative-method-92734');
    await expect(results).toHaveCount(0);
    await expect(page.locator('[data-search-status]')).not.toBeEmpty();
    await expect(page.locator('#get-started')).toBeVisible();
    await page.locator('.guide-search button[type="reset"]').click();
    await expect(input).toHaveValue('');
    expect(new URL(page.url()).searchParams.has('q')).toBe(false);
    for (const id of layers) {
      await page.locator(`.layer-jump a[href="#${id}"]`).click();
      const box = await page.locator(`#${id}`).boundingBox();
      const header = await page.locator('header.top').boundingBox();
      expect(box!.y).toBeGreaterThanOrEqual(header!.y + header!.height);
    }
    await input.fill(query);
    await input.focus();
    await page.keyboard.press('Tab');
    await expect(page.locator('.guide-search button[type="reset"]')).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(input).toHaveValue('');
  });
}

test.describe('without JavaScript', () => {
  test.use({ javaScriptEnabled: false });
  for (const lang of ['en', 'zh']) test(`${lang} indexes expose their complete catalog and learning routes without scripts`, async ({ page }) => {
    const prefix = lang === 'zh' ? '/zh' : '';
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${prefix}/projects`);
    await expect(page.locator('.project-entry')).toHaveCount(6);
    await expect(page.locator('.star-snapshot')).toContainText(org.proof.as_of);
    await page.goto(`${prefix}/guides`);
    for (const id of layers) await expect(page.locator(`#${id}`)).toBeVisible();
    await expect(page.locator('.learning-path li')).toHaveCount(16);
    await expect(page.locator('.task-index a')).toHaveCount(10);
    await expect(page.locator('.guide-search')).toBeHidden();
    const link = page.locator('.layer-jump a').first();
    await link.focus();
    expect(await link.evaluate(el => el.matches(':focus-visible'))).toBe(true);
    expect(await link.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth))).toBeGreaterThanOrEqual(2);
  });
});
