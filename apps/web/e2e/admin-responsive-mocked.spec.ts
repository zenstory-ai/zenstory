import { expect, test, type Page, type Route } from '@playwright/test';

const adminUser = {
  id: 'admin-1', username: 'admin', email: 'admin@example.com', email_verified: true,
  is_active: true, is_superuser: true, created_at: '2026-03-01T00:00:00Z', updated_at: '2026-03-01T00:00:00Z',
};

const stamp = '2026-03-01T00:00:00Z';
const longText = 'A deliberately long populated value used to verify that dense admin content wraps or scrolls locally without widening the document.';
const users = Array.from({ length: 3 }, (_, index) => ({
  id: `user-${index + 1}`,
  username: `writer-with-a-long-name-${index + 1}`,
  email: `long-admin-responsive-address-${index + 1}@example.com`,
  email_verified: true,
  is_active: true,
  is_superuser: index === 0,
  created_at: stamp,
  updated_at: stamp,
}));

const prompt = {
  id: 'prompt-1', project_type: 'novel', role_definition: longText, capabilities: longText,
  directory_structure: '/novel/chapters/chapter-001.md', content_structure: longText,
  file_types: '.md,.txt', writing_guidelines: longText, include_dialogue_guidelines: true,
  primary_content_type: 'novel', is_active: true, version: 7, created_at: stamp, updated_at: stamp,
};

const json = (route: Route, body: unknown) => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

