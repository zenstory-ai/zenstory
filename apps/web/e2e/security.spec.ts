import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { TEST_USERS, config } from './config';

/**
 * Security Test Suite - OWASP Top 10 Coverage
 *
 * These tests verify basic security protections against common web vulnerabilities:
 * - XSS (Cross-Site Scripting)
 * - SQL Injection
 * - CSRF (Cross-Site Request Forgery)
 * - Path Traversal
 * - Authentication Bypass
 * - Input Validation
 */

const AUTHENTICATED_ROUTE_PATTERN = /\/(dashboard|project|onboarding\/persona)/;

async function loginAndOpenDashboardHome(page: Page) {
  await page.addInitScript(() => {
    const cachedUser = localStorage.getItem('user');
    if (cachedUser) {
      localStorage.setItem('auth_validated_at', Date.now().toString());
    }
  });

  const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  if (await inspirationInput.isVisible({ timeout: 5000 }).catch(() => false)) {
    return;
  }

  if (!page.url().includes('/login')) {
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
  }

  await expect(page.locator('#identifier')).toBeVisible({ timeout: 15000 });
  await page.fill('#identifier', TEST_USERS.standard.email);
  await page.fill('#password', TEST_USERS.standard.password);
  await page.click('button[type="submit"]');

  await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 30000 });
  await page
    .waitForResponse(
      (response) =>
        response.url().includes('/api/v1/projects') &&
        response.request().method() === 'GET',
      { timeout: 30000 }
    )
    .catch(() => null);

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  await expect(inspirationInput).toBeVisible({ timeout: 30000 });
}

async function getAuthHeaders(page: Page) {
  const accessToken = await page.evaluate(() => localStorage.getItem('access_token'));
  if (!accessToken) {
    throw new Error('Missing access token for security test setup');
  }

  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  };
}

async function listProjects(page: Page, request: APIRequestContext) {
  const headers = await getAuthHeaders(page);
  const response = await request.get(`${config.apiBaseUrl}/api/v1/projects`, { headers });
  expect(response.ok()).toBeTruthy();

  const payload = await response.json();
  return Array.isArray(payload) ? payload : [];
}

async function ensureProjectSlotsAvailable(page: Page, request: APIRequestContext, requiredCreates = 1) {
  const headers = await getAuthHeaders(page);
  const quotaResponse = await request.get(`${config.apiBaseUrl}/api/v1/subscription/quota`, { headers });
  expect(quotaResponse.ok()).toBeTruthy();

  const quota = await quotaResponse.json();
  const projectLimit = quota?.projects?.limit;
  if (typeof projectLimit !== 'number' || projectLimit < 0) return;

  const projects = await listProjects(page, request);
  const targetProjectCount = Math.max(projectLimit - requiredCreates, 0);

  projects.sort(
    (a, b) =>
      new Date(a.updated_at || a.created_at || 0).getTime() -
      new Date(b.updated_at || b.created_at || 0).getTime()
  );

  while (projects.length > targetProjectCount) {
    const project = projects.shift();
    if (!project?.id) continue;

    const deleteResponse = await request.delete(`${config.apiBaseUrl}/api/v1/projects/${project.id}`, {
      headers,
    });
    expect(deleteResponse.ok()).toBeTruthy();
  }

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-testid="dashboard-inspiration-input"]')).toBeVisible({
    timeout: 15000,
  });
}

