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
    // Online checkout explicitly off (a failed options request would count as unknown),
    // so 「兑换码」 is the page's one solid action. Routes added later take precedence.
    await page.route('**/api/v1/payments/options', (route) =>
      route.fulfill({ json: { enabled: false, payment_methods: [] } }));

    await page.goto('/dashboard/billing');
    const solid = page.getByRole('main').getByRole('button', { name: '兑换码', exact: true });
    await expect(solid).toBeVisible();
    await expect(solid).toHaveClass(/bg-\[hsl\(var\(--accent-primary\)\)\]/);
    // It turns primary once the payment options arrive, and `transition-all` animates the border.
    await expect.poll(async () => isTransparent((await borderTop(solid)).color)).toBe(true);

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

  test('section headings are sans, but a heading that asks for the display font keeps it', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockResponsiveApp(page, null, 'free');
    await page.goto('/dashboard/skills');
    await expect(page.getByRole('button', { name: '导入' })).toBeVisible();

    // Inject probes rather than rely on one page's markup: the rule is global CSS.
    const fonts = await page.evaluate(() => {
      const main = document.querySelector('main')!;
      const probe = (parent: Element, html: string) => {
        const box = document.createElement('div');
        box.innerHTML = html;
        parent.appendChild(box);
        const font = getComputedStyle(box.querySelector('h2,h3')!).fontFamily;
        box.remove();
        return font;
      };
      return {
        plainInMain: probe(main, '<h3>技能</h3>'),
        displayInMain: probe(main, '<h2 class="heading-display">灵感</h2>'),
        proseInMain: probe(main, '<div class="prose"><h2>第一章</h2></div>'),
        // Outside a control surface the serif default must still yield to a utility.
        utilityOutside: probe(document.body, '<h2 class="font-sans">标题</h2>'),
      };
    });
    expect(fonts.plainInMain).toMatch(/^"?Plus Jakarta Sans/);
    expect(fonts.displayInMain).toMatch(/^"?Crimson Pro/);
    expect(fonts.proseInMain).toMatch(/^"?Crimson Pro/);
    expect(fonts.utilityOutside).toMatch(/^"?Plus Jakarta Sans/);

    // Dialogs are portaled out of <main> and follow the same rule; writing content in them stays serif.
    await page.getByRole('button', { name: '我的技能', exact: true }).click();
    await page.getByRole('button', { name: '编辑技能', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '编辑技能', exact: true });
    await expect(dialog).toBeVisible();
    const dialogFonts = await dialog.evaluate((el) => {
      const box = document.createElement('div');
      box.innerHTML = '<h3 id="probe-label">统计</h3><div class="editor-content"><h3 id="probe-writing">第二章</h3></div>';
      el.appendChild(box);
      const read = (id: string) => getComputedStyle(box.querySelector('#' + id)!).fontFamily;
      const result = { label: read('probe-label'), writing: read('probe-writing') };
      box.remove();
      return result;
    });
    expect(dialogFonts.label).toMatch(/^"?Plus Jakarta Sans/);
    expect(dialogFonts.writing).toMatch(/^"?Crimson Pro/);
  });

  test('the API-key tab lists a key with named actions and opens the create form', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockResponsiveApp(page, null, 'pro');
    await page.goto('/dashboard');
    await page.getByTestId('dashboard-user-panel-toggle').first().click();
    await page.getByTestId('dashboard-open-settings-button').click();
    const dialog = page.getByRole('dialog', { name: '设置', exact: true });
    await dialog.getByTestId('settings-tab-agent').click();

    await expect(dialog.getByText('写作助手（家里的电脑）')).toBeVisible();
    for (const name of ['禁用', '重新生成', '删除']) {
      await expect(dialog.getByRole('button', { name, exact: true })).toBeVisible();
    }

    await dialog.getByRole('button', { name: '创建密钥', exact: true }).click();
    const submit = dialog.locator('form button[type="submit"]');
    await expect(submit).toHaveText('创建密钥');
    expect((await submit.boundingBox())!.height).toBeGreaterThanOrEqual(40);
    expect(isTransparent((await borderTop(submit)).color)).toBe(true);
    await expect(dialog.getByRole('button', { name: '取消', exact: true })).toBeVisible();
  });

  test('an API key row keeps a readable name on a 390px phone', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockResponsiveApp(page, null, 'pro');
    await page.goto('/dashboard');
    await page.getByRole('button', { name: '打开菜单', exact: true }).click();
    await page.getByRole('button', { name: '设置', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '设置', exact: true });
    await dialog.getByTestId('settings-tab-agent').click();

    const name = dialog.getByText('写作助手（家里的电脑）');
    await name.scrollIntoViewIfNeeded();
    // Three 44px actions beside the key once left the name ~48px wide.
    expect((await name.boundingBox())!.width).toBeGreaterThanOrEqual(120);
    const remove = dialog.getByRole('button', { name: '删除', exact: true });
    expect((await remove.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    const status = dialog.getByText('活跃', { exact: true });
    expect((await status.boundingBox())!.height).toBeLessThan(24); // one line, not one character per line
  });
});
