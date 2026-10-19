import { test, expect, type Locator, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';

const viewports = [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1280, height: 800 },
  { width: 1920, height: 1080 },
];

async function box(locator: Locator) {
  await expect(locator).toBeVisible();
  return (await locator.boundingBox())!;
}

async function openSettings(page: Page, width: number) {
  if (width < 768) {
    await page.getByRole('button', { name: '打开菜单', exact: true }).click();
    await page.getByRole('button', { name: '设置', exact: true }).click();
  } else {
    await page.getByTestId('dashboard-user-panel-toggle').first().click();
    await page.getByTestId('dashboard-open-settings-button').click();
  }
  return page.getByRole('dialog', { name: '设置', exact: true });
}

for (const viewport of viewports) {
  test(`project search and long-name detail headers remain usable at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockResponsiveApp(page, null, 'pro');
    await page.goto('/dashboard/projects');
    const search = page.getByPlaceholder('搜索项目');
    expect((await box(search)).width).toBeGreaterThanOrEqual(200);
    await search.fill('小屏布局回归项目');
    await expect(page.getByRole('button', { name: '打开项目「小屏布局回归项目」' })).toBeVisible();
    await search.fill('没有这个项目');
    await expect(page.getByText('没有匹配的项目', { exact: true })).toBeVisible();
    await search.clear();

    await page.goto('/project/responsive-project-1/dashboard');
    await expect(page.getByRole('heading', { name: '项目统计', exact: true })).toBeVisible();
    const heading = page.getByRole('heading', { name: '项目统计', exact: true });
    const refresh = page.getByRole('button', { name: '刷新', exact: true });
    const headingBox = await box(heading);
    const refreshBox = await box(refresh);
    expect(headingBox.x + headingBox.width + 8).toBeLessThanOrEqual(refreshBox.x);
    const projectNameBox = await box(page.getByText(responsiveProjects[1].name, { exact: true }));
    expect(projectNameBox.y).toBeGreaterThanOrEqual(headingBox.y + headingBox.height);
    expect(projectNameBox.x + projectNameBox.width).toBeLessThanOrEqual(viewport.width);
    expect(refreshBox.width).toBeGreaterThanOrEqual(viewport.width < 768 ? 44 : 32);
    expect(refreshBox.height).toBeGreaterThanOrEqual(viewport.width < 768 ? 44 : 32);
    for (const toggle of [page.locator('[aria-controls="project-chapter-details"]'), page.getByRole('button', { name: /手写字数趋势/ })]) {
      await toggle.scrollIntoViewIfNeeded();
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    }
    await page.screenshot({ path: `/tmp/zenstory-ui-audit/app-project-stats-${viewport.width}.png` });

    await page.goto('/materials/responsive-material');
    const materialHeading = page.getByRole('heading', { name: '素材标题：很长的人物选择与情节发展', exact: true });
    await expect(materialHeading).toBeVisible();
    const back = page.getByRole('button', { name: '返回', exact: true });
    expect((await box(back)).width).toBeGreaterThanOrEqual(44);
    expect((await box(back)).height).toBeGreaterThanOrEqual(44);
    const materialSearch = page.getByPlaceholder('搜索', { exact: true });
    expect((await box(materialSearch)).width).toBeGreaterThanOrEqual(200);
    const actualInputFont = await materialSearch.evaluate(element => Number.parseFloat(getComputedStyle(element).fontSize));
    if (viewport.width < 768) expect(actualInputFont).toBeGreaterThanOrEqual(16);
    await page.getByRole('button', { name: '章节', exact: true }).click();
    await page.getByText('第1章 很长的标题与人物选择', { exact: true }).click();
    await expect(page.getByText('章节摘要。'.repeat(30), { exact: true })).toBeVisible();
  });

  test(`community skill actions, billing and settings fit at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockResponsiveApp(page, null, 'pro');
    await page.route('**/api/v1/public-skills?**', route => route.fulfill({ json: {
      skills: [{ id: 'community-skill', name: '人物行动与剧情的因果关系', description: '用具体行动检查角色选择，不覆盖原有设定。', instructions: '# 人物行动\n' + '检查行动与后果。'.repeat(30), instructions_hidden: false, source: 'community', category: 'Writing', add_count: 12800, tags: [], status: 'approved' }],
      total: 1, page: 1, page_size: 20,
    } }));
    await page.goto('/dashboard/skills');
    const expand = page.getByRole('button', { name: '展开指令', exact: true });
    const add = page.getByRole('button', { name: '添加到我的技能', exact: true });
    const expandBox = await box(expand);
    const addBox = await box(add);
    const countBox = await box(page.getByText('12800 人添加', { exact: true }));
    // If the footer wraps, the second action must have a separate row; otherwise
    // the two labels must retain enough room to be independent click targets.
    if (Math.abs(expandBox.y - addBox.y) < 5) expect(expandBox.x + expandBox.width + 8).toBeLessThanOrEqual(addBox.x);
    else expect(addBox.y).toBeGreaterThanOrEqual(expandBox.y + expandBox.height + 8);
    if (Math.abs(expandBox.y + expandBox.height / 2 - countBox.y - countBox.height / 2) < 5) {
      expect(expandBox.x + expandBox.width + 8).toBeLessThanOrEqual(countBox.x);
    } else {
      expect(countBox.y).toBeGreaterThanOrEqual(expandBox.y + expandBox.height + 8);
    }
    expect(addBox.x + addBox.width).toBeLessThanOrEqual(viewport.width - 12);
    await expand.click();
    await expect(page.getByRole('button', { name: '收起指令', exact: true })).toHaveAttribute('aria-expanded', 'true');
    await page.screenshot({ path: `/tmp/zenstory-ui-audit/app-skills-${viewport.width}.png` });
    await page.getByRole('button', { name: '我的技能', exact: true }).click();
    await page.getByRole('button', { name: '编辑技能', exact: true }).click();
    const edit = page.getByRole('dialog', { name: '编辑技能', exact: true });
    await expect(edit).toBeVisible();
    await edit.getByRole('button', { name: '保存', exact: true }).scrollIntoViewIfNeeded();
    await page.keyboard.press('Escape');
    await expect(edit).not.toBeVisible();

    await page.goto('/dashboard/billing');
    const help = page.getByTestId('billing-help');
    await expect(help).not.toHaveAttribute('open', '');
    await help.locator('summary').click();
    await expect(page.getByTestId('billing-support-email').getByRole('link')).toHaveAttribute('href', /^mailto:/);
    await expect(page.getByRole('button', { name: '兑换码', exact: true })).toHaveCount(0);
    await help.locator('summary').click();
    await expect(help).not.toHaveAttribute('open', '');

    await page.goto('/dashboard/projects');
    const settings = await openSettings(page, viewport.width);
    await expect(settings).toBeVisible();
    for (const id of ['profile', 'general', 'subscription', 'points', 'agent', 'referral']) {
      if (await settings.getByTestId(`settings-tab-${id}`).count() === 0) continue;
      await settings.getByTestId(`settings-tab-${id}`).click();
      await expect(settings.getByRole('tabpanel')).toBeVisible();
      expect(await settings.evaluate(element => element.scrollWidth)).toBeLessThanOrEqual(Math.ceil((await box(settings)).width));
    }
    await page.screenshot({ path: `/tmp/zenstory-ui-audit/app-settings-${viewport.width}.png` });
  });

  test(`materials, workspace, onboarding and payment states at ${viewport.width}`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize(viewport);
    await mockResponsiveApp(page, null, 'pro');
    await page.goto('/dashboard/materials');
    await expect(page.getByTestId('material-card')).toHaveCount(12);
    await page.getByRole('button', { name: '上传小说', exact: true }).click();
    const upload = page.getByRole('dialog');
    await expect(upload).toBeVisible();
    const uploadBox = await box(upload);
    expect(uploadBox.x).toBeGreaterThanOrEqual(0);
    expect(uploadBox.x + uploadBox.width).toBeLessThanOrEqual(viewport.width);
    await page.keyboard.press('Escape');
    await expect(upload).not.toBeVisible();

    await page.goto('/project/responsive-project-0');
    if (viewport.width < 768) {
      await page.getByRole('tab', { name: 'AI', exact: true }).click();
    }
    const chat = page.getByTestId('chat-input');
    await expect(chat).toBeVisible();
    await chat.fill('保留草稿，切换面板后继续写作。');
    if (viewport.width < 768) {
      await page.getByRole('tab', { name: '文件', exact: true }).click();
      await expect(page.locator('#chat-panel')).toHaveAttribute('aria-hidden', 'true');
      await expect(page.locator('#chat-panel')).toHaveAttribute('inert', '');
      await page.getByRole('tab', { name: '编辑', exact: true }).click();
      await page.getByRole('tab', { name: 'AI', exact: true }).click();
      await expect(chat).toHaveValue('保留草稿，切换面板后继续写作。');
    }
    const send = await box(page.getByTestId('send-button'));
    expect(send.y + send.height).toBeLessThanOrEqual(viewport.height);
    await page.screenshot({ path: `/tmp/zenstory-ui-audit/app-workspace-${viewport.width}.png` });

    await page.goto('/onboarding/persona');
    await expect(page.getByRole('heading', { name: '你想写什么？', exact: true })).toBeVisible();
    // One tap answers: every option and 跳过 are full touch targets inside the first screen.
    for (const type of ['novel', 'short', 'screenplay']) {
      const option = await box(page.getByTestId(`onboarding-type-${type}`));
      expect(option.height).toBeGreaterThanOrEqual(44);
      expect(option.y + option.height).toBeLessThanOrEqual(viewport.height);
    }
    expect((await box(page.getByRole('button', { name: '先跳过', exact: true }))).height).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: `/tmp/zenstory-ui-audit/app-onboarding-${viewport.width}.png` });

    for (const state of ['missing', 'pending', 'failed', 'succeeded']) {
      const orderNo = `local-ui-${state}-${'1234567890'.repeat(6)}`;
      if (state !== 'missing') {
        await page.route(`**/api/v1/payments/orders/${orderNo}`, route => route.fulfill({ json: {
          id: orderNo, out_trade_no: orderNo, user_id: 'responsive-user', plan_name: 'pro', cycle: 'year', amount_cents: 39900,
          status: state === 'pending' ? 'pending' : 'paid', fulfillment_status: state === 'pending' ? 'pending' : state,
          created_at: '2026-10-13T00:00:00Z',
        } }));
      }
      await page.goto(`/dashboard/billing/payment-return${state === 'missing' ? '' : `?out_trade_no=${orderNo}`}`);
      const headings = { missing: '没有找到这笔订单', pending: '付款确认中', failed: '付款已确认，Pro 还没开通', succeeded: '支付成功，Pro 已开通' };
      await expect(page.getByRole('heading', { name: headings[state as keyof typeof headings], exact: true })).toBeVisible();
      const billing = page.getByRole('button', { name: '返回订阅权益', exact: true });
      await billing.scrollIntoViewIfNeeded();
      const billingBox = await box(billing);
      expect(billingBox.x + billingBox.width).toBeLessThanOrEqual(viewport.width - 12);
      expect(billingBox.height).toBeGreaterThanOrEqual(44);
    }
    await page.screenshot({ path: `/tmp/zenstory-ui-audit/app-payment-${viewport.width}.png` });
    if (viewport.width < 768) {
      await page.getByRole('button', { name: '打开菜单', exact: true }).click();
      await expect(page.locator('#dashboard-mobile-navigation')).toBeVisible();
    }
    const inspirationEntry = page.locator('a[href="/dashboard/inspirations"]');
    const hasInspirationEntry = await inspirationEntry.count() > 0;
    if (hasInspirationEntry) await expect(inspirationEntry.first()).toBeVisible();
    if (viewport.width < 768) {
      await page.keyboard.press('Escape');
      await expect(page.locator('#dashboard-mobile-navigation')).not.toBeVisible();
    }
    if (!hasInspirationEntry) {
      await page.goto('/dashboard/inspirations');
      await expect(page).toHaveURL(/\/dashboard$/);
    }
  });

  test(`skill failure and material retry stay independent at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockResponsiveApp(page, null, 'pro');
    let discoverFails = true;
    await page.route('**/api/v1/public-skills?**', route => discoverFails
      ? route.fulfill({ status: 503, json: { detail: 'Local synthetic outage' } })
      : route.fallback());
    await page.goto('/dashboard/skills');
    const error = page.getByRole('alert').filter({ hasText: '技能加载失败，请重试' });
    await expect(error).toBeVisible();
    if (viewport.width === 390) await page.screenshot({ path: '/tmp/zenstory-ui-audit/app-skills-error-390.png' });
    await expect(page.getByText('还没有可发现的技能', { exact: true })).not.toBeVisible();
    const retry = error.getByRole('button', { name: '重试', exact: true });
    expect((await box(retry)).height).toBeGreaterThanOrEqual(44);
    discoverFails = false;
    await retry.click();
    await expect(page.getByRole('heading', { name: '长标题技能：人物行动与剧情的因果关系' })).toBeVisible();
    await expect(error).not.toBeVisible();

    let myFails = true;
    await page.route('**/api/v1/skills/my-skills**', route => myFails
      ? route.fulfill({ status: 503, json: { detail: 'Local synthetic outage' } })
      : route.fallback());
    await page.getByRole('button', { name: '我的技能', exact: true }).click();
    await expect(error).toBeVisible();
    myFails = false;
    await retry.click();
    await expect(page.getByRole('button', { name: '编辑技能', exact: true })).toBeVisible();

    await page.route('**/api/v1/materials/list**', route => route.fulfill({ json: [{
      id: 'retry-material', title: '需要重试的长标题参考小说', original_filename: 'local.txt',
      status: 'failed', chapters_count: 0, error_message: 'ERR_DECOMPOSITION_FAILED',
      created_at: '2026-10-13T00:00:00Z', updated_at: '2026-10-13T00:00:00Z',
    }] }));
    let retries = 0;
    await page.route('**/api/v1/materials/retry-material/retry', route => {
      retries += 1;
      return route.fulfill({ json: { message: 'Local retry queued' } });
    });
    await page.goto('/dashboard/materials');
    const materialRetry = page.getByTestId('material-card').getByRole('button', { name: '重试', exact: true });
    expect((await box(materialRetry)).height).toBeGreaterThanOrEqual(44);
    await materialRetry.focus();
    await page.keyboard.press('Space');
    await expect.poll(() => retries).toBe(1);
    await expect(page).toHaveURL(/\/dashboard\/materials$/);
    await page.screenshot({ path: `/tmp/zenstory-ui-audit/app-material-retry-${viewport.width}.png` });
  });
}