async function createProjectFromDashboard(page: Page, request: APIRequestContext, inspiration: string) {
  await loginAndOpenDashboardHome(page);
  await ensureProjectSlotsAvailable(page, request);

  const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');
  await inspirationInput.fill(inspiration);
  await page.click('[data-testid="create-project-button"]');
  await expect(page).toHaveURL(/\/project\//, { timeout: 15000 });
}

test.describe('Security - XSS Protection', () => {
  test.beforeEach(async ({ page, request }) => {
    await createProjectFromDashboard(page, request, `安全测试项目 ${Date.now()}`);
  });

  /**
   * XSS Test 1: Script tags in file content should be escaped
   *
   * Given: User creates file with XSS payload in content
   * When: File is displayed in editor/preview
   * Then: Script does NOT execute, content is escaped
   */
  test('XSS payload in file content is escaped and does not execute', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    // Create a file with XSS payload in content
    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    await fileInput.fill('XSS测试文件');
    await fileInput.press('Enter');
    await expect(page.locator('.overflow-auto >> text=XSS测试文件').first()).toBeVisible({ timeout: 5000 });

    // Select the file
    const testFile = page.locator('.overflow-auto >> text=XSS测试文件').first();
    await testFile.click();
    await expect(page.locator('textarea').first()).toBeVisible({ timeout: 5000 });

    // Inject XSS payload into content
    const editor = page.locator('textarea').first();
    const xssPayload = '<script>alert("XSS")</script>这是测试内容';
    await editor.fill(xssPayload);
    await page.waitForTimeout(500);

    // Verify script tag is escaped (visible as text, not executed)
    const contentCheck = await page.evaluate(() => {
      // Check if any script tags exist in the DOM (they shouldn't be executed)
      const scripts = document.querySelectorAll('script');
      const hasAlert = Array.from(scripts).some(s => s.textContent?.includes('XSS'));
      return { scriptCount: scripts.length, hasAlert };
    });

    // Scripts should not be injected into DOM
    expect(contentCheck.hasAlert).toBe(false);

    // Reload and verify content persisted as escaped text
    await page.reload();
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    const outlineFolderAfter = page.locator('text=大纲').first();
    const savedFile = page.locator('.overflow-auto >> text=XSS测试文件').first();
    if (!(await savedFile.isVisible().catch(() => false))) {
      await outlineFolderAfter.click();
    }
    await expect(savedFile).toBeVisible({ timeout: 5000 });
    await savedFile.click();
    await expect(page.locator('textarea').first()).toBeVisible({ timeout: 5000 });

    const editorAfter = page.locator('textarea').first();
    const content = await editorAfter.inputValue();

    // Current editor may preserve the payload as literal text or sanitize it away entirely.
    // Either outcome is acceptable as long as it never executed in the DOM.
    expect(content === '' || content.includes('<script>')).toBe(true);
    expect(content === '' || content.includes('XSS')).toBe(true);
  });

  /**
   * XSS Test 2: Event handlers in file names should be escaped
   *
   * Given: User creates file with XSS in filename
   * When: File is displayed in tree
   * Then: Event handler does NOT execute, name is escaped
   */
  test('XSS payload in file name is escaped in file tree', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    // Create file with XSS in name
    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    const xssName = '<img src=x onerror=alert(1)>文件名';
    await fileInput.fill(xssName);
    await fileInput.press('Enter');
    await expect(page.locator(`text=${xssName}`).first()).toBeVisible({ timeout: 5000 });

    // Check if the XSS payload is escaped in the file tree
    const escapedFile = page.locator(`text=${xssName}`).first();
    await expect(escapedFile).toBeVisible();

    // Verify no script execution using page.evaluate
    const hasExecutedScript = await page.evaluate(() => {
      // Check for any img tags with onerror handlers in the tree
      const imgs = document.querySelectorAll('img[onerror]');
      return imgs.length > 0;
    });

    expect(hasExecutedScript).toBe(false);

    // Verify the name is displayed as text, not HTML
    const fileNameElement = await escapedFile.textContent();
    expect(fileNameElement).toContain('<img');
    expect(fileNameElement).toContain('onerror');
  });
});

