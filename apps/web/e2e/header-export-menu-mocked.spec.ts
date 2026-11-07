import { expect, test } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

test('tablet header offers the full-text export and no outline export', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await mockResponsiveApp(page);
  await page.addInitScript(() => localStorage.setItem('zenstory-language', 'zh'));
  await page.goto('/project/responsive-project-0');

  await expect(page.getByRole('button', { name: '导出整本正文（TXT）', exact: true })).toBeVisible();
  await page.getByTestId('header-more-actions').click();
  await expect(page.getByTestId('header-more-actions-menu')).not.toContainText('大纲');
});

for (const locale of ['zh', 'en']) {
test(`phone header export action keeps its icon and label intact in ${locale}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 1024 });
  await mockResponsiveApp(page);
  await page.addInitScript(locale => localStorage.setItem('zenstory-language', locale), locale);
  await page.goto('/project/responsive-project-0');

  await page.locator('[aria-controls="header-mobile-menu"]').click();
  const menu = page.locator('#header-mobile-menu');
  await expect(menu).not.toContainText(locale === 'zh' ? '大纲' : 'outline');
  const exportItem = menu.getByRole('menuitem', { name: locale === 'zh' ? '导出整本正文（TXT）' : 'Export whole work (TXT)', exact: true });
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
  expect(geometry.iconWidth).toBe(18);
  expect(geometry.iconFlexShrink).toBe('0');
  expect(geometry.menuWidth).toBeLessThanOrEqual(geometry.viewportWidth);
  expect(await page.locator('html').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
});
}
