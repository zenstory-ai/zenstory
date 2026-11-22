import { expect, test, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { bootstrapAdminSession, longText, users } from './fixtures/admin-responsive';

const routes = [
  ['dashboard', '/admin', '1,289'], ['acquisition', '/admin/acquisition', 'www.google.com'],
  ['users', '/admin/users', users[0].username],
  ['user-detail', '/admin/users/user-1', users[0].email], ['usage', '/admin/usage', users[0].email],
  ['prompts', '/admin/prompts', 'novel'], ['prompt-new', '/admin/prompts/new', null],
  ['prompt-edit', '/admin/prompts/novel', null], ['skills', '/admin/skills', 'Long-form narrative continuity reviewer'],
  ['codes', '/admin/codes', 'RESPONSIVE-AUDIT-LONG-CODE-2026'], ['subscriptions', '/admin/subscriptions', users[0].email],
  ['payments', '/admin/payment-orders', users[0].email], ['plans', '/admin/plans', 'Professional Writer Plan'],
  ['audit', '/admin/audit-logs', 'super-administrator-with-long-name'], ['feedback', '/admin/feedback', users[0].email],
  ['announcements', '/admin/announcements', 'Maintenance notice'],
] as const;

const viewports = [
  { width: 390, height: 844 }, { width: 768, height: 1024 },
  { width: 1280, height: 720 }, { width: 1920, height: 1080 },
];

async function assertLocalContent(page: Page, label: string) {
  const defects = await page.locator('.admin-page').evaluate((root) => {
    const bounds = root.getBoundingClientRect();
    const isLocalScroller = (element: Element) => {
      let parent: Element | null = element;
      while (parent && parent !== root) {
        const style = getComputedStyle(parent);
        if (['auto', 'scroll'].includes(style.overflowX)) return true;
        parent = parent.parentElement;
      }
      return false;
    };
    return Array.from(root.querySelectorAll('h1,h2,h3,p,button,input,select,textarea,dt,dd')).flatMap((el) => {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden' || isLocalScroller(el)) return [];
      return r.left < bounds.left - 1 || r.right > bounds.right + 1
        ? [{ tag: el.tagName, text: (el.textContent || el.getAttribute('aria-label') || '').slice(0, 60), left: r.left, right: r.right, pageLeft: bounds.left, pageRight: bounds.right }]
        : [];
    });
  });
  expect(defects, label).toEqual([]);
  const collisions = await page.locator('.admin-page').evaluate(root => {
    const controls = Array.from(root.querySelectorAll('button,input,select,textarea')).filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.bottom > 56 && r.top < innerHeight && getComputedStyle(el).visibility !== 'hidden';
    });
    return controls.flatMap((a, index) => controls.slice(index + 1).flatMap(b => {
      if (a.contains(b) || b.contains(a)) return [];
      const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
      const xOverlap = Math.min(ar.right, br.right) - Math.max(ar.left, br.left);
      const yOverlap = Math.min(ar.bottom, br.bottom) - Math.max(ar.top, br.top);
      return xOverlap > 1 && yOverlap > 1 ? [(a.textContent || a.getAttribute('aria-label') || a.tagName).slice(0, 40) + ' / ' + (b.textContent || b.getAttribute('aria-label') || b.tagName).slice(0, 40)] : [];
    }));
  });
  expect(collisions, `overlapping controls: ${label}`).toEqual([]);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
}