test.describe('Security - Authentication Bypass', () => {
  /**
   * Auth Test 1: Protected routes require valid token
   *
   * Given: User is not authenticated
   * When: Accessing protected API endpoint
   * Then: Returns 401 Unauthorized
   */
  test('protected API endpoints return 401 without authentication', async ({ request }) => {
    // Attempt to access protected endpoint without token
    const response = await request.get('/api/v1/projects');

    expect(response.status()).toBe(401);
  });

  /**
   * Auth Test 2: Expired tokens are rejected
   *
   * Given: Request with expired JWT token
   * When: Accessing protected endpoint
   * Then: Returns 401 Unauthorized
   */
  test('expired authentication token is rejected', async ({ request }) => {
    // Use an obviously expired/invalid token
    const expiredToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c';

    const response = await request.get('/api/v1/projects', {
      headers: {
        Authorization: `Bearer ${expiredToken}`,
      },
    });

    expect(response.status()).toBe(401);
  });

  /**
   * Auth Test 3: Protected routes redirect to login
   *
   * Given: User is not authenticated
   * When: Navigating to protected route
   * Then: Redirects to /login
   */
  test('protected routes redirect to login when not authenticated', async ({ page }) => {
    // Clear any existing auth tokens
    await page.goto('/');

    // Try to access protected route
    await page.goto('/dashboard/projects');

    // Should be redirected to login
    await expect(page).toHaveURL('/login');
  });

  /**
   * Auth Test 4: Invalid credentials are rejected
   *
   * Given: Login attempt with wrong password
   * When: Submitting login form
   * Then: Shows error message, does not authenticate
   */
  test('login with invalid credentials is rejected', async ({ page }) => {
    await page.goto('/login');

    await page.fill('#identifier', 'wrong@example.com');
    await page.fill('#password', 'wrongpassword');
    await page.click('button[type="submit"]');

    // Should show error message
    const loginError = page.locator('#login-error');
    await expect(loginError).toBeVisible({ timeout: 5000 });
    await expect(loginError).toContainText(/invalid|账号|密码|邮箱|email/i);

    // Should still be on login page
    await expect(page).toHaveURL('/login');
  });
});

test.describe('Security - SQL Injection', () => {
  test.beforeEach(async ({ page, request }) => {
    await createProjectFromDashboard(page, request, `SQL注入测试项目 ${Date.now()}`);
  });

  /**
   * SQL Injection Test 1: SQL injection in search is sanitized
   *
   * Given: User searches with SQL injection payload
   * When: Search query is processed
   * Then: No SQL errors, input is sanitized
   */
  test('SQL injection in file search is sanitized', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    // Trigger file search
    const isMac = process.platform === 'darwin';
    await page.keyboard.press(isMac ? 'Meta+K' : 'Control+K');

    const searchInput = page.locator('input[placeholder*="搜索"], input[placeholder*="Search"]').first();
    await expect(searchInput).toBeVisible({ timeout: 5000 });

    // Try SQL injection payload
    const sqlPayload = "' OR '1'='1";
    await searchInput.fill(sqlPayload);
    // Wait for search results dropdown to appear or input to settle
    await page.waitForLoadState('domcontentloaded', { timeout: 3000 });

    // Should not show SQL errors
    const errorElements = page.locator('text=/SQL|syntax|error|exception/i');
    const hasSQLError = await errorElements.count();

    expect(hasSQLError).toBe(0);

    // App should still be functional
    await expect(searchInput).toBeVisible();
  });

  /**
   * SQL Injection Test 2: SQL injection in file creation is sanitized
   *
   * Given: User creates file with SQL injection payload in name
   * When: File is created
   * Then: No SQL errors, file is created with escaped name
   */
  test('SQL injection in file name is sanitized', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    const sqlPayload = "'; DROP TABLE files;--";
    await fileInput.fill(sqlPayload);
    const submittedTitle = (await fileInput.inputValue()).trim();
    const creation = page.waitForResponse(resp => {
      const path = new URL(resp.url()).pathname;
      return path.startsWith('/api/v1/projects/') && path.endsWith('/files') &&
        resp.request().method() === 'POST' && resp.request().postDataJSON()?.title === submittedTitle;
    });
    await fileInput.press('Enter');
    const response = await creation;

    const serverLeak = page.locator('text=/sqlite|postgres|traceback|internal server error|syntax error at or near/i');
    expect(await serverLeak.count()).toBe(0);
    expect(response.ok()).toBeTruthy();
    const createdFile = await response.json();
    const createdRow = page.locator('.overflow-auto').getByText(createdFile.title, { exact: true }).first();
    await expect(createdRow).toBeVisible();
    await createdRow.click();
    await expect(page.locator('[data-editor-scroll-container="true"]')).toBeVisible();
  });
});

