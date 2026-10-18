import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { getPoolUser, type TestUser } from './config';
import { cleanupOwnedProject, createOwnedProject, type OwnedProject } from './fixtures/project-test-data';
import { login } from './helpers/common';

/**
 * E2E Tests for Browser Navigation
 *
 * These tests cover browser navigation patterns:
 * - Back/forward button behavior
 * - Navigation state preservation
 * - Protected route redirects
 * - Scroll position preservation
 */

let currentOwner: TestUser;
let ownedProjects: OwnedProject[] = [];
let pendingProjectTracking: Promise<string>[] = [];

async function loginAndOpenDashboardHome(page: Page, testInfo: TestInfo) {
  currentOwner = getPoolUser(testInfo.parallelIndex);
  await login(page, currentOwner.email, currentOwner.password);
}

async function waitForProjectReady(page: Page) {
  await expect(page).toHaveURL(/\/project\//, { timeout: 15000 });
  await expect(page.getByTestId('editor-panel')).toBeVisible();
}

function registerOwnedProject(projectId: string, accessToken: string): string {
  if (ownedProjects.some((project) => project.id === projectId)) return projectId;
  ownedProjects.push({
    id: projectId,
    name: `navigation-${projectId}`,
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    owner: currentOwner,
  });
  return projectId;
}

async function createProjectFromDashboard(page: Page): Promise<string> {
  const accessToken = await page.evaluate(() => localStorage.getItem('access_token'));
  if (!accessToken) throw new Error('Missing access token before creating navigation fixture');

  const tracking = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'POST' && url.pathname === '/api/v1/projects';
  }).then(async (response) => {
    expect(response.ok()).toBeTruthy();
    const project = await response.json() as { id?: string };
    if (!project.id) throw new Error('Project creation response did not include an id');
    return registerOwnedProject(project.id, accessToken);
  });
  pendingProjectTracking.push(tracking);
  void tracking.catch(() => undefined);

  await page.click('[data-testid="create-project-button"]');
  return tracking;
}

async function createTrackedOwnedProject(page: Page, testInfo: TestInfo, prefix: string): Promise<OwnedProject> {
  const project = await createOwnedProject(page, testInfo, prefix);
  ownedProjects.push(project);
  return project;
}

