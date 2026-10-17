import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

const first = '雨停的时候，沈砚才推开那扇旧木门。院里积着薄薄一层水，倒映出檐角最后一点天光。';
const dialogue = '“你终于来了。”老人没有回头，只把桌上的信往前推了推。';
const content = Array.from({ length: 20 }, () => `${first}\n${dialogue}`).join('\n\n')
  + '\n\n### 场景笔记\n\n- 仍保留 Markdown 列表\n\n```text\n第一行\n第二行\n```';

async function setup(page: Page, theme: 'light' | 'dark') {
  await mockResponsiveApp(page, null, 'pro');
  await page.addInitScript(value => localStorage.setItem('zenstory-theme', value), theme);
  await page.route('**/api/v1/materials/*/chapters/*', route => route.fulfill({ json: {
    id: 'material-chapter-0', title: '第一章 雨后的来信', chapter_number: 1,
    word_count: 1280, summary: '沈砚回到故乡，收到一封迟到了十年的信。', content,
  } }));
}

for (const theme of ['light', 'dark'] as const) {
  for (const width of [390, 768, 1280, 1920]) {
    test(`manuscript newlines, navigation and reading measure ${theme} ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      await setup(page, theme);
      await page.goto('/materials/responsive-material');
      const folder = page.getByRole('button', { name: /^章节/ });
      await expect(folder).toHaveAttribute('aria-expanded', 'false');
      await folder.click();
      await expect(folder).toHaveAttribute('aria-expanded', 'true');
      const search = page.getByRole('searchbox', { name: '搜索', exact: true });
      if (width < 768) await search.fill('第1章');
      const chapter = page.getByRole('button', { name: '第1章 很长的标题与人物选择', exact: true });
      await chapter.click();
      const manuscript = page.locator('.material-reader-manuscript');
      await expect(manuscript).toBeVisible();
      const paragraph = manuscript.locator('p').first();
      await expect(paragraph).toHaveCSS('white-space', 'pre-wrap');
      expect(await paragraph.textContent()).toBe(`${first}\n${dialogue}`);
      const typography = await paragraph.evaluate(element => {
        const style = getComputedStyle(element);
        const text = element.firstChild!;
        const value = text.textContent!;
        const y = (offset: number) => {
          const range = document.createRange();
          range.setStart(text, offset); range.setEnd(text, offset + 1);
          // WebKit includes a zero-width previous-line rect for opening quotes.
          // Measure the painted glyph rather than that union's top edge.
          return [...range.getClientRects()].find(rect => rect.width > 0)!.y;
        };
        const newline = value.indexOf('\n');
        return {
          font: style.fontFamily, size: parseFloat(style.fontSize),
          leading: parseFloat(style.lineHeight) / parseFloat(style.fontSize),
          nextLine: y(newline + 1) - y(newline - 1), width: element.getBoundingClientRect().width,
        };
      });
      expect(typography.font).toContain('Source Serif 4');
      expect(typography.size).toBeGreaterThanOrEqual(16);
      expect(typography.leading).toBeGreaterThanOrEqual(1.8);
      expect(typography.nextLine).toBeGreaterThanOrEqual(typography.size * 1.8);
      expect(typography.width).toBeLessThanOrEqual(704);
      await expect(manuscript.getByRole('listitem')).toHaveCount(1);
      await expect(manuscript.locator('pre code')).toHaveCSS('white-space', 'pre');
      const reader = page.getByRole('region', { name: '素材内容', exact: true });
      if (width < 768) {
        const back = page.getByRole('button', { name: '返回', exact: true });
        expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
        await expect(search).toHaveCount(0);
        await back.click();
        await expect(search).toHaveValue('第1章');
        await expect(folder).toHaveAttribute('aria-expanded', 'true');
        await expect(chapter).toHaveAttribute('aria-current', 'true');
      } else {
        const directory = page.getByRole('complementary', { name: '目录', exact: true });
        await directory.evaluate(element => { element.scrollTop = 100; });
        await reader.evaluate(element => { element.scrollTop = 300; });
        expect(await directory.evaluate(element => element.scrollTop)).toBe(100);
        expect(await reader.evaluate(element => element.scrollTop)).toBe(300);
        expect(await page.evaluate(() => scrollY)).toBe(0);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      expect(errors).toEqual([]);
    });
  }
}

test('chapter read retry is independent of paid decomposition retry', async ({ page }) => {
  await setup(page, 'light');
  let unavailable = true;
  const writes: string[] = [];
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
  await page.route('**/api/v1/materials/*/chapters/*', route => route.fulfill({
    status: unavailable ? 503 : 200,
    json: unavailable ? { detail: 'Unavailable' } : { id: 'material-chapter-0', title: 'Recovered chapter', content: '正文恢复。' },
  }));
  await page.goto('/materials/responsive-material');
  await page.getByRole('button', { name: /^章节/ }).click();
  await page.getByRole('button', { name: '第1章 很长的标题与人物选择', exact: true }).click();
  const error = page.getByRole('alert');
  await expect(error).toContainText('素材详情加载失败', { timeout: 15000 });
  unavailable = false;
  await error.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.locator('.material-reader-manuscript')).toHaveText('正文恢复。');
  expect(writes.filter(url => /\/materials\//.test(url))).toEqual([]);
});

test('English contents and controls fit a small screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, 'dark');
  await page.goto('/materials/responsive-material?lang=en');
  await expect(page.getByRole('complementary', { name: 'Contents', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^Chapters/ }).click();
  await page.getByRole('button', { name: '第1章 很长的标题与人物选择', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Material content', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page.getByRole('searchbox', { name: 'Search', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test('a phone folder error has a real touch-size read retry', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, 'light');
  let unavailable = true;
  await page.route('**/api/v1/materials/*/characters', route => route.fulfill({
    status: unavailable ? 503 : 200,
    json: unavailable ? { detail: 'Unavailable' } : [{ id: 'recovered-character', name: '重试后角色' }],
  }));
  await page.goto('/materials/responsive-material');
  await page.getByRole('button', { name: /^角色/ }).click();
  const error = page.getByRole('alert');
  await expect(error).toContainText('加载失败', { timeout: 15000 });
  const retry = error.getByRole('button', { name: '重试', exact: true });
  const bounds = (await retry.boundingBox())!;
  expect(bounds.width).toBeGreaterThanOrEqual(44);
  expect(bounds.height).toBeGreaterThanOrEqual(44);
  unavailable = false;
  await retry.click();
  await expect(page.getByRole('button', { name: '重试后角色', exact: true })).toBeVisible();
});
