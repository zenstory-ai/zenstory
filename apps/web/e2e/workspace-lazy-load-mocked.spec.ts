import { test, expect } from '@playwright/test';
import { mockResponsiveApi, responsiveProjects, responsiveUser } from './fixtures-responsive';

// Works with Vite dev and production builds: the workbench must be requested
// by its own lazy route, not embedded in the public router's static imports.
const workspaceModule = /\/(?:src\/pages\/ProjectEditor\.tsx|assets\/ProjectEditor-[^/]+\.js)(?:\?|$)/;

test('public login and rejected project entry defer the workbench until successful sign-in', async ({ page }, info) => {
  const workspaceRequests: string[] = [];
  const errors: string[] = [];
  const baseOrigin = new URL(info.project.use.baseURL!).origin;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (workspaceModule.test(new URL(request.url()).pathname)) workspaceRequests.push(request.url());
  });
  // Local fixture only. In particular, neither model endpoints nor analytics
  // and font hosts can reach a real provider during this regression.
  await page.route('**/*', route => new URL(route.request().url()).origin === baseOrigin
    ? route.fallback()
    : route.abort('blockedbyclient'));
  await mockResponsiveApi(page);
  let loginRequests = 0;
  await page.route('**/api/auth/login', route => {
    loginRequests++;
    return route.fulfill({ json: {
      access_token: 'local-workspace-access', refresh_token: 'local-workspace-refresh',
      token_type: 'bearer', user: responsiveUser,
    } });
  });
  await page.addInitScript(() => localStorage.setItem('zenstory-language', 'zh'));

  await page.goto('/login');
  await expect(page.getByTestId('password-input')).toBeVisible();
  expect(workspaceRequests).toEqual([]);
  await page.goto(`/project/${responsiveProjects[0].id}`);
  await expect(page.getByTestId('password-input')).toBeVisible();
  expect(workspaceRequests).toEqual([]);

  await page.getByTestId('email-input').fill(responsiveUser.email);
  await page.getByTestId('password-input').fill('local-fixture-password');
  await page.getByTestId('login-submit').click();
  await expect(page).toHaveURL(new RegExp(`/project/${responsiveProjects[0].id}$`));
  await expect(page.getByTestId('app-layout')).toBeVisible();
  await expect(page.getByTestId('chat-input')).toBeVisible();
  expect(workspaceRequests.length).toBeGreaterThan(0);
  expect(loginRequests).toBe(1);
  expect(errors).toEqual([]);
  await info.attach('workspace-module-requests', { body: JSON.stringify({ workspaceRequests, loginRequests }), contentType: 'application/json' });
});