test.describe('Admin detailed responsive layout', () => {
  test.setTimeout(180_000);
  for (const language of ['zh', 'en']) {
    for (const width of [390, 768]) {
      test(`${language} ${width}px: feedback status remains fully readable next to a long identity`, async ({ page }) => {
        await page.setViewportSize({ width, height: 844 });
        await page.addInitScript(lang => localStorage.setItem('zenstory-language', lang), language);
        await bootstrapAdminSession(page);
        await page.goto('/admin/feedback');
        await expect(page.getByText(longText, { exact: true })).toBeVisible();
        const select = page.locator('select').last();
        const geometry = await select.evaluate(el => {
          const style = getComputedStyle(el);
          const canvas = document.createElement('canvas');
          const context = canvas.getContext('2d')!;
          context.font = style.font;
          return { available: el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
            text: context.measureText(el.selectedOptions[0].text).width };
        });
        expect(geometry.available).toBeGreaterThanOrEqual(geometry.text);
      });
    }
  }
  test('phone admin filter chevrons remain inside their select borders', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await bootstrapAdminSession(page);
    for (const path of ['/admin/audit-logs', '/admin/codes', '/admin/subscriptions', '/admin/feedback']) {
      await page.goto(path);
      await expect(page.locator('select').first()).toBeVisible();
      const escaped = await page.locator('select').evaluateAll(selects => selects.flatMap(select => {
        const chevron = select.parentElement?.querySelector('svg');
        if (!chevron || !select.getBoundingClientRect().width) return [];
        const sr = select.getBoundingClientRect(), cr = chevron.getBoundingClientRect();
        return cr.left < sr.left || cr.right > sr.right ? [select.value] : [];
      }));
      expect(escaped, path).toEqual([]);
    }
  });
  for (const language of ['zh', 'en']) {
  for (const viewport of viewports) {
    test(`${language} ${viewport.width}px: every available admin page contains controls and long content`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await page.addInitScript(lang => localStorage.setItem('zenstory-language', lang), language);
      await bootstrapAdminSession(page);
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      for (const [name, path, readyText] of routes) {
        await page.goto(path);
        await expect(page.locator('.admin-page')).toBeVisible();
        if (name === 'prompt-new') await expect(page.locator('textarea').first()).toBeVisible();
        else if (name === 'prompt-edit') await expect(page.locator('textarea').first()).toHaveValue(longText);
        else if (name === 'announcements') await expect(page.locator('input').first()).toHaveValue(readyText!);
        else await expect(page.getByText(readyText!, { exact: false }).filter({ visible: true }).first()).toBeVisible();
        await assertLocalContent(page, `${viewport.width}: ${path}`);
        const artifactDir = process.env.ADMIN_DETAIL_ARTIFACT_DIR;
        if (artifactDir) {
          await mkdir(artifactDir, { recursive: true });
          await page.screenshot({ path: `${artifactDir}/${language}-${viewport.width}-${name}.png` });
        }
        await page.locator('main').evaluate(el => { el.scrollTop = el.scrollHeight; });
        await assertLocalContent(page, `${viewport.width}: ${path} bottom`);
      }
      expect(errors).toEqual([]);
      await testInfo.attach('routes-checked', { body: Buffer.from(routes.map(([, path]) => path).join('\n')), contentType: 'text/plain' });
    });
  }
  }

  for (const width of [390, 768]) {
    test(`${width}px: prompt header actions stay on one line and card icons have touch targets`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await bootstrapAdminSession(page);
      await page.goto('/admin/prompts');
      await expect(page.getByText('novel', { exact: true }).first()).toBeVisible();
      for (const name of ['重新加载', '新建配置']) {
        const box = (await page.getByRole('button', { name, exact: true }).boundingBox())!;
        expect(box.height).toBeLessThanOrEqual(48);
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
      for (const name of ['编辑', '删除']) {
        const box = (await page.getByRole('button', { name, exact: true }).boundingBox())!;
        expect(box.width).toBeGreaterThanOrEqual(width < 768 ? 44 : 32);
        expect(box.height).toBeGreaterThanOrEqual(width < 768 ? 44 : 32);
      }
    });

    test(`${width}px: daily usage table can scroll using the keyboard`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await bootstrapAdminSession(page);
      await page.goto('/admin/usage');
      await page.getByRole('button', { name: '近7天', exact: true }).click();
      const scroller = page.getByRole('region', { name: '每日', exact: true });
      await scroller.scrollIntoViewIfNeeded();
      await scroller.focus();
      await expect(scroller).toBeFocused();
      await page.keyboard.press('ArrowRight');
      await expect.poll(() => scroller.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
    });

    test(`${width}px: payment filters and user-detail adjustment fields retain useful widths`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await bootstrapAdminSession(page);
      await page.goto('/admin/payment-orders');
      await expect(page.getByText(users[0].email).first()).toBeVisible();
      await expect(page.locator('table')).toBeHidden();
      const details = page.getByRole('button', { name: /^详情$|^details$/i }).filter({ visible: true }).first();
      await expect(details).toBeVisible();
      const detailsBox = (await details.boundingBox())!;
      expect(detailsBox.x + detailsBox.width).toBeLessThanOrEqual(width);
      const search = page.getByRole('textbox').first();
      expect((await search.boundingBox())!.width).toBeGreaterThan(150);
      await assertLocalContent(page, 'payment filters');

      await page.goto('/admin/users/user-1');
      await expect(page.getByText(users[0].email).first()).toBeVisible();
      await expect(page.getByRole('link', { name: /查看用量|View usage/ })).toBeVisible();
      await assertLocalContent(page, 'user detail');
    });

    test(`${width}px: payment and usage details fit with readable long identifiers`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await bootstrapAdminSession(page);
      for (const path of ['/admin/payment-orders', '/admin/usage']) {
        await page.goto(path);
        const openDetails = path.endsWith('usage')
          ? page.getByRole('button', { name: /writer-with-a-long-name/i }).first()
          : page.getByRole('button', { name: /详情|detail/i }).first();
        await openDetails.click();
        const dialog = page.getByRole('dialog');
        await expect(dialog).toBeVisible();
        const geometry = await dialog.evaluate(el => {
          const r = el.getBoundingClientRect();
          return { left: r.left, right: r.right, width: innerWidth, overflow: el.scrollWidth - el.clientWidth };
        });
        expect(geometry.left).toBeGreaterThanOrEqual(0);
        expect(geometry.right).toBeLessThanOrEqual(geometry.width);
        expect(geometry.overflow).toBeLessThanOrEqual(1);
        await page.keyboard.press('Escape');
        await expect(dialog).toBeHidden();
      }
    });
  }
});