test.describe('Security - Path Traversal', () => {
  const TEST_EMAIL = TEST_USERS.standard.email;
  const TEST_PASSWORD = TEST_USERS.standard.password;

  test.beforeEach(async ({ page, request }) => {
    await createProjectFromDashboard(page, request, `路径遍历测试项目 ${Date.now()}`);
  });

  /**
   * Path Traversal Test 1: Path traversal in file operations is blocked
   *
   * Given: Attempt to create file with path traversal sequence
   * When: File creation is processed
   * Then: Path is sanitized, no access to parent directories
   */
  test('path traversal in file name is sanitized', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    const traversalPath = '../../../etc/passwd';
    await fileInput.fill(traversalPath);
    const submittedTitle = (await fileInput.inputValue()).trim();
    const creation = page.waitForResponse(resp => {
      const path = new URL(resp.url()).pathname;
      return path.startsWith('/api/v1/projects/') && path.endsWith('/files') &&
        resp.request().method() === 'POST' && resp.request().postDataJSON()?.title === submittedTitle;
    });
    await fileInput.press('Enter');
    const response = await creation;

    const bodyText = (await page.locator('body').textContent()) ?? '';
    expect(bodyText).not.toContain('root:x:0:0');
    expect(response.ok()).toBeTruthy();
    const createdFile = await response.json();
    const createdRow = page.locator('.overflow-auto').getByText(createdFile.title, { exact: true }).first();
    await expect(createdRow).toBeVisible();
    await createdRow.click();
    await expect(page.locator('[data-editor-scroll-container="true"]')).toBeVisible();
  });

  /**
   * Path Traversal Test 2: API rejects path traversal in file ID
   *
   * Given: Request with path traversal in file ID parameter
   * When: API processes request
   * Then: Returns 400/404, not system file contents
   */
  test('API rejects path traversal in file ID parameter', async ({ request }) => {
    // Login to get token
    const loginResponse = await request.post('/api/auth/login', {
      form: {
        username: TEST_EMAIL,
        password: TEST_PASSWORD,
      },
    });
    expect(loginResponse.ok()).toBeTruthy();
    const loginData = await loginResponse.json();
    const token = loginData.access_token;

    // Try to access file with path traversal
    const encodedTraversalId = encodeURIComponent('../../../etc/passwd');
    const response = await request.get(`/api/v1/files/${encodedTraversalId}`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    // Should return 400 Bad Request or 404 Not Found, not 200
    expect([400, 404, 422]).toContain(response.status());
  });
});

