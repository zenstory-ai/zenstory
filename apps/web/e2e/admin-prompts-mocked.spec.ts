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

test.describe('Admin prompts (mocked)', () => {
  test('shows populated prompt cards with localized active and inactive status', async ({ page }) => {
    await bootstrapAdminSession(page);
    await page.route('**/api/admin/prompts', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: ['novel', 'short', 'screenplay'].map((project_type) => ({
            project_type,
            version: 1,
            is_active: project_type !== 'short',
            updated_at: '2026-03-08T00:00:00Z',
          })),
        }),
      });
    });

    await page.goto('/admin/prompts');
    const cards = page.locator('.grid > div');
    await expect(cards).toHaveCount(3);
    const novel = cards.filter({ has: page.getByRole('heading', { name: 'novel', exact: true }) });
    const short = cards.filter({ has: page.getByRole('heading', { name: 'short', exact: true }) });
    await expect(novel.getByText(/^(已启用|Active)$/)).toBeVisible();
    await expect(short.getByText(/^(已禁用|Inactive)$/)).toBeVisible();
  });

  test('can create a new prompt config from editor', async ({ page }) => {
    await bootstrapAdminSession(page);

    let savePayload: Record<string, unknown> | null = null;

    await page.route('**/api/admin/prompts**', async (route) => {
      const request = route.request();
      const { pathname } = new URL(request.url());

      if (request.method() === 'PUT' && pathname.endsWith('/api/admin/prompts/novel')) {
        savePayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            project_type: 'novel',
            version: 2,
            updated_at: '2026-03-08T00:00:00Z',
            ...savePayload,
          }),
        });
        return;
      }

      if (request.method() === 'GET' && pathname.endsWith('/api/admin/prompts')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            items: [
              {
                project_type: 'novel',
                version: 2,
                is_active: true,
                updated_at: '2026-03-08T00:00:00Z',
              },
            ],
          }),
        });
        return;
      }

      await route.continue();
    });

    await page.goto('/admin/prompts/new');
    await expect(page).toHaveURL(/\/admin\/prompts\/new/);

    await page.locator('textarea').first().fill('你是专业小说创作助手');
    await page.locator('textarea').nth(1).fill('结构化大纲能力');
    await page.getByRole('button', { name: /保存|save|promptEditor\.save/i }).click();

    await expect.poll(() => savePayload).not.toBeNull();
    expect(savePayload).toMatchObject({
      role_definition: '你是专业小说创作助手',
      capabilities: '结构化大纲能力',
      is_active: true,
    });
  });

  test('can update and delete an existing prompt config', async ({ page }) => {
    await bootstrapAdminSession(page);

    let savePayload: Record<string, unknown> | null = null;
    let deleteCalled = 0;

    await page.route('**/api/admin/prompts**', async (route) => {
      const request = route.request();
      const { pathname } = new URL(request.url());

      if (request.method() === 'GET' && pathname.endsWith('/api/admin/prompts/novel')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            project_type: 'novel',
            role_definition: '旧角色定义',
            capabilities: '旧能力',
            directory_structure: '旧目录',
            content_structure: '旧内容结构',
            file_types: '*.md',
            writing_guidelines: '旧写作规范',
            include_dialogue_guidelines: false,
            primary_content_type: 'novel',
            is_active: true,
            version: 1,
            updated_at: '2026-03-01T00:00:00Z',
          }),
        });
        return;
      }

      if (request.method() === 'GET' && pathname.endsWith('/api/admin/prompts')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ items: [] }),
        });
        return;
      }

      if (request.method() === 'PUT' && pathname.endsWith('/api/admin/prompts/novel')) {
        savePayload = request.postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            project_type: 'novel',
            version: 2,
            updated_at: '2026-03-08T00:00:00Z',
            ...savePayload,
          }),
        });
        return;
      }

      if (request.method() === 'DELETE' && pathname.endsWith('/api/admin/prompts/novel')) {
        deleteCalled += 1;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'deleted' }),
        });
        return;
      }

      await route.continue();
    });

    await page.goto('/admin/prompts/novel');
    await expect(page).toHaveURL(/\/admin\/prompts\/novel/);
    await expect(page.locator('input[disabled]')).toHaveValue('novel');

    await page.locator('textarea').first().fill('新角色定义');
    await page.getByRole('button', { name: /保存|save|promptEditor\.save/i }).click();

    await expect.poll(() => savePayload).not.toBeNull();
    expect(savePayload).toMatchObject({
      role_definition: '新角色定义',
      is_active: true,
    });

    await page.getByRole('button', { name: /删除|delete|prompts\.delete/i }).first().click();
    const modal = page.locator('.fixed.inset-0.z-50').last();
    await modal.getByRole('button', { name: /确认|confirm|common:confirm/i }).click();

    await expect.poll(() => deleteCalled).toBe(1);
  });
});
