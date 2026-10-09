import { test, expect, type Locator } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

// Dashboard button/type system (DESIGN.md "Dashboard buttons and type").
// Computed styles, because jsdom cannot see the unlayered `* { border-color }` rule in
// index.css that silently beats layered Tailwind border colours.

const borderTop = (locator: Locator) =>
  locator.evaluate((el) => {
    const style = getComputedStyle(el);
    return { color: style.borderTopColor, width: style.borderTopWidth };
  });

const isTransparent = (color: string) => color === 'transparent' || /rgba\(.*,\s*0\)$/.test(color);

test.describe('dashboard buttons and type', () => {
  test.use({ colorScheme: 'light' });

  test('solid and ghost buttons have no visible outline; secondary keeps its border', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockResponsiveApp(page, null, 'free');

    await page.goto('/dashboard/billing');
    const solid = page.getByRole('main').getByRole('button', { name: '开通 Pro' }).first();
    await expect(solid).toBeVisible();
    expect(isTransparent((await borderTop(solid)).color)).toBe(true);

    await page.goto('/dashboard/skills');
    const ghost = page.getByRole('button', { name: '导入' });
    await expect(ghost).toBeVisible();
    expect(isTransparent((await borderTop(ghost)).color)).toBe(true);

    await page.goto('/dashboard/materials');
    const secondary = page.getByTestId('materials-header-upgrade');
    await expect(secondary).toBeVisible();
    const secondaryBorder = await borderTop(secondary);
    expect(secondaryBorder.width).toBe('1px');
    expect(isTransparent(secondaryBorder.color)).toBe(false);
  });

  test('a skill card keeps a readable title on a 390px phone', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockResponsiveApp(page, null, 'free');

    await page.goto('/dashboard/skills');
    await page.getByRole('button', { name: '我的技能', exact: true }).click();
    const title = page.getByRole('heading', { name: '长标题技能：人物行动与剧情的因果关系' });
    await expect(title).toBeVisible();
    // Five 44px actions in the title row once left the name 8px wide.
    expect((await title.boundingBox())!.width).toBeGreaterThanOrEqual(160);
    const edit = page.getByRole('button', { name: '编辑技能' }).first();
    expect((await edit.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  });
});
