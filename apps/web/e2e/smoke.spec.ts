import { test, expect, Page, APIRequestContext } from '@playwright/test';
import { TEST_USERS } from './config';
import { LoginPage } from './fixtures/page-objects/LoginPage';
import { ChatPanel } from './fixtures/page-objects/ChatPanel';

// Test credentials from environment (following auth.spec.ts pattern)
const TEST_EMAIL = TEST_USERS.standard.email;
const TEST_USERNAME = process.env.E2E_TEST_USERNAME || 'e2e_test_user';
const TEST_PASSWORD = TEST_USERS.standard.password;
const MOCK_INSPIRATION = {
  id: 'smoke-inspiration-1',
  name: 'Smoke Inspiration',
  description: 'Smoke test inspiration template',
  cover_image: null,
  project_type: 'novel',
  tags: ['test'],
  source: 'official',
  author_id: null,
  original_project_id: null,
  copy_count: 1,
  is_featured: false,
  created_at: '2026-03-01T00:00:00Z',
};

async function gotoWithRetry(
  page: Page,
  url: string,
  options: { attempts?: number; timeout?: number } = {}
): Promise<void> {
  const attempts = options.attempts ?? 2;
  const timeout = options.timeout ?? 25000;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
      return;
    } catch (error) {
      if (attempt === attempts) {
        throw error;
      }
      await page.waitForTimeout(400 * attempt);
    }
  }
}

