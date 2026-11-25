import { expect, test, type Page } from '@playwright/test';

const adminUser = {
  id: 'admin-1',
  username: 'admin',
  email: 'admin@example.com',
  email_verified: true,
  is_active: true,
  is_superuser: true,
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-03-01T00:00:00Z',
};

async function bootstrapAdminSession(page: Page) {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(adminUser),
    });
  });

  await page.route('**/api/auth/me/preferences', async (route) => {
    const payload = route.request().postDataJSON() as { ui_language?: string } | null;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ui_language: payload?.ui_language ?? 'zh' }),
    });
  });

  await page.route('**/api/auth/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        access_token: 'mock-access-token',
        refresh_token: 'mock-refresh-token',
        user: adminUser,
      }),
    });
  });

  await page.route('**/api/v1/projects**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  // The materials library checks the plan before requesting the summary.
  await page.route('**/api/v1/subscription/me', async (route) => {
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

  await page.route('**/api/v1/materials/library-summary**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.addInitScript((user) => {
    localStorage.setItem('access_token', 'mock-access-token');
    localStorage.setItem('refresh_token', 'mock-refresh-token');
    localStorage.setItem('token_type', 'bearer');
    localStorage.setItem('user', JSON.stringify(user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, adminUser);
}

test.describe('Admin moderation flows (mocked)', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    void page;
    test.skip(testInfo.project.name === 'mobile', 'Desktop-focused admin moderation mocked scenarios');
  });

  test('can approve and reject pending skills', async ({ page }) => {
    await bootstrapAdminSession(page);

    let approveCalled = 0;
    let rejectPayload: Record<string, unknown> | null = null;
    let skills = [
      {
        id: 'skill-1',
        name: 'Plot Doctor',
        description: 'Fix plot consistency',
        instructions: 'Use timeline checks',
        category: 'writing',
        author_name: 'alice',
        status: 'pending',
        created_at: '2026-03-08T00:00:00Z',
      },
      {
        id: 'skill-2',
        name: 'Dialogue Refiner',
        description: 'Polish dialogue',
        instructions: 'Improve rhythm',
        category: 'editing',
        author_name: 'bob',
        status: 'pending',
        created_at: '2026-03-08T00:00:00Z',
      },
    ];

    await page.route('**/api/admin/skills/**', async (route) => {
      const request = route.request();
      const { pathname } = new URL(request.url());

      if (request.method() === 'GET' && pathname.endsWith('/api/admin/skills/pending')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(skills),
        });
        return;
      }

      if (request.method() === 'POST' && pathname.endsWith('/api/admin/skills/skill-1/approve')) {
        approveCalled += 1;
        skills = skills.filter((item) => item.id !== 'skill-1');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'ok', skill_id: 'skill-1' }),
        });
        return;
      }

      if (request.method() === 'POST' && pathname.endsWith('/api/admin/skills/skill-2/reject')) {
        rejectPayload = request.postDataJSON() as Record<string, unknown>;
        skills = skills.filter((item) => item.id !== 'skill-2');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'ok', skill_id: 'skill-2' }),
        });
        return;
      }

      await route.continue();
    });

    await page.goto('/admin/skills');
    const skillCards = page.locator('.space-y-4 > div');
    const plotDoctor = skillCards.filter({ has: page.getByRole('heading', { name: 'Plot Doctor', exact: true }) });
    await expect(plotDoctor).toBeVisible();
    await plotDoctor.getByRole('button', { name: /^批准$|^Approve$/i }).click();
    const confirmation = page.getByRole('dialog');
    await expect(confirmation).toBeVisible();
    expect(approveCalled).toBe(0);
    await confirmation.getByRole('button', { name: /批准并公开|Approve and publish|skills\.confirmApprove/i }).click();
    await expect.poll(() => approveCalled).toBe(1);

    await expect(page.getByRole('heading', { name: 'Plot Doctor', exact: true })).toHaveCount(0);
    const dialogueRefiner = skillCards.filter({ has: page.getByRole('heading', { name: 'Dialogue Refiner', exact: true }) });
    await expect(dialogueRefiner).toBeVisible();
    await dialogueRefiner.getByRole('button', { name: /^拒绝$|^Reject$/i }).click();
    await page.locator('textarea').fill('not enough detail');
    await page.getByRole('button', { name: /确认拒绝|confirm reject|skills\.confirmReject/i }).click();

    await expect.poll(() => rejectPayload).not.toBeNull();
    expect(rejectPayload).toEqual({ rejection_reason: 'not enough detail' });
    await expect(page.locator('text=Dialogue Refiner')).toHaveCount(0);
  });

  test('can search feedback and update status', async ({ page }) => {
    await bootstrapAdminSession(page);

    let statusPayload: Record<string, unknown> | null = null;
    const searched: string[] = [];

    await page.route('**/api/admin/feedback**', async (route) => {
      const request = route.request();
      const { pathname, searchParams } = new URL(request.url());

      if (request.method() === 'GET' && pathname.endsWith('/api/admin/feedback')) {
        const keyword = searchParams.get('search');
        if (keyword) searched.push(keyword);
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [
              {
                id: 'fb-1',
                user_id: 'user-1',
                username: 'writer',
                email: 'writer@example.com',
                source_page: 'editor',
                source_route: '/project/1',
                issue_text: 'Save button is not responsive',
                has_screenshot: false,
                screenshot_original_name: null,
                screenshot_size_bytes: null,
                status: 'open',
                created_at: '2026-03-08T00:00:00Z',
                updated_at: '2026-03-08T00:00:00Z',
              },
            ],
            total: 1,
          }),
        });
        return;
      }

      if (request.method() === 'PATCH' && pathname.endsWith('/api/admin/feedback/fb-1/status')) {
        statusPayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'fb-1',
            user_id: 'user-1',
            username: 'writer',
            email: 'writer@example.com',
            source_page: 'editor',
            source_route: '/project/1',
            issue_text: 'Save button is not responsive',
            has_screenshot: false,
            screenshot_original_name: null,
            screenshot_size_bytes: null,
            status: 'processing',
            created_at: '2026-03-08T00:00:00Z',
            updated_at: '2026-03-08T00:00:00Z',
          }),
        });
        return;
      }

      await route.continue();
    });

    await page.goto('/admin/feedback');
    const row = page.locator('table tbody tr', { hasText: 'writer@example.com' }).first();
    await expect(row).toBeVisible();

    await page.getByPlaceholder(/搜索|search/i).fill('writer');
    await page.getByRole('button', { name: /搜索|search/i }).click();
    await expect.poll(() => searched.includes('writer')).toBe(true);

    await row.getByRole('combobox').selectOption('processing');
    await expect.poll(() => statusPayload).toEqual({ status: 'processing' });
  });

  test('applies feedback filters and pagination query params', async ({ page }) => {
    await bootstrapAdminSession(page);

    const feedbackQueries: Array<Record<string, string | null>> = [];

    await page.route('**/api/admin/feedback**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());

      if (request.method() === 'GET' && url.pathname.endsWith('/api/admin/feedback')) {
        const queryRecord = {
          status: url.searchParams.get('status'),
          source_page: url.searchParams.get('source_page'),
          has_screenshot: url.searchParams.get('has_screenshot'),
          search: url.searchParams.get('search'),
          skip: url.searchParams.get('skip'),
          limit: url.searchParams.get('limit'),
        };
        feedbackQueries.push(queryRecord);

        const isSecondPage = queryRecord.skip === '20';
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [
              {
                id: isSecondPage ? 'fb-21' : 'fb-1',
                user_id: 'user-1',
                username: isSecondPage ? 'writer-page-2' : 'writer-page-1',
                email: isSecondPage ? 'writer2@example.com' : 'writer1@example.com',
                source_page: 'editor',
                source_route: '/project/1',
                issue_text: isSecondPage ? 'Second page issue' : 'First page issue',
                has_screenshot: true,
                screenshot_original_name: 'bug.png',
                screenshot_size_bytes: 1024,
                status: 'open',
                created_at: '2026-03-08T00:00:00Z',
                updated_at: '2026-03-08T00:00:00Z',
              },
            ],
            total: 41,
          }),
        });
        return;
      }

      await route.continue();
    });

    await page.goto('/admin/feedback');
    await expect(page.locator('table tbody tr', { hasText: 'writer1@example.com' }).first()).toBeVisible();

    await page.locator('form select').nth(0).selectOption('open');
    await page.locator('form select').nth(1).selectOption('editor');
    await page.locator('form select').nth(2).selectOption('with');
    await page.getByPlaceholder(/搜索|search/i).fill('writer');
    await page.getByRole('button', { name: /搜索|search|common:search/i }).click();

    await expect.poll(() =>
      feedbackQueries.some((query) =>
        query.status === 'open' &&
        query.source_page === 'editor' &&
        query.has_screenshot === 'true' &&
        query.search === 'writer' &&
        query.skip === '0' &&
        query.limit === '20',
      ),
    ).toBe(true);

    await page.getByRole('button', { name: /下一页|下一步|next|common:next/i }).click();

    await expect.poll(() =>
      feedbackQueries.some((query) =>
        query.status === 'open' &&
        query.source_page === 'editor' &&
        query.has_screenshot === 'true' &&
        query.search === 'writer' &&
        query.skip === '20' &&
        query.limit === '20',
      ),
    ).toBe(true);

    await expect(page.locator('table tbody tr', { hasText: 'writer2@example.com' }).first()).toBeVisible();
  });

  test('can open feedback screenshot preview modal', async ({ page }) => {
    await bootstrapAdminSession(page);

    let screenshotRequested = 0;
    const onePixelPng = Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8Xw8AAoMBgQf0A1sAAAAASUVORK5CYII=',
      'base64',
    );

    await page.route('**/api/admin/feedback**', async (route) => {
      const request = route.request();
      const { pathname } = new URL(request.url());

      if (request.method() === 'GET' && pathname.endsWith('/api/admin/feedback')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [
              {
                id: 'fb-1',
                user_id: 'user-1',
                username: 'writer',
                email: 'writer@example.com',
                source_page: 'editor',
                source_route: '/project/1',
                issue_text: 'Screenshot issue',
                has_screenshot: true,
                screenshot_original_name: 'bug.png',
                screenshot_content_type: 'image/png',
                screenshot_size_bytes: 128,
                status: 'open',
                created_at: '2026-03-08T00:00:00Z',
                updated_at: '2026-03-08T00:00:00Z',
              },
            ],
            total: 1,
          }),
        });
        return;
      }

      await route.continue();
    });

    await page.route('**/api/admin/feedback/fb-1/screenshot', async (route) => {
      screenshotRequested += 1;
      await route.fulfill({
        status: 200,
        contentType: 'image/png',
        body: onePixelPng,
      });
    });

    await page.goto('/admin/feedback');
    await page.getByRole('button', { name: /查看截图|view screenshot/i }).click();

    await expect.poll(() => screenshotRequested).toBe(1);
    await expect(page.getByText(/反馈截图预览|screenshot preview|feedback\.screenshotPreviewTitle/i)).toBeVisible();
    await page.getByRole('button', { name: /^关闭$|^close$/i }).click({ force: true });
    await expect(page.getByText(/反馈截图预览|screenshot preview|feedback\.screenshotPreviewTitle/i)).toHaveCount(0);
  });

  test('can switch to chat votes sub-tab and filter by vote', async ({ page }) => {
    await bootstrapAdminSession(page);

    const voteQueries: Array<{ days: string | null; vote: string | null }> = [];

    await page.route('**/api/admin/feedback**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());

      if (request.method() === 'GET' && url.pathname.endsWith('/api/admin/feedback')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ items: [], total: 0 }),
        });
        return;
      }

      if (request.method() === 'GET' && url.pathname.endsWith('/api/admin/feedback/chat-votes')) {
        const vote = url.searchParams.get('vote');
        voteQueries.push({ days: url.searchParams.get('days'), vote });
        const allItems = [
          {
            message_id: 'msg-1',
            user_id: 'user-1',
            username: 'novelist',
            vote: 'down',
            preset: 'too_slow',
            comment: 'Took too long to respond',
            created_at: '2026-03-08T00:00:00Z',
          },
          {
            message_id: 'msg-2',
            user_id: 'user-2',
            username: 'playwright_author',
            vote: 'up',
            preset: null,
            comment: 'Great dialogue pacing',
            created_at: '2026-03-07T00:00:00Z',
          },
        ];
        const filtered = vote && vote !== 'all' ? allItems.filter((item) => item.vote === vote) : allItems;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            days: 7,
            timezone: 'Asia/Shanghai',
            period_start: '2026-03-01T00:00:00Z',
            period_end: '2026-03-08T00:00:00Z',
            summary: {
              up: 1,
              down: 1,
              total_votes: 2,
              up_rate: 0.5,
              preset_counts: [{ preset: 'too_slow', count: 1 }],
            },
            items: filtered,
            total: filtered.length,
            page: 1,
            page_size: 20,
          }),
        });
        return;
      }

      await route.continue();
    });

    await page.goto('/admin/feedback');
    await page.getByRole('button', { name: /^(对话反馈|Chat Votes)$/ }).click();
    await expect(page.getByText('50.0%')).toBeVisible();
    await expect(page.getByText('Took too long to respond')).toBeVisible();
    await expect(page.getByText('Great dialogue pacing')).toBeVisible();

    await page.getByRole('combobox', { name: /^(评价筛选|Vote filter)$/ }).selectOption('down');
    await expect.poll(() => voteQueries.some((q) => q.vote === 'down')).toBe(true);
    await expect(page.getByText('Took too long to respond')).toBeVisible();
    await expect(page.getByText('Great dialogue pacing')).toHaveCount(0);
  });
});