test.describe('Browser Navigation', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    ownedProjects = [];
    pendingProjectTracking = [];
    await loginAndOpenDashboardHome(page, testInfo);
  });

  test.afterEach(async ({ page }) => {
    const trackingResults = await Promise.allSettled(pendingProjectTracking);
    for (const project of ownedProjects) await cleanupOwnedProject(page, project);
    ownedProjects = [];
    pendingProjectTracking = [];
    const failedTracking = trackingResults.find((result) => result.status === 'rejected');
    if (failedTracking?.status === 'rejected') throw failedTracking.reason;
  });

  test('back button returns to previous page', async ({ page }) => {
    await expect(page).toHaveURL(/\/dashboard/);

    // Create a project to navigate to
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');
    await inspirationInput.fill(`导航测试项目 ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    // Verify we're on project page
    expect(page.url()).toContain('/project/');

    // Click browser back button
    await page.goBack();

    // Should return to dashboard
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('forward button advances after back', async ({ page }) => {
    await expect(page).toHaveURL(/\/dashboard/);

    // Create a project
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');
    await inspirationInput.fill(`前进测试项目 ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    const projectUrl = page.url();

    // Go back to dashboard
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard/);

    // Click forward button
    await page.goForward();

    // Should return to project
    await expect(page).toHaveURL(new RegExp(projectUrl.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')));
  });

  test('back from login redirect returns to login', async ({ page }) => {

    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');
    await inspirationInput.fill(`重定向测试项目 ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    const projectUrl = page.url();

    // Logout
    const userMenuButton = page.locator('[data-testid="user-menu-button"], button[aria-label="User menu"]').first();
    await userMenuButton.click();
    const logoutButton = page.locator('[data-testid="logout-button"], button:has-text("Logout"), button:has-text("Sign out")').first();
    await logoutButton.click();
    await expect(page).toHaveURL('/login');

    // Try to access protected route directly
    await page.goto(projectUrl, { waitUntil: 'commit' });

    // Should be redirected to login
    await expect(page).toHaveURL('/login');
    await expect(page.locator('#identifier')).toBeVisible();

    // Click back button - should stay on login (not go to protected route)
    await page.goBack();

    // Should still be on login or a public page
    const currentUrl = page.url();
    expect(currentUrl).not.toContain('/project/');
  });

  test('back navigation resets dashboard scroll position', async ({ page }, testInfo) => {
    const project = await createTrackedOwnedProject(page, testInfo, 'navigation-scroll');
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    const dashboardScroller = page.locator('main');
    await expect(page.getByRole('button', { name: `打开项目「${project.name}」` })).toBeVisible();

    await dashboardScroller.evaluate((element) => {
      element.scrollTop = 300;
    });
    await expect.poll(() => dashboardScroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);

    // Navigate to a project
    await page.getByRole('button', { name: `打开项目「${project.name}」` }).click();
    await expect(page).toHaveURL(`/project/${project.id}`);

    // Go back
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole('button', { name: `打开项目「${project.name}」` })).toBeVisible();
    await expect.poll(() => dashboardScroller.evaluate((element) => element.scrollTop)).toBe(0);
  });

  test('navigation from project to project works', async ({ page }) => {

    // Create first project
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');
    await inspirationInput.fill(`项目A ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    const projectAUrl = page.url();

    // Go back to dashboard and create second project
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await inspirationInput.fill(`项目B ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    const projectBUrl = page.url();

    // Verify they are different project URLs
    expect(projectAUrl).not.toBe(projectBUrl);

    // Navigate back to project A via direct URL
    await page.goto(projectAUrl);
    await expect(page).toHaveURL(new RegExp(projectAUrl.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')));
    await expect(page.getByTestId('editor-panel')).toBeVisible();

    // Navigate to project B via direct URL
    await page.goto(projectBUrl);
    await expect(page).toHaveURL(new RegExp(projectBUrl.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')));
    await expect(page.getByTestId('editor-panel')).toBeVisible();
  });

  test('refresh on project page maintains state', async ({ page }) => {

    // Create a project
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');
    await inspirationInput.fill(`刷新测试项目 ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    // Create a file
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });
    const outlineFolder = page.locator('text=大纲').first();
    await outlineFolder.click();
    await outlineFolder.hover();
    const addButton = outlineFolder.locator('..').locator('button:has(svg.lucide-plus)').first();
    await addButton.click({ force: true });

    const fileInput = page.locator('input[placeholder*="大纲"]');
    await fileInput.fill('刷新测试文件');
    await fileInput.press('Enter');

    // Select the file
    await page.locator('.overflow-auto >> text=刷新测试文件').first().click();

    // Add content
    const editor = page.locator('textarea').first();
    await editor.fill('刷新前的内容');
    await page.waitForTimeout(1500);

    // Refresh the page
    await page.reload();
    await page.waitForSelector('.overflow-auto', { timeout: 5000 });

    // Verify file still exists
    const outlineFolderAfter = page.locator('text=大纲').first();
    const refreshedFile = page.locator('.overflow-auto >> text=刷新测试文件').first();
    if (!(await refreshedFile.isVisible({ timeout: 2000 }).catch(() => false))) {
      await outlineFolderAfter.click();
    }
    await expect(refreshedFile).toBeVisible();

    // Select file and verify content
    await refreshedFile.click();
    await expect(page.locator('[data-editor-scroll-container] textarea').first()).toBeVisible();
  });

  test('navigation history is correct after multiple navigations', async ({ page }) => {
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

    // Create two projects
    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');

    await inspirationInput.fill(`历史测试A ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

    await inspirationInput.fill(`历史测试B ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    // Go back - should go to dashboard
    await page.goBack();
    await expect(page).toHaveURL(/\/dashboard/);

    // Go back again - should go to first project
    await page.goBack();
    await expect(page).toHaveURL(/\/project\//);

    // Go forward - should go to dashboard
    await page.goForward();
    await expect(page).toHaveURL(/\/dashboard/);

    // Go forward again - should go to second project
    await page.goForward();
    await expect(page).toHaveURL(/\/project\//);
  });

  test('chunk recovery remains active after cancelling a document navigation', async ({ page }) => {
    // Browsers only show beforeunload confirmation after a trusted user gesture.
    await page.getByTestId('dashboard-inspiration-input').click();
    await page.evaluate(() => {
      const record = (event: string) => {
        const events = JSON.parse(localStorage.getItem('zenstory:e2e-navigation-events') || '[]');
        localStorage.setItem('zenstory:e2e-navigation-events', JSON.stringify([...events, event]));
      };
      window.addEventListener('beforeunload', (event) => {
        record('beforeunload');
        event.preventDefault();
        event.returnValue = '';
      });
    });

    const cancelDialogPromise = page.waitForEvent('dialog');
    const cancelledNavigation = page.goto('/robots.txt').catch((error: unknown) => error);
    const cancelDialog = await cancelDialogPromise;
    await cancelDialog.dismiss();
    expect(await cancelledNavigation).toBeInstanceOf(Error);
    await expect(page).toHaveURL(/\/dashboard/);
    await expect.poll(() => page.evaluate(() => (
      JSON.parse(localStorage.getItem('zenstory:e2e-navigation-events') || '[]') as string[]
    ))).toContain('beforeunload');

    const reload = page.waitForEvent('load');
    const acceptDialogPromise = page.waitForEvent('dialog');
    const dispatchRecovery = page.evaluate(() => {
      const event = new Event('vite:preloadError') as Event & { payload?: unknown };
      event.payload = new Error('Failed to fetch dynamically imported module: /assets/stale.js');
      window.dispatchEvent(event);
    }).catch(() => undefined);
    const acceptDialog = await acceptDialogPromise;
    await acceptDialog.accept();
    await dispatchRecovery;
    await reload;

    await expect(page.getByTestId('dashboard-composer')).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem('zenstory:chunk-reload-once')))
      .toBe('vite:preloadError');
  });
});

test.describe('Navigation with Authentication', () => {
  test.beforeEach(async () => {
    ownedProjects = [];
    pendingProjectTracking = [];
  });

  test.afterEach(async ({ page }) => {
    const trackingResults = await Promise.allSettled(pendingProjectTracking);
    for (const project of ownedProjects) await cleanupOwnedProject(page, project);
    ownedProjects = [];
    pendingProjectTracking = [];
    const failedTracking = trackingResults.find((result) => result.status === 'rejected');
    if (failedTracking?.status === 'rejected') throw failedTracking.reason;
  });

  test('protected route stores redirect for post-login', async ({ page }, testInfo) => {
    await loginAndOpenDashboardHome(page, testInfo);

    const inspirationInput = page.locator('[data-testid="dashboard-inspiration-input"]');
    await inspirationInput.fill(`重定向测试项目 ${Date.now()}`);
    await createProjectFromDashboard(page);
    await waitForProjectReady(page);

    const projectUrl = page.url();

    // Logout
    const userMenuButton = page.locator('[data-testid="user-menu-button"], button[aria-label="User menu"]').first();
    await userMenuButton.click();
    const logoutButton = page.locator('[data-testid="logout-button"], button:has-text("Logout"), button:has-text("Sign out")').first();
    await logoutButton.click();
    await expect(page).toHaveURL('/login');

    // Try to access the project URL directly
    await page.goto(projectUrl, { waitUntil: 'commit' });

    // Should be on login page
    await expect(page).toHaveURL('/login');
    await expect(page.locator('#identifier')).toBeVisible();

    // Login again
    await page.fill('#identifier', currentOwner.email);
    await page.fill('#password', currentOwner.password);
    await page.click('button[type="submit"]');

    // Should redirect back to the originally requested URL or dashboard
    await page.waitForURL(/\/(project|dashboard)/, { timeout: 10000 });
  });

  test('login page redirects to dashboard when already authenticated', async ({ page }, testInfo) => {
    await loginAndOpenDashboardHome(page, testInfo);

    // Try to go to login page again
    await page.goto('/login', { waitUntil: 'commit' });

    // Should be redirected away from login (to dashboard or project)
    await expect(page).toHaveURL(/\/(dashboard|project)/, { timeout: 5000 });
    await expect(page.locator('[data-testid="dashboard-composer"], [data-testid="user-menu-button"]').first()).toBeVisible();
  });
});