async function createProjectViaApi(
  page: Page,
  name: string,
  options: { allowFallback?: boolean } = {}
): Promise<{ projectId: string; created: boolean }> {
  const token = await page.evaluate(() => localStorage.getItem('access_token'));
  expect(token).toBeTruthy();

  const headers = { Authorization: `Bearer ${token}` };
  const result = await createOrReuseProject(page.request, headers, name, options);
  const { projectId } = result;

  await gotoWithRetry(page, `/project/${projectId}`);
  await expect(page).toHaveURL(/\/project\//, { timeout: 10000 });
  return result;
}

async function loginViaApi(page: Page): Promise<void> {
  const params = new URLSearchParams();
  params.append('username', TEST_EMAIL);
  params.append('password', TEST_PASSWORD);

  const response = await page.request.post('/api/auth/login', {
    data: params.toString(),
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  });
  expect(response.ok()).toBeTruthy();
  const tokens = await response.json();

  await gotoWithRetry(page, '/');
  await page.evaluate((tokenData) => {
    localStorage.setItem('access_token', tokenData.access_token);
    localStorage.setItem('refresh_token', tokenData.refresh_token);
    localStorage.setItem('token_type', tokenData.token_type);
    localStorage.setItem('user', JSON.stringify(tokenData.user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, tokens);
}

async function createOrReuseProject(
  request: APIRequestContext,
  headers: Record<string, string>,
  name: string,
  options: { allowFallback?: boolean } = {}
): Promise<{ projectId: string; created: boolean }> {
  const allowFallback = options.allowFallback ?? true;

  const createResponse = await request.post('/api/v1/projects', {
    headers,
    data: {
      name,
      project_type: 'novel',
    },
  });

  if (createResponse.ok()) {
    const payload = await createResponse.json();
    const projectId = payload.id as string;
    expect(projectId).toBeTruthy();
    return { projectId, created: true };
  }

  if (!allowFallback) {
    expect(createResponse.ok()).toBeTruthy();
  }

  const listResponse = await request.get('/api/v1/projects', { headers });
  expect(listResponse.ok()).toBeTruthy();
  const existingProjects = (await listResponse.json()) as Array<{ id: string }>;
  expect(Array.isArray(existingProjects)).toBeTruthy();
  expect(existingProjects.length).toBeGreaterThan(0);
  return { projectId: existingProjects[0].id, created: false };
}

async function ensureProjectOpened(page: Page): Promise<void> {
  if (page.url().includes('/project/')) {
    return;
  }

  const pathname = new URL(page.url()).pathname;
  if (!/^\/dashboard\/?$/.test(pathname)) {
    await gotoWithRetry(page, '/dashboard');
    await expect(page).toHaveURL(/\/dashboard\/?$/, { timeout: 10000 });
  }

  const projectCards = page.getByTestId('project-card');
  if (await projectCards.count()) {
    await projectCards.first().click();
    await expect(page).toHaveURL(/\/project\//, { timeout: 10000 });
    return;
  }

  await createProjectViaApi(page, `Smoke seed ${Date.now()}`);
}

// Configure faster timeouts for smoke tests
test.describe.configure({ mode: 'parallel', timeout: 20000 });

test.describe('Smoke Tests - Critical Path', () => {
  test.describe('API Core Flow', () => {
    test('project create -> file update -> version recorded', async ({ request }) => {
      const loginCandidates = [
        { username: TEST_USERNAME, password: TEST_PASSWORD },
        { username: TEST_EMAIL, password: TEST_PASSWORD },
      ];
      let token = '';

      for (const candidate of loginCandidates) {
        const loginResponse = await request.post('/api/auth/login', {
          form: { username: candidate.username, password: candidate.password },
        });
        if (loginResponse.ok()) {
          token = (await loginResponse.json()).access_token;
          break;
        }
      }

      expect(token).toBeTruthy();
      const headers = { Authorization: `Bearer ${token}` };

      // Keep this flow on regular-user permissions while avoiding quota-induced false negatives.
      const existingProjectsResponse = await request.get('/api/v1/projects', { headers });
      expect(existingProjectsResponse.ok()).toBeTruthy();
      const existingProjects = (await existingProjectsResponse.json()) as Array<{ id: string }>;
      for (const project of existingProjects) {
        await request.delete(`/api/v1/projects/${project.id}`, { headers });
      }

      const projectName = `Smoke API Project ${Date.now()}`;
      const { projectId, created: createdProject } = await createOrReuseProject(request, headers, projectName, {
        allowFallback: false,
      });
      expect(projectId).toBeTruthy();

      const fileResponse = await request.post(`/api/v1/projects/${projectId}/files`, {
        data: {
          title: `Smoke API File ${Date.now()}`,
          file_type: 'draft',
          content: 'initial content',
        },
        headers,
      });
      expect(fileResponse.ok()).toBeTruthy();
      const file = await fileResponse.json();
      const fileId = file.id as string;
      expect(fileId).toBeTruthy();

      const updateResponse = await request.put(`/api/v1/files/${fileId}`, {
        data: {
          content: 'updated content from smoke test',
        },
        headers,
      });
      expect(updateResponse.ok()).toBeTruthy();

      const versionsResponse = await request.get(`/api/v1/files/${fileId}/versions`, {
        headers,
      });
      expect(versionsResponse.ok()).toBeTruthy();
      const versionsData = await versionsResponse.json();
      expect(versionsData.total).toBeGreaterThanOrEqual(1);
      expect(versionsData.versions[0]?.change_type).toBe('edit');
      expect(versionsData.versions[0]?.change_source).toBe('user');

      const aiReviewedUpdateResponse = await request.put(`/api/v1/files/${fileId}`, {
        data: {
          content: 'reviewed content from smoke test',
          change_type: 'ai_edit',
          change_source: 'ai',
          change_summary: 'AI edit (reviewed)',
        },
        headers,
      });
      expect(aiReviewedUpdateResponse.ok()).toBeTruthy();

      const versionsAfterAiReviewResponse = await request.get(`/api/v1/files/${fileId}/versions`, {
        headers,
      });
      expect(versionsAfterAiReviewResponse.ok()).toBeTruthy();
      const versionsAfterAiReview = await versionsAfterAiReviewResponse.json();
      expect(versionsAfterAiReview.total).toBeGreaterThanOrEqual(2);
      expect(versionsAfterAiReview.versions[0]?.change_type).toBe('ai_edit');
      // A browser-originated save is persisted by the authenticated user even
      // when the content came from an accepted AI suggestion. `change_type`
      // retains the content origin; `change_source` is the trusted actor.
      expect(versionsAfterAiReview.versions[0]?.change_source).toBe('user');

      if (createdProject) {
        await request.delete(`/api/v1/projects/${projectId}`, { headers });
      }
    });
  });

  test.describe('Authentication', () => {
    test('user can log in', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.login(TEST_EMAIL, TEST_PASSWORD);

      // Verify redirect to dashboard or project page
      await expect(page).toHaveURL(/\/(dashboard|project)/, { timeout: 10000 });
    });

    test('login form is accessible', async ({ page }) => {
      await gotoWithRetry(page, '/login');

      // Verify login form elements are visible
      await expect(page.getByTestId('login-form')).toBeVisible();
      await expect(page.getByTestId('email-input')).toBeVisible();
      await expect(page.getByTestId('password-input')).toBeVisible();
      await expect(page.getByTestId('login-submit')).toBeVisible();
    });

    test('public home header exposes a direct subscription entry', async ({ page }) => {
      await gotoWithRetry(page, '/');
      await expect(page).toHaveURL(/\/$/, { timeout: 10000 });

      const subscriptionEntry = page.getByRole('link', { name: /(订阅|Pricing|Subscription)/i }).first();
      await expect(subscriptionEntry).toBeVisible({ timeout: 10000 });
      await subscriptionEntry.click();

      await expect(page).toHaveURL(/\/pricing/, { timeout: 10000 });
    });

    test('protected routes redirect to login when not authenticated', async ({ page }) => {
      // Try to access protected route directly
      await gotoWithRetry(page, '/projects');

      // Should be redirected to public entry or login
      await expect(page).toHaveURL(/\/(login)?$/, { timeout: 10000 });
    });
  });

  test.describe('Dashboard Navigation', () => {
    test('legacy lab entry is hidden', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);
      await gotoWithRetry(page, '/dashboard');
      await expect(page).toHaveURL(/\/dashboard\/?$/, { timeout: 10000 });

      await expect(page.getByRole('button', { name: /^(实验室|Lab)$/i })).toHaveCount(0);
    });

    test('user can open billing center from dashboard sidebar', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);
      await gotoWithRetry(page, '/dashboard');
      await expect(page).toHaveURL(/\/dashboard\/?$/, { timeout: 10000 });

      const billingEntry = page.getByRole('button', { name: /(订阅|权益|Billing)/i }).first();
      await expect(billingEntry).toBeVisible({ timeout: 10000 });
      await billingEntry.click();

      await expect(page).toHaveURL(/\/dashboard\/billing/, { timeout: 10000 });
      await expect(page.getByText(/(订阅权益|Plans & Benefits|套餐权益)/i).first()).toBeVisible({ timeout: 10000 });
    });

    test('inspiration quota modal primary CTA routes to billing with source tracking', async ({ page }) => {
      test.setTimeout(45000);

      await page.route('**/api/v1/inspirations/*/copy', async (route) => {
        await route.fulfill({
          status: 402,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED' }),
        });
      });

      await page.route('**/api/v1/inspirations*', async (route) => {
        const request = route.request();
        if (request.method() !== 'GET') {
          await route.continue();
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            inspirations: [MOCK_INSPIRATION],
            total: 1,
            page: 1,
            page_size: 12,
          }),
        });
      });

      await loginViaApi(page);
      await gotoWithRetry(page, '/dashboard/inspirations');
      await expect(page).toHaveURL(/\/dashboard\/inspirations/, { timeout: 10000 });

      const useButton = page.getByRole('button', { name: /创建项目|Create project/i }).first();
      await expect(useButton).toBeVisible({ timeout: 10000 });
      await useButton.click();

      await expect(page.getByText(/本月灵感使用次数已用完|limit reached/i).first()).toBeVisible({ timeout: 10000 });

      const upgradeButton = page.getByRole('button', { name: /升级套餐|Upgrade/i }).first();
      await upgradeButton.click();

      await expect(page).toHaveURL(/\/dashboard\/billing\?source=inspiration_copy_quota_blocked/, {
        timeout: 10000,
      });
    });
  });

  test.describe('Project Management', () => {
    test('user can create a project', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);

      const { created } = await createProjectViaApi(page, `Smoke Test Project ${Date.now()}`, {
        allowFallback: false,
      });
      expect(created).toBeTruthy();
      await expect(page).toHaveURL(/\/project\//, { timeout: 15000 });
    });

    test('user can navigate to an existing project', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);

      await ensureProjectOpened(page);
      await expect(page).toHaveURL(/\/project\//, { timeout: 10000 });
    });
  });

  test.describe('File Operations', () => {
    test('user can view file tree', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);

      await ensureProjectOpened(page);

      // Verify file tree is visible
      const fileSearch = page
        .locator('[role="searchbox"], input[placeholder*="搜索文件"], input[placeholder*="Search files"]')
        .first();
      await expect(fileSearch).toBeVisible({ timeout: 10000 });
    });

    test('user can create a file', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);

      await ensureProjectOpened(page);

      const token = await page.evaluate(() => localStorage.getItem('access_token'));
      expect(token).toBeTruthy();

      const projectMatch = page.url().match(/\/project\/([^/?#]+)/);
      expect(projectMatch?.[1]).toBeTruthy();
      const projectId = projectMatch![1];

      const fileName = `Smoke UI File ${Date.now()}`;
      const createFileResponse = await page.request.post(`/api/v1/projects/${projectId}/files`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
        data: {
          title: fileName,
          file_type: 'draft',
          content: '',
        },
      });
      expect(createFileResponse.ok()).toBeTruthy();

      // Refresh to ensure the latest tree data is rendered
      await gotoWithRetry(page, `/project/${projectId}`);
      await expect(page).toHaveURL(/\/project\//, { timeout: 10000 });

      // Verify file tree remains interactive and file is visible
      const fileSearch = page
        .locator('[role="searchbox"], input[placeholder*="搜索文件"], input[placeholder*="Search files"]')
        .first();
      await expect(fileSearch).toBeVisible({ timeout: 10000 });
      await expect(page.locator(`text=${fileName}`).first()).toBeVisible({ timeout: 10000 });
    });
  });

  test.describe('AI Chat', () => {
    test('user can send a chat message', async ({ page }) => {
      const loginPage = new LoginPage(page);
      const chatPanel = new ChatPanel(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);

      await ensureProjectOpened(page);

      // Wait for chat panel to be ready
      await chatPanel.waitForChatReady();

      // Send message
      await chatPanel.sendMessage('Smoke test message');

      // Verify input cleared (message sent)
      await expect(page.getByTestId('chat-input')).toHaveValue('', { timeout: 10000 });
    });

    test('chat panel is accessible', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);

      await ensureProjectOpened(page);

      // Verify chat panel elements
      await expect(page.getByTestId('chat-panel')).toBeVisible({ timeout: 10000 });
      await expect(page.getByTestId('chat-input')).toBeVisible();
    });
  });

  test.describe('Export', () => {
    test('export button is available in project view', async ({ page }) => {
      const loginPage = new LoginPage(page);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(TEST_EMAIL, TEST_PASSWORD);

      await ensureProjectOpened(page);

      // Verify export button exists and is enabled
      const exportButton = page.getByRole('button', { name: /Export|导出/i }).first();
      await expect(exportButton).toBeVisible({ timeout: 10000 });
      await expect(exportButton).toBeEnabled();
    });
  });
});