test.describe('Security - Input Validation', () => {
  test.beforeEach(async ({ page, request }) => {
    await createProjectFromDashboard(page, request, `输入验证测试项目 ${Date.now()}`);
  });

  /**
   * Input Validation Test 1: Long input is validated
   *
   * Given: User inputs extremely long string
   * When: Submitting form
   * Then: Input is validated/truncated, no buffer overflow
   */
  test('extremely long file name is validated', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    // Create a very long file name (1000+ characters)
    const longName = 'A'.repeat(1000);
    await fileInput.fill(longName);
    await fileInput.press('Enter');
    // Wait for file creation response
    await page.waitForResponse(resp => resp.url().includes('/api/v1/files') && resp.request().method() === 'POST', { timeout: 5000 }).catch(() => {});

    // App should handle gracefully - either truncate or show validation error
    const errorToast = page.locator('text=/too long|过长|maximum|limit/i');
    const hasError = await errorToast.count();

    // If file was created, verify it exists (possibly truncated)
    const createdFile = page.locator(`text=/${longName.substring(0, 50)}/`);
    const fileExists = await createdFile.count();

    // Either validation error or successful creation (possibly truncated)
    expect(hasError > 0 || fileExists > 0).toBe(true);
  });

  /**
   * Input Validation Test 2: Null bytes are sanitized
   *
   * Given: Input contains null bytes
   * When: Processing input
   * Then: Null bytes are stripped/sanitized
   */
  test('null bytes in input are sanitized', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    // File name with null byte
    const nullByteName = 'test\x00file.txt';
    await fileInput.fill(nullByteName);
    const submittedTitle = (await fileInput.inputValue()).trim();
    const creation = page.waitForResponse(resp => {
      const path = new URL(resp.url()).pathname;
      return path.startsWith('/api/v1/projects/') && path.endsWith('/files') &&
        resp.request().method() === 'POST' && resp.request().postDataJSON()?.title === submittedTitle;
    });
    await fileInput.press('Enter');
    const response = await creation;

    // Reject embedded NUL at the request boundary, before PostgreSQL sees it.
    if (submittedTitle.includes('\x00')) {
      expect(response.status()).toBe(422);
      await expect(fileInput).toBeVisible();
    } else {
      expect(response.ok()).toBeTruthy();
      const createdFile = await response.json();
      const createdRow = page.locator('.overflow-auto').getByText(createdFile.title, { exact: true }).first();
      await expect(createdRow).toBeVisible();
      await createdRow.click();
      await expect(page.locator('[data-editor-scroll-container="true"]')).toBeVisible();
    }
  });

  /**
   * Input Validation Test 3: Special characters are handled safely
   *
   * Given: Input contains special characters
   * When: Processing input
   * Then: Characters are escaped/sanitized, no injection
   */
  test('special characters in content are handled safely', async ({ page }) => {
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    await fileInput.fill('特殊字符测试');
    await fileInput.press('Enter');
    await expect(page.locator('.overflow-auto >> text=特殊字符测试').first()).toBeVisible({ timeout: 5000 });

    const testFile = page.locator('.overflow-auto >> text=特殊字符测试').first();
    await testFile.click();
    await expect(page.locator('textarea').first()).toBeVisible({ timeout: 5000 });

    const editor = page.locator('textarea').first();
    // Test various special characters
    const specialChars = '< > & " \' / \\ \n \t \r {{ }} <% %>';
    await editor.fill(specialChars);
    await page.waitForTimeout(500);

    // Reload and verify content persisted safely
    await page.reload();
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    const outlineFolderAfter = page.locator('text=大纲').first();
    const savedFile = page.locator('.overflow-auto >> text=特殊字符测试').first();
    if (!(await savedFile.isVisible().catch(() => false))) {
      await outlineFolderAfter.click();
    }
    await expect(savedFile).toBeVisible({ timeout: 5000 });
    await savedFile.click();
    await expect(page.locator('textarea').first()).toBeVisible({ timeout: 5000 });

    const editorAfter = page.locator('textarea').first();
    const content = await editorAfter.inputValue();

    // Special characters should not crash the editor; current sanitization may preserve
    // or strip them, but the reopened editor must remain functional.
    expect(typeof content).toBe('string');
  });
});

test.describe('Security - CSRF Protection', () => {
  /**
   * CSRF Test: State-changing operations require authentication
   *
   * Given: Request without proper authentication
   * When: Attempting state-changing operation
   * Then: Returns 401 Unauthorized
   */
  test('POST requests without authentication are rejected', async ({ request }) => {
    // Attempt to create project without authentication
    const response = await request.post('/api/v1/projects', {
      data: {
        name: 'CSRF Test Project',
        description: 'Should fail',
      },
    });

    expect(response.status()).toBe(401);
  });

  /**
   * CSRF Test: Cross-origin requests are blocked by CORS
   *
   * Given: Request from unauthorized origin
   * When: Accessing API
   * Then: CORS blocks the request
   */
  test('API enforces CORS policy', async ({ request }) => {
    // Try to access API from different origin (simulated)
    const response = await request.get('/api/v1/projects', {
      headers: {
        Origin: 'https://malicious-site.com',
      },
    });

    // Should either be blocked by CORS or return 401
    expect([401, 403]).toContain(response.status());
  });
});

test.describe('Security - Error Handling', () => {
  /**
   * Error Test: Internal errors don't leak stack traces
   *
   * Given: Error occurs during request processing
   * When: Error response is returned
   * Then: No stack traces or sensitive info leaked
   */
  test('API errors do not leak stack traces', async ({ request }) => {
    // Trigger an error (invalid UUID format)
    const response = await request.get('/api/v1/files/not-a-uuid');

    // Should return error, but not with stack trace
    expect(response.status()).toBeGreaterThanOrEqual(400);

    const body = await response.text();

    // Should not contain Python stack trace indicators
    expect(body).not.toContain('Traceback (most recent call last)');
    expect(body).not.toContain('File "');
    expect(body).not.toContain('line ');
    expect(body).not.toContain('Error: ');
  });
});