async function bootstrapAdminSession(page: Page) {
  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'mock-access-token');
    localStorage.setItem('refresh_token', 'mock-refresh-token');
    localStorage.setItem('token_type', 'bearer');
    localStorage.setItem('user', JSON.stringify(user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, adminUser);

  await page.route('**/api/auth/me', (route) => json(route, adminUser));
  await page.route('**/api/auth/refresh', (route) => json(route, { access_token: 'mock-access-token', refresh_token: 'mock-refresh-token', user: adminUser }));
  await page.route('**/api/v1/projects**', (route) => json(route, []));
  // The materials library checks the plan before requesting the summary.
  await page.route('**/api/v1/subscription/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tier: 'pro',
        status: 'active',
        display_name: 'Pro',
        display_name_en: 'Pro',
        current_period_end: null,
        days_remaining: null,
        features: { materials_library_access: true },
      }),
    });
  });

  await page.route('**/api/v1/materials/library-summary**', (route) => json(route, []));
  await page.route('**/api/admin/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    const method = route.request().method();
    if (method !== 'GET') return json(route, { success: true, message: 'ok' });

    if (pathname.endsWith('/dashboard/stats')) return json(route, { total_users: 1289, active_users: 934, new_users_today: 37, total_projects: 4821, total_inspirations: 271, pending_inspirations: 14, active_subscriptions: 408, pro_users: 379, total_points_in_circulation: 902341, today_check_ins: 611, active_invite_codes: 88, week_referrals: 143 });
    if (pathname.endsWith('/dashboard/activation-funnel')) return json(route, { window_days: 7, period_start: stamp, period_end: stamp, activation_rate: 0.378, steps: [{ event_name: 'signup', label: 'Registered account with long attribution label', users: 1200, conversion_from_previous: null, drop_off_from_previous: null }, { event_name: 'created', label: 'Created first writing project', users: 454, conversion_from_previous: 0.378, drop_off_from_previous: 0.622 }] });
    if (pathname.endsWith('/dashboard/upgrade-conversions')) return json(route, { window_days: 7, period_start: stamp, period_end: stamp, total_conversions: 73, unattributed_conversions: 4, sources: [{ source: 'editor-upgrade-banner-with-long-campaign-name', conversions: 69, share: 94.5 }] });
    if (pathname.endsWith('/dashboard/upgrade-funnel')) return json(route, { window_days: 7, period_start: stamp, period_end: stamp, totals: { expose: 1800, click: 352, conversion: 73 }, sources: [{ source: 'editor-upgrade-banner-with-long-campaign-name', exposes: 1800, clicks: 352, conversions: 73, click_through_rate: 19.6, conversion_rate_from_click: 20.7, conversion_rate_from_expose: 4.1 }] });
    if (pathname === '/api/admin/users') return json(route, { items: users, total: users.length });
    if (pathname === '/api/admin/prompts') return json(route, { items: [prompt] });
    if (pathname.endsWith('/api/admin/prompts/novel')) return json(route, prompt);
    if (pathname.endsWith('/api/admin/skills/pending')) return json(route, { items: [{ id: 'skill-1', name: 'Long-form narrative continuity reviewer', description: longText, instructions: longText, category: 'writing-and-development', author_id: 'user-1', author_name: 'writer-with-a-long-name', status: 'pending', reviewed_by: null, reviewer_name: null, reviewed_at: null, rejection_reason: null, created_at: stamp }] });
    if (pathname === '/api/admin/codes') return json(route, { items: [{ id: 'code-1', code: 'RESPONSIVE-AUDIT-LONG-CODE-2026', tier: 'pro', duration_days: 365, code_type: 'multi_use', max_uses: 500, current_uses: 214, is_active: true, notes: longText, created_at: stamp, updated_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname === '/api/admin/subscriptions') return json(route, { items: [{ id: 'sub-1', user_id: 'user-1', username: users[0].username, email: users[0].email, plan_name: 'pro', plan_display_name: 'Professional Annual Writer', status: 'active', current_period_start: stamp, current_period_end: '2027-03-01T00:00:00Z', created_at: stamp, updated_at: stamp, has_subscription_record: true }], total: 1, page: 1, page_size: 20 });
    if (pathname === '/api/admin/plans') return json(route, [{ id: 'plan-pro', name: 'pro', display_name: 'Professional Writer Plan', display_name_en: 'Professional Writer Plan', price_monthly_cents: 1999, price_yearly_cents: 19999, features: { ai_conversations: 5000, material_uploads: 250, skill_creates: 100 }, is_active: true, created_at: stamp, updated_at: stamp }]);
    if (pathname === '/api/admin/audit-logs') return json(route, { items: [{ id: 'log-1', admin_id: 'admin-1', admin_name: 'super-administrator-with-long-name', action: 'subscription.configuration.updated', resource_type: 'subscription_plan_configuration', resource_id: 'professional-annual-plan-identifier', details: longText, old_value: { description: longText }, new_value: { description: `${longText} Updated.` }, ip_address: '2001:db8:3333:4444:5555:6666:7777:8888', user_agent: longText, created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname === '/api/admin/inspirations') return json(route, { items: [{ id: 'ins-1', name: 'Urban fantasy mystery with a deliberately long title', description: longText, tags: ['urban-fantasy', 'slow-burn-mystery', 'found-family'], source: 'community', status: 'pending', is_featured: false, sort_order: 1, copy_count: 234, creator_id: 'user-1', creator_name: users[0].username, reviewer_id: null, created_at: stamp, updated_at: stamp }], total: 1 });
    if (pathname === '/api/admin/feedback') return json(route, { items: [{ id: 'feedback-1', user_id: 'user-1', username: users[0].username, email: users[0].email, source_page: 'editor', source_route: '/projects/extremely-long-project-identifier/editor', issue_text: longText, trace_id: 'trace-extremely-long-correlation-identifier-1234567890', request_id: 'request-1234567890', agent_run_id: 'agent-run-1234567890', project_id: 'project-1234567890', agent_session_id: 'session-1234567890', has_screenshot: false, screenshot_original_name: null, screenshot_content_type: null, screenshot_size_bytes: null, screenshot_download_url: null, status: 'open', created_at: stamp, updated_at: stamp }], total: 1 });
    if (pathname.endsWith('/points/stats')) return json(route, { total_points_issued: 9999999, total_points_spent: 7654321, total_points_expired: 123456, active_users_with_points: 8765 });
    if (pathname.endsWith('/points/transactions')) return json(route, { items: [{ id: 'tx-1', user_id: 'user-1', username: users[0].username, amount: 1000, balance_after: 9000, transaction_type: 'admin_adjustment_with_long_label', source_id: null, description: longText, expires_at: null, is_expired: false, created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname.includes('/points/users/')) return json(route, { user_id: 'user-1', username: users[0].username, email: users[0].email, available: 9000, pending_expiration: 1000, total_earned: 12000, total_spent: 3000 });
    if (pathname.endsWith('/check-in/stats')) return json(route, { today_count: 611, yesterday_count: 598, week_total: 4012, streak_distribution: { 1: 120, 7: 89, 30: 17 } });
    if (pathname.endsWith('/check-in/records')) return json(route, { items: [{ id: 'check-1', user_id: 'user-1', username: users[0].username, check_in_date: '2026-03-01', streak_days: 30, points_earned: 100, created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname.endsWith('/referrals/stats')) return json(route, { total_codes: 88, active_codes: 74, total_referrals: 1430, successful_referrals: 1190, pending_rewards: 19, total_points_awarded: 89200 });
    if (pathname.endsWith('/api/admin/invites')) return json(route, { items: [{ id: 'invite-1', code: 'INVITE-RESPONSIVE-AUDIT-2026', owner_id: 'user-1', owner_name: users[0].username, current_uses: 52, max_uses: 100, is_active: true, created_at: stamp, expires_at: '2027-03-01T00:00:00Z' }], total: 1, page: 1, page_size: 20 });
    if (pathname.endsWith('/referrals/rewards')) return json(route, { items: [{ id: 'reward-1', user_id: 'user-1', username: users[0].username, reward_type: 'successful_referral_award', amount: 500, source: 'invite-responsive-audit-2026', is_used: false, expires_at: '2027-03-01T00:00:00Z', created_at: stamp }], total: 1, page: 1, page_size: 20 });
    if (pathname.endsWith('/quota/usage')) return json(route, { material_uploads: 48321, material_decomposes: 18765, skill_creates: 9432, inspiration_copies: 29110 });
    if (pathname.includes('/quota/users/')) return json(route, { user_id: 'user-1', username: users[0].username, plan_name: 'Professional Annual Writer', ai_conversations_used: 4500, ai_conversations_limit: 5000, material_upload_used: 220, material_upload_limit: 250, skill_create_used: 88, skill_create_limit: 100, inspiration_copy_used: 410, inspiration_copy_limit: 500 });
    return json(route, {});
  });
}

const routes = [
  ['dashboard', '/admin', '1,289'], ['users', '/admin/users', users[0].username], ['prompts', '/admin/prompts', 'novel'],
  ['prompt-new', '/admin/prompts/new', null], ['prompt-novel', '/admin/prompts/novel', null],
  ['skills', '/admin/skills', 'Long-form narrative continuity reviewer'], ['codes', '/admin/codes', 'RESPONSIVE-AUDIT-LONG-CODE-2026'],
  ['subscriptions', '/admin/subscriptions', users[0].email], ['plans', '/admin/plans', 'Professional Writer Plan'],
  ['audit-logs', '/admin/audit-logs', 'super-administrator-with-long-name'], ['feedback', '/admin/feedback', users[0].email],
  ['points', '/admin/points', '9,999,999'], ['check-in', '/admin/check-in', '611'],
  ['referrals', '/admin/referrals', 'INVITE-RESPONSIVE-AUDIT-2026'], ['quota', '/admin/quota', '48,321'],
] as const;

const viewports = [
  { name: 'small-laptop', width: 1280, height: 720 },
  { name: 'laptop', width: 1366, height: 768 },
  { name: 'large-laptop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
  { name: 'tablet', width: 768, height: 1024 },
] as const;

test.describe('Admin responsive routes with populated mocked data', () => {
  test.setTimeout(180_000);

  for (const viewport of viewports) {
    test(`${viewport.name}: all admin pages stay within the document viewport`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await bootstrapAdminSession(page);
      const pageErrors: string[] = [];
      page.on('pageerror', (error) => pageErrors.push(error.message));

      for (const [name, path, readyText] of routes) {
        await page.goto(path);
        await expect(page.locator('.admin-page')).toBeVisible();
        if (name === 'prompt-new') {
          await expect(page.locator('textarea').first()).toBeVisible();
        } else if (name === 'prompt-novel') {
          await expect(page.locator('textarea').first()).toHaveValue(longText);
        } else {
          await expect.poll(() => page.getByText(readyText!, { exact: false }).evaluateAll((elements) =>
            elements.some((element) => {
              const style = getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
            }),
          )).toBe(true);
        }
        await expect(page.locator('body')).not.toContainText(/something went wrong|页面加载失败/i);
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

        const artifactDir = process.env.ADMIN_RESPONSIVE_ARTIFACT_DIR;
        if (artifactDir) {
          await page.screenshot({ path: `${artifactDir}/${viewport.name}-${name}.png`, fullPage: true });
        }
      }

      expect(pageErrors, `page errors at ${viewport.name}`).toEqual([]);
      await testInfo.attach(`${viewport.name}-coverage`, { body: Buffer.from(routes.map(([, path]) => path).join('\n')), contentType: 'text/plain' });
    });
  }

  test('inspirations route follows the configured feature availability', async ({ page }) => {
    await bootstrapAdminSession(page);
    await page.goto('/admin/inspirations');
    await expect.poll(() => page.locator('body').innerText()).toMatch(/Urban fantasy mystery with a deliberately long title|1,289/);
    if (new URL(page.url()).pathname === '/admin') {
      await expect(page.locator('.admin-page')).toBeVisible();
    } else {
      const populatedInspiration = page
        .getByText('Urban fantasy mystery with a deliberately long title', { exact: true })
        .filter({ visible: true });
      await expect(populatedInspiration).toHaveCount(1);
      await expect(populatedInspiration).toBeVisible();
    }
  });

  test('tablet uses compact cards and readable metric widths in the narrowed admin content area', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await bootstrapAdminSession(page);

    for (const path of ['/admin/users', '/admin/codes', '/admin/subscriptions', '/admin/audit-logs', '/admin/feedback']) {
      await page.goto(path);
      await expect(page.locator('.admin-page')).toBeVisible();
      await expect(page.locator('table')).toBeHidden();
    }

    for (const path of ['/admin', '/admin/points', '/admin/check-in', '/admin/quota']) {
      await page.goto(path);
      const metricGrid = page.locator('.admin-page .grid.grid-cols-2').first();
      await expect(metricGrid).toBeVisible();
      const minimumCardWidth = await metricGrid.locator(':scope > *').evaluateAll((cards) =>
        Math.min(...cards.map((card) => card.getBoundingClientRect().width)),
      );
      expect(minimumCardWidth).toBeGreaterThan(150);
    }
  });

  test('representative mobile dialogs and forms fit and keep their actions reachable', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await bootstrapAdminSession(page);

    for (const scenario of [
      { path: '/admin/users', open: () => page.getByRole('button', { name: /^编辑$|^edit$/i }).first().click() },
      { path: '/admin/codes', open: () => page.getByRole('button', { name: /创建|create/i }).first().click() },
      { path: '/admin/plans', open: () => page.getByRole('button', { name: /编辑|edit/i }).first().click() },
      { path: '/admin/audit-logs', open: () => page.getByRole('button', { name: /查看详情|view details/i }).first().click() },
    ]) {
      await page.goto(scenario.path);
      await scenario.open();
      const modal = page.locator('.fixed.inset-0.z-50 > div, [role="dialog"]').last();
      await expect(modal).toBeVisible();
      const finalAction = modal.getByRole('button').last();
      await finalAction.scrollIntoViewIfNeeded();
      await expect(finalAction).toBeVisible();
      const geometry = await modal.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: innerWidth };
      });
      expect(geometry.left).toBeGreaterThanOrEqual(0);
      expect(geometry.right).toBeLessThanOrEqual(geometry.width);
    }
  });
});
