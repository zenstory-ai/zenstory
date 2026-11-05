import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import type { MaterialNovel } from '../src/lib/materialsApi';
import { referenceCharacter, referenceMaterial } from '../src/test/materialReaderFixtures';
import { mockMaterialReaders } from './fixtures-material-readers';

const privateFilePath = '/private/uploads/responsive-user/original.txt';
const privateMd5 = '8db7f3c9d2a14a8da42652f50d8fb126';
const privateUnknown = 'internal-only-attribute';
const longSynopsis = `${referenceMaterial.synopsis}\n\n十年前离开的每个人，都在这次归乡里留下了不同版本的证词；沈砚必须把它们拼回同一段往事。`;

const overviewMaterial = {
  ...referenceMaterial,
  synopsis: longSynopsis,
  file_path: privateFilePath,
  md5: privateMd5,
  unknown_attribute: privateUnknown,
  source_meta: {
    ...referenceMaterial.source_meta,
    source_char_count: 72000,
    source_chapter_count: 42,
    file_path: privateFilePath,
    md5: privateMd5,
    unknown_attribute: privateUnknown,
  },
} as MaterialNovel;

async function setup(
  page: Page,
  options: { theme?: 'light' | 'dark'; material?: MaterialNovel } = {},
) {
  await mockMaterialReaders(page);
  await page.addInitScript(theme => localStorage.setItem('zenstory-theme', theme), options.theme ?? 'light');
  if (options.material) {
    await page.route('**/api/v1/materials/24', route => route.fulfill({ json: options.material }));
  }
}

function seriousAxeIssues(page: Page) {
  return new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze().then(result =>
    result.violations
      .filter(issue => ['serious', 'critical'].includes(issue.impact ?? ''))
      .map(issue => ({ id: issue.id, targets: issue.nodes.map(node => node.target) })),
  );
}

test('overview is complete, private-safe and usable across viewport, theme and language', async ({ browser, baseURL }) => {
  for (const width of [390, 768, 1440]) {
    for (const theme of ['light', 'dark'] as const) {
      for (const language of ['zh', 'en'] as const) {
        const context = await browser.newContext({ baseURL, viewport: { width, height: 900 } });
        const page = await context.newPage();
        try {
          await setup(page, { theme, material: overviewMaterial });
          await page.goto(`/materials/24?lang=${language}`);
          if (width < 768) {
            const overviewEntry = page.getByRole('button', { name: language === 'zh' ? '素材概览' : 'Material overview', exact: true });
            const bounds = (await overviewEntry.boundingBox())!;
            expect(bounds.width).toBeGreaterThanOrEqual(44);
            expect(bounds.height).toBeGreaterThanOrEqual(44);
            await overviewEntry.click();
          }

          const overview = page.getByRole('article', { name: language === 'zh' ? '素材概览' : 'Material overview', exact: true });
          await expect(overview).toContainText('林海');
          await expect(overview).toContainText('沈砚必须把它们拼回同一段往事');
          await expect(overview).toContainText('synthetic.txt');
          await expect(overview).toContainText('36,800');
          await expect(overview).toContainText('72,000');
          await expect(overview).toContainText(language === 'zh' ? '前 18/42 章' : 'first 18 of 42 chapters');
          await expect(overview.locator('.material-overview-index').getByRole('button')).toHaveCount(8);
          await expect(page.locator('body')).not.toContainText(privateFilePath);
          await expect(page.locator('body')).not.toContainText(privateMd5);
          await expect(page.locator('body')).not.toContainText(privateUnknown);
          expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
          if (theme === 'dark' && language === 'en') expect(await seriousAxeIssues(page)).toEqual([]);
        } finally {
          await context.close();
        }
      }
    }
  }
});

test('initial overview uses aggregate counts without entity reads and its index provides real keyboard navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const entityReads: string[] = [];
  page.on('request', request => {
    if (request.method() === 'GET' && /\/api\/v1\/materials\/24\/(tree|characters|stories|plots|storylines|relationships|goldenfingers|worldview)$/.test(new URL(request.url()).pathname)) {
      entityReads.push(new URL(request.url()).pathname);
    }
  });
  await setup(page);
  await page.goto('/materials/24');
  const overview = page.getByRole('article', { name: '素材概览', exact: true });
  const characterIndex = overview.locator('.material-overview-index button').filter({ hasText: '角色' });
  await expect(characterIndex).toContainText(/角色\s*·\s*1/);
  await expect(characterIndex).toHaveAttribute('aria-label', '打开角色目录，1 项');
  expect(entityReads).toEqual([]);

  const directory = page.getByRole('complementary', { name: '目录', exact: true });
  await directory.evaluate(element => { element.scrollTop = element.scrollHeight; });
  const chapterIndex = overview.locator('.material-overview-index button').filter({ hasText: '章节' });
  await chapterIndex.focus();
  await page.keyboard.press('Enter');
  const chapterFolder = directory.getByRole('button', { name: /^章节\s+1$/ });
  await expect(chapterFolder).toHaveAttribute('aria-expanded', 'true');
  await expect(chapterFolder).toBeFocused();
  await expect(chapterFolder).toBeInViewport();
  await expect.poll(() => entityReads).toContain('/api/v1/materials/24/tree');
});

