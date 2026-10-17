import { expect, test, type Page } from '@playwright/test';
import { mockMaterialReaders, referenceCases } from './fixtures-material-readers';
import { referenceRelationship, referenceCharacter } from '../src/test/materialReaderFixtures';

async function setup(page: Page, theme: 'light' | 'dark') {
  await mockMaterialReaders(page);
  await page.addInitScript(value => localStorage.setItem('zenstory-theme', value), theme);
}

for (const theme of ['light', 'dark'] as const) {
  for (const width of [390, 768, 1440]) {
    test(`all seven reference types preserve readable fields and navigation ${theme} ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [], writes: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => { if (request.method() === 'POST' && /\/api\//.test(request.url())) writes.push(request.url()); });
      await setup(page, theme);
      await page.goto('/materials/24');
      for (const item of referenceCases) {
        const directory = page.getByRole('complementary', { name: '目录', exact: true });
        const folder = directory.getByRole('button', { name: new RegExp('^' + item.folder) }).first();
        await folder.click();
        const leaf = directory.getByRole('button', { name: item.treeTitle, exact: true });
        await leaf.click();
        const article = page.getByRole('article', { name: item.title, exact: true });
        await expect(article.getByRole('heading', { level: 2, name: item.title, exact: true })).toBeVisible();
        await expect(article).toContainText(item.text);
        const typography = await article.locator('p').evaluateAll(paragraphs => {
          const p = paragraphs.find(element => element.textContent?.includes('\n'))!;
          const style = getComputedStyle(p);
          return { size: parseFloat(style.fontSize), leading: parseFloat(style.lineHeight) / parseFloat(style.fontSize), whitespace: style.whiteSpace, width: p.getBoundingClientRect().width };
        });
        expect(typography).toMatchObject({ size: 16, whitespace: 'pre-wrap' });
        expect(typography.leading).toBeGreaterThanOrEqual(1.8);
        expect(typography.width).toBeLessThanOrEqual(704);
        const headingFont = await article.getByRole('heading', { level: 2 }).evaluate(element => getComputedStyle(element).fontFamily);
        expect(headingFont).toContain('Plus Jakarta Sans');
        if (item.kind === 'character') {
          await expect(article).toContainText('主角');
          await expect(article).toContainText('阿砚、守灯人');
          await expect(article).toContainText('第 3 章');
          await expect(article.locator('strong')).toHaveText('人物动机');
        } else if (item.kind === 'story') {
          await expect(article).toContainText('第 1–12 章');
          await expect(article.getByRole('region', { name: '核心目标', exact: true })).toContainText('找到写信的人');
          await expect(article.getByRole('region', { name: '核心冲突', exact: true })).toContainText('保护故人与追索真相');
          await expect(article.getByRole('region', { name: '主题', exact: true })).toContainText('真相与代价');
        } else if (item.kind === 'plot') {
          await expect(article.getByRole('region', { name: '涉及角色', exact: true })).toContainText('温掌柜');
        } else if (item.kind === 'storyline') {
          await expect(article).toContainText('陆青禾');
          await expect(article.locator('dl')).toContainText('4');
        } else if (item.kind === 'relationship') {
          await expect(article.getByRole('region', { name: '情感倾向', exact: true })).toHaveText('情感倾向复杂');
          await expect(article).not.toContainText('complex');
        } else if (item.kind === 'goldenfinger') {
          const records = article.getByRole('region', { name: '进化历史', exact: true }).getByRole('listitem');
          await expect(records).toHaveCount(2);
          await expect(records.nth(0)).toContainText('初醒');
          await expect(records.nth(0)).toContainText('暴雨之夜');
          await expect(records.nth(1)).toContainText('辨声');
          await expect(records.nth(1)).toContainText('12');
        } else if (item.kind === 'worldview') {
          const factions = article.getByRole('region', { name: '关键势力', exact: true }).getByRole('listitem');
          await expect(factions).toHaveCount(2);
          await expect(factions.nth(0)).toContainText('旧灯铺');
          await expect(factions.nth(1)).toContainText('巡夜司');
          await expect(factions.nth(1)).toContainText('陆青禾');
          await expect(article.getByRole('region', { name: '力量体系', exact: true }).getByRole('listitem')).toHaveCount(3);
          await expect(article.getByRole('region', { name: '特殊规则', exact: true })).toContainText('同一段记忆只能');
        }
        await expect(article).not.toContainText('1003');
        expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
        if (width < 768) {
          const back = page.getByRole('button', { name: '返回', exact: true });
          expect((await back.boundingBox())!.height).toBeGreaterThanOrEqual(44);
          await back.click();
        }
        await expect(leaf).toHaveAttribute('aria-current', 'true');
        await expect(folder).toHaveAttribute('aria-expanded', 'true');
      }
      expect(errors).toEqual([]);
      expect(writes).toEqual([]);
    });
  }
}

for (const [sentimentValue, expected] of [['complex', 'Complex'], [' Complex ', 'Complex'], ['Trust, but verify.\nA fragile alliance.', 'Trust, but verify.\nA fragile alliance.']]) {
test(`English relationship sentiment: ${sentimentValue}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, 'dark');
  await page.route('**/api/v1/materials/24/relationships', route => route.fulfill({ json: [{ ...referenceRelationship, sentiment: sentimentValue }] }));
  await page.goto('/materials/24?lang=en');
  await page.getByRole('button', { name: /^Relationships/ }).click();
  await page.getByRole('button', { name: '沈砚 - 温掌柜', exact: true }).click();
  const sentiment = page.getByRole('region', { name: 'Sentiment', exact: true });
  await expect(sentiment).toContainText(expected);
  await expect(sentiment.locator('p')).toHaveCSS('white-space', 'pre-wrap');
  await expect(page.getByRole('region', { name: 'Relationship details', exact: true })).toContainText('温掌柜曾教沈砚修灯');
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

}

test('character role labels preserve unknown source descriptions', async ({ page }) => {
  await setup(page, 'light');
  await page.route('**/api/v1/materials/24/characters', route => route.fulfill({ json: [{ ...referenceCharacter, archetype: 'Reluctant hero' }] }));
  await page.goto('/materials/24?lang=en');
  await page.getByRole('button', { name: /^Characters/ }).click();
  await page.getByRole('button', { name: '沈砚', exact: true }).click();
  const article = page.getByRole('article', { name: '沈砚', exact: true });
  await expect(article.locator('dl')).toContainText('Character roleReluctant hero');
});

test('reference Markdown preserves soft newlines, code and safe rendering with long content', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, 'light');
  const line = '第一行内容。', next = '第二行必须换行。';
  const name = '非常长的人物姓名'.repeat(8);
  await page.route('**/api/v1/materials/24/characters', route => route.fulfill({ json: [{ ...referenceCharacter, name, aliases: ['Alias'.repeat(40)], description: `${line}\n${next}\n\n- 列表一\n- 列表二\n\n\`\`\`text\n第一行\n第二行\n\`\`\`\n\n<script>window.materialUnsafe=true</script>` }] }));
  await page.goto('/materials/24');
  await page.getByRole('button', { name: /^角色/ }).click();
  await page.getByRole('button', { name, exact: true }).click();
  const article = page.getByRole('article', { name, exact: true });
  const p = article.locator('p').filter({ hasText: line });
  await expect(p).toHaveText(`${line}\n${next}`);
  const newLineDistance = await p.evaluate(element => {
    const text = element.firstChild!;
    const y = (offset: number) => { const range = document.createRange(); range.setStart(text, offset); range.setEnd(text, offset + 1); return [...range.getClientRects()].find(rect => rect.width > 0)!.y; };
    const newline = text.textContent!.indexOf('\n');
    return y(newline + 1) - y(newline - 1);
  });
  expect(newLineDistance).toBeGreaterThanOrEqual(28);
  await expect(article.getByRole('listitem')).toHaveCount(2);
  await expect(article.locator('pre code')).toHaveCSS('white-space', 'pre');
  expect(await page.evaluate(() => 'materialUnsafe' in window)).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});
