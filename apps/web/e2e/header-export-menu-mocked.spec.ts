import { expect, test } from '@playwright/test';
import path from 'node:path';
import { mockResponsiveApp } from './fixtures-responsive';

for (const width of [390, 768]) for (const locale of ['zh', 'en']) {
test(`header export action keeps its icon and label intact at ${width}px ${locale}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1024 });
  await mockResponsiveApp(page);
  await page.addInitScript(locale => localStorage.setItem('zenstory-language', locale), locale);
  await page.goto('/project/responsive-project-0');

  if (width < 768) await page.locator('[aria-controls="header-mobile-menu"]').click();
  else await page.getByTestId('header-more-actions').click();
  const menu = width < 768 ? page.locator('#header-mobile-menu') : page.getByTestId('header-more-actions-menu');
  const exportItem = menu.getByRole('menuitem', { name: locale === 'zh' ? '导出大纲与正文' : 'Export outline & manuscript', exact: true });
  await expect(exportItem).toBeVisible();

  const geometry = await exportItem.evaluate((item) => {
    const icon = item.querySelector('svg');
    const label = item.querySelector('span') ?? Array.from(item.childNodes).find(node => node.nodeType === Node.TEXT_NODE && node.textContent?.trim());
    if (!icon || !label) throw new Error('Export action icon and label are required');

    const range = document.createRange();
    range.selectNodeContents(label);
    return {
      labelLines: range.getClientRects().length,
      iconWidth: icon.getBoundingClientRect().width,
      iconFlexShrink: getComputedStyle(icon).flexShrink,
      menuWidth: item.parentElement?.getBoundingClientRect().width ?? 0,
      viewportWidth: window.innerWidth,
    };
  });

  expect(geometry.labelLines).toBe(1);
  expect(geometry.iconWidth).toBe(width < 768 ? 18 : 16);
  expect(geometry.iconFlexShrink).toBe('0');
  expect(geometry.menuWidth).toBeLessThanOrEqual(geometry.viewportWidth - (width < 768 ? 0 : 16));
  expect(await page.locator('html').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
});
}

test('comparison capture header export menu', async ({ page }) => {
  const artifactDir = process.env.STATS_COMPARISON_DIR;
  test.skip(!artifactDir, 'Opt-in screenshot artifact for the before/after comparison');
  await page.setViewportSize({ width: 768, height: 1024 });
  await mockResponsiveApp(page);
  await page.goto('/project/responsive-project-0');
  await page.getByTestId('header-more-actions').click();
  const menu = page.getByTestId('header-more-actions-menu');
  await expect(menu.getByRole('menuitem', { name: /导出大纲/ })).toBeVisible();
  const name = process.env.STATS_COMPARISON_BASELINE === '1' ? 'before' : 'after';
  await menu.screenshot({ path: path.join(artifactDir!, `menu-${name}.png`) });
});