test('phone overview round-trip preserves directory state, while category navigation reveals its target', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.goto('/materials/24');
  const directory = page.getByRole('complementary', { name: '目录', exact: true });
  const characterFolder = directory.getByRole('button', { name: /^角色\s+1$/ });
  await characterFolder.click();
  const search = page.getByRole('searchbox', { name: '搜索', exact: true });
  await search.fill('沈砚');
  await page.getByRole('button', { name: '素材概览', exact: true }).click();
  await page.getByRole('button', { name: '返回', exact: true }).click();
  await expect(search).toHaveValue('沈砚');
  await expect(characterFolder).toHaveAttribute('aria-expanded', 'true');
  await expect(directory.getByRole('button', { name: '沈砚', exact: true })).toBeVisible();

  await search.fill('不会命中的条件');
  await page.getByRole('button', { name: '素材概览', exact: true }).click();
  const category = page.getByRole('article', { name: '素材概览', exact: true }).locator('.material-overview-index button').filter({ hasText: '角色' });
  await category.focus();
  await page.keyboard.press('Enter');
  await expect(directory).toBeVisible();
  await expect(directory.getByRole('button', { name: '沈砚', exact: true })).toBeVisible();
  await expect(characterFolder).toBeFocused();
});

test('readable aliases, descriptions and themes are searchable; pending and failed reads never masquerade as no results', async ({ page }) => {
  await setup(page);
  let releaseFirst = () => {};
  let unavailable = true;
  let characterRequests = 0;
  const firstResponse = new Promise<void>(resolve => { releaseFirst = resolve; });
  const writes: string[] = [];
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
  await page.route('**/api/v1/materials/24/characters', async route => {
    characterRequests += 1;
    if (characterRequests === 1) await firstResponse;
    await route.fulfill({
      status: unavailable ? 503 : 200,
      json: unavailable ? { detail: 'Unavailable' } : [referenceCharacter],
    });
  });
  await page.goto('/materials/24');
  const directory = page.getByRole('complementary', { name: '目录', exact: true });
  const search = page.getByRole('searchbox', { name: '搜索', exact: true });
  await search.fill('没有任何命中');
  await expect.poll(() => characterRequests).toBe(1);
  await expect(directory).not.toContainText('没有找到匹配内容');
  releaseFirst();
  const characterError = directory.getByRole('alert').filter({ hasText: '加载失败' });
  await expect(characterError).toBeVisible({ timeout: 15_000 });
  await expect(directory).not.toContainText('没有找到匹配内容');
  unavailable = false;
  await characterError.getByRole('button', { name: '重试', exact: true }).click();
  await expect(directory).toContainText('没有找到匹配内容');
  expect(writes.filter(url => url.includes('/materials/'))).toEqual([]);

  await search.fill('守灯人');
  await expect(directory.getByRole('button', { name: '沈砚', exact: true })).toBeVisible();
  await search.fill('人物动机');
  await expect(directory.getByRole('button', { name: '沈砚', exact: true })).toBeVisible();
  await search.fill('真相与代价');
  await expect(directory.getByRole('button', { name: '归乡与旧信', exact: true })).toBeVisible();
  for (const privateValue of [privateFilePath, privateMd5, privateUnknown]) {
    await search.fill(privateValue);
    await expect(directory).toContainText('没有找到匹配内容');
  }
});

test('unknown, zero, disabled historical and successful empty counts keep distinct meanings', async ({ page }) => {
  const material = {
    ...overviewMaterial,
    chapters_count: undefined,
    characters_count: 0,
    stories_count: 0,
    story_lines_count: 1,
    enabled_stages: {
      chapter_summaries: true,
      plots: true,
      characters: true,
      meta: true,
      synopsis: true,
      stories: false,
      storylines: false,
      relationships: true,
    },
  } satisfies MaterialNovel;
  await setup(page, { material });
  await page.route('**/api/v1/materials/24/characters', route => route.fulfill({ json: [] }));
  await page.goto('/materials/24');
  const overview = page.getByRole('article', { name: '素材概览', exact: true });
  const index = overview.locator('.material-overview-index button');
  await expect(index.filter({ hasText: '章节' })).toContainText(/章节\s*·\s*—/);
  await expect(index.filter({ hasText: '角色' })).toContainText(/角色\s*·\s*0/);
  await expect(index.filter({ hasText: /^剧情/ })).toHaveCount(0);
  await expect(index.filter({ hasText: '故事线' })).toContainText(/故事线\s*·\s*1/);

  const directory = page.getByRole('complementary', { name: '目录', exact: true });
  await expect(directory.getByRole('button', { name: /^章节$/ })).toBeVisible();
  const emptyCharacters = directory.getByRole('button', { name: /^角色\s+0$/ });
  await emptyCharacters.click();
  await expect(directory).toContainText('暂无内容');
});
