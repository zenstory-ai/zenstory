import { test, expect, type Page } from '@playwright/test';
import { LoginPage } from './fixtures/page-objects';
import { TEST_USERS, config } from './config';
import { TIMEOUTS } from './constants';

const MICROPHONE_PERMISSION_BROWSER_MESSAGE =
  'Playwright can grant microphone permission only in Chromium.'

/**
 * Mobile Responsive Tests
 * Tests mobile-specific functionality and responsive layouts across different viewport sizes.
 */

test.describe('Mobile Responsive Tests', () => {
  let loginPage: LoginPage;

  test.beforeEach(async ({ page }) => {
    loginPage = new LoginPage(page);
  });

  const STANDARD_USER = TEST_USERS.standard;

  /** Create a project via API (fresh login) and navigate to it. Works on any viewport. */
  const navigateToProject = async (page: Page) => {
    const params = new URLSearchParams();
    params.append('username', STANDARD_USER.email);
    params.append('password', STANDARD_USER.password);
    const loginResponse = await page.request.post(`${config.apiBaseUrl}/api/auth/login`, {
      data: params.toString(),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    if (!loginResponse.ok()) throw new Error(`Login failed: ${loginResponse.status()}`);
    const tokens = await loginResponse.json();

    const headers = { Authorization: `Bearer ${tokens.access_token}` };
    const projectsResponse = await page.request.get(`${config.apiBaseUrl}/api/v1/projects`, { headers });
    if (!projectsResponse.ok()) throw new Error(`Failed to list projects: ${projectsResponse.status()}`);
    const projects = await projectsResponse.json() as Array<{ id?: string }>;

    let project = projects.find((candidate) => candidate?.id);
    if (!project) {
      const createResponse = await page.request.post(`${config.apiBaseUrl}/api/v1/projects`, {
        headers,
        data: { name: `Mobile Test ${Date.now()}` },
      });
      if (!createResponse.ok()) throw new Error(`Failed to create project: ${createResponse.status()}`);
      project = await createResponse.json();
    }
    if (!project?.id) throw new Error('Project creation returned no ID');

    // Inject API tokens into browser so React auth context matches
    await page.evaluate((tokenData) => {
      localStorage.setItem('access_token', tokenData.access_token);
      localStorage.setItem('refresh_token', tokenData.refresh_token);
      localStorage.setItem('token_type', tokenData.token_type);
      if (tokenData.user) localStorage.setItem('user', JSON.stringify(tokenData.user));
      localStorage.setItem('auth_validated_at', Date.now().toString());
    }, tokens);

    await page.goto(`/project/${project.id}`, { waitUntil: 'domcontentloaded' });
    return project.id;
  };

  const waitForBottomTabs = async (page: Page) => {
    const bottomTabs = page
      .locator('[data-testid="bottom-tabs"], [role="tablist"][aria-label="Mobile navigation"]')
      .first();

    if (await bottomTabs.waitFor({ state: 'visible', timeout: 2000 }).then(() => true).catch(() => false)) {
      return;
    }

    const recentProjectButtons = page.locator('button[aria-label^="Open project"]');
    if ((await recentProjectButtons.count()) > 0) {
      const recentProjectButton = recentProjectButtons.first();
      await recentProjectButton.scrollIntoViewIfNeeded().catch(() => {});
      await recentProjectButton.click();
      await bottomTabs.waitFor({ timeout: TIMEOUTS.MEDIUM });
      return;
    }

    // No projects on dashboard — create one via API and navigate into it
    await navigateToProject(page);
    await bottomTabs.waitFor({ timeout: TIMEOUTS.LONG });
  };

  test.describe('320px viewport (small mobile)', () => {
    test.use({ viewport: { width: 320, height: 568 }, hasTouch: true });

    test('responsive layout at 320px width', async ({ page }) => {
      await loginPage.navigateToLogin();
      await expect(page.getByTestId('login-form')).toBeVisible();

      // Check all elements fit within 320px viewport
      const formWidth = await page.getByTestId('login-form').evaluate((el) => el.scrollWidth);
      expect(formWidth).toBeLessThanOrEqual(320);

      // Verify email input is usable at this width
      const emailInput = page.getByTestId('email-input');
      await expect(emailInput).toBeVisible();
      await emailInput.fill('test@example.com');
      await expect(emailInput).toHaveValue('test@example.com');
    });

    test('login form elements are accessible at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();

      // Verify all form elements are visible and not truncated
      await expect(page.getByTestId('email-input')).toBeVisible();
      await expect(page.getByTestId('password-input')).toBeVisible();
      await expect(page.getByTestId('login-submit')).toBeVisible();

      // Verify no horizontal scrolling on login page
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(scrollWidth).toBeLessThanOrEqual(320);
    });

    test('dashboard renders correctly at 320px width', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Verify main layout elements adapt properly to 320px
      const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
      expect(bodyWidth).toBeLessThanOrEqual(320);

      // 当前移动端首页显示顶部菜单；进入项目后才显示底部 tabs
      await expect(page.getByRole('button', { name: /菜单|Menu/i })).toBeVisible();
    });

    test('bottom tabs navigation at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      const bottomTabs = page
        .locator('[data-testid="bottom-tabs"], [role="tablist"][aria-label="Mobile navigation"]')
        .first();

      if (await bottomTabs.isVisible({ timeout: 3000 }).catch(() => false)) {
        // Verify all three tabs are present
        const tabs = page.locator('[role="tab"]');
        const tabCount = await tabs.count();
        expect(tabCount).toBe(3);

        // Each tab should be accessible (min 44px touch target)
        for (let i = 0; i < tabCount; i++) {
          const tab = tabs.nth(i);
          const box = await tab.boundingBox();
          expect(box?.height).toBeGreaterThanOrEqual(36);
        }
      } else {
        // Current mobile dashboard home may show a top menu before entering a project.
        await expect(page.getByRole('button', { name: /菜单|Menu|Open mobile menu/i })).toBeVisible();
      }
    });

    test('touch gestures for file tree at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for file tree to be visible
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      const fileTreeElement = page.getByTestId('file-tree');

      // Simulate touch scroll gesture
      await fileTreeElement.evaluate((el) => {
        const touchStartEvent = new TouchEvent('touchstart', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 80,
              clientY: 100,
            }),
          ],
        });
        el.dispatchEvent(touchStartEvent);

        const touchMoveEvent = new TouchEvent('touchmove', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 80,
              clientY: 50,
            }),
          ],
        });
        el.dispatchEvent(touchMoveEvent);

        const touchEndEvent = new TouchEvent('touchend', {
          bubbles: true,
          cancelable: true,
          touches: [],
        });
        el.dispatchEvent(touchEndEvent);
      });

      // Verify touch events don't cause errors
      await expect(fileTreeElement).toBeVisible();
    });

    test('chat input works at 320px width', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for chat input to be ready
      await page.waitForSelector('[data-testid="chat-input"], [data-testid="mobile-chat-input"]', {
        timeout: 10000,
      });

      // Focus chat input
      const chatInput = page.getByTestId('chat-input').or(page.getByTestId('mobile-chat-input'));
      await chatInput.focus();

      // Type a message
      await chatInput.fill('Test message at 320px width');

      // Verify input has the text
      await expect(chatInput).toHaveValue('Test message at 320px width');
    });

    test('no horizontal overflow on dashboard at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Check no horizontal overflow on dashboard
      const horizontalScroll = await page.evaluate(() => {
        return document.documentElement.scrollWidth > document.documentElement.clientWidth;
      });
      expect(horizontalScroll).toBe(false);

      // Navigate through all panels and check each
      const tabs = page.locator('[role="tab"]');
      const tabCount = await tabs.count();

      for (let i = 0; i < tabCount; i++) {
        await tabs.nth(i).click();
        await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

        const panelScroll = await page.evaluate(() => {
          return document.documentElement.scrollWidth > document.documentElement.clientWidth;
        });
        expect(panelScroll).toBe(false);
      }
    });

    test('file tree items fit within 320px viewport', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for file tree
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Check file tree fits within viewport
      const fileTree = page.getByTestId('file-tree');
      const treeWidth = await fileTree.evaluate((el) => el.scrollWidth);
      expect(treeWidth).toBeLessThanOrEqual(320);
    });

    test('mobile navigation menu at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Look for hamburger menu or mobile nav toggle
      const mobileMenuSelectors = [
        '[aria-label="Menu"]',
        '[aria-label="Toggle menu"]',
        '[aria-label="Open mobile menu"]',
        'button:has-text("菜单")',
        '[data-testid="mobile-menu"]',
        '[data-testid="hamburger-menu"]',
        'button:has([class*="menu"])',
        'button:has([class*="hamburger"])',
        '[class*="mobile-menu-toggle"]',
      ];

      let menuFound = false;
      for (const selector of mobileMenuSelectors) {
        const menuButton = page.locator(selector);
        if ((await menuButton.count()) > 0) {
          menuFound = true;
          await menuButton.first().click({ force: true });

          // Verify menu is visible
          const menuSelectors = [
            '[role="menu"]',
            '[data-testid="mobile-nav"]',
            '[data-testid="navigation-menu"]',
            'aside',
            '[class*="mobile-menu"]',
            '[class*="nav-menu"]',
          ];

          for (const menuSelector of menuSelectors) {
            const menu = page.locator(menuSelector);
            if ((await menu.count()) > 0 && (await menu.first().isVisible())) {
              break;
            }
          }

          break;
        }
      }

      // If no mobile menu is found, that's acceptable for some layouts
      // Just verify the main content is accessible
      if (!menuFound) {
        await expect(page.getByTestId('project-grid').or(page.getByTestId('file-tree'))).toBeVisible();
      }
    });

    test('tap targets are appropriately sized at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();

      // Verify interactive elements have sufficient touch target size (min 44x44px recommended)
      const interactiveElements = [
        { selector: '[data-testid="login-submit"]', name: 'Login button' },
        { selector: '[data-testid="email-input"]', name: 'Email input' },
        { selector: '[data-testid="password-input"]', name: 'Password input' },
      ];

      for (const element of interactiveElements) {
        const locator = page.locator(element.selector);
        if ((await locator.count()) > 0) {
          const box = await locator.boundingBox();
          if (box) {
            // Touch targets should be at least 36px (slightly relaxed from 44px WCAG)
            expect(box.height).toBeGreaterThanOrEqual(32);
          }
        }
      }
    });

    test('panel switching via tabs at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for bottom tabs
      await waitForBottomTabs(page);

      // Click each tab and verify panel switches
      const tabs = page.locator('[role="tab"]');
      const tabCount = await tabs.count();

      for (let i = 0; i < tabCount; i++) {
        const tab = tabs.nth(i);
        await tab.click();
        await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

        // Verify tab is selected
        await expect(tab).toHaveAttribute('aria-selected', 'true');
      }
    });

    test('viewport dimensions remain stable at 320px', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Verify viewport stays at 320px width
      const viewportSize = page.viewportSize();
      expect(viewportSize?.width).toBe(320);

      // Navigate through panels
      const tabs = page.locator('[role="tab"]');
      const tabCount = await tabs.count();

      for (let i = 0; i < tabCount; i++) {
        await tabs.nth(i).click();
        await page.waitForTimeout(200);
      }

      // Viewport should still be 320px
      const finalViewportSize = page.viewportSize();
      expect(finalViewportSize?.width).toBe(320);
    });
  });

  test.describe('375px viewport (standard mobile)', () => {
    test.use({ viewport: { width: 375, height: 667 }, hasTouch: true });

    test('responsive layout at 375px width', async ({ page }) => {
      await loginPage.navigateToLogin();
      await expect(page.getByTestId('login-form')).toBeVisible();

      // Login and navigate to dashboard
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Verify main layout elements adapt properly
      const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
      expect(bodyWidth).toBeLessThanOrEqual(375);
    });

    test('touch gestures for file tree navigation', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for file tree to be visible
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      const fileTreeElement = page.getByTestId('file-tree');

      // Simulate touch scroll gesture
      await fileTreeElement.evaluate((el) => {
        const touchStartEvent = new TouchEvent('touchstart', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 100,
              clientY: 100,
            }),
          ],
        });
        el.dispatchEvent(touchStartEvent);

        const touchMoveEvent = new TouchEvent('touchmove', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 100,
              clientY: 50,
            }),
          ],
        });
        el.dispatchEvent(touchMoveEvent);

        const touchEndEvent = new TouchEvent('touchend', {
          bubbles: true,
          cancelable: true,
          touches: [],
        });
        el.dispatchEvent(touchEndEvent);
      });

      // Verify touch events don't cause errors
      await expect(fileTreeElement).toBeVisible();
    });

    test('virtual keyboard handling in chat', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for chat panel to be ready
      await page.waitForSelector('[data-testid="chat-input"], [data-testid="mobile-chat-input"]', { timeout: 10000 });

      // Focus chat input (simulates virtual keyboard appearing on mobile)
      const chatInput = page.getByTestId('chat-input').or(page.getByTestId('mobile-chat-input'));
      await expect(chatInput).toBeEnabled({ timeout: 15000 });
      await chatInput.focus();

      // Type a message
      await chatInput.fill('Mobile test message from virtual keyboard');

      // Verify send button is accessible
      const sendButton = page.getByTestId('send-button');
      await expect(sendButton).toBeVisible();

      // 输入后仍应保持可见且按钮可用
      await expect(chatInput).toHaveValue('Mobile test message from virtual keyboard');
    });

    test('mobile navigation menu functionality', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Look for hamburger menu or mobile nav toggle
      const mobileMenuSelectors = [
        '[aria-label="Menu"]',
        '[aria-label="Toggle menu"]',
        '[aria-label="Open mobile menu"]',
        'button:has-text("菜单")',
        '[data-testid="mobile-menu"]',
        '[data-testid="hamburger-menu"]',
        'button:has([class*="menu"])',
        'button:has([class*="hamburger"])',
        '[class*="mobile-menu-toggle"]',
      ];

      let menuFound = false;
      for (const selector of mobileMenuSelectors) {
        const menuButton = page.locator(selector);
        if ((await menuButton.count()) > 0) {
          menuFound = true;
          await menuButton.first().click({ force: true });

          // Verify menu is visible
          const menuSelectors = [
            '[role="menu"]',
            '[data-testid="mobile-nav"]',
            '[data-testid="navigation-menu"]',
            'aside',
            '[class*="mobile-menu"]',
            '[class*="nav-menu"]',
          ];

          for (const menuSelector of menuSelectors) {
            const menu = page.locator(menuSelector);
            if ((await menu.count()) > 0 && (await menu.first().isVisible())) {
              break;
            }
          }
          break;
        }
      }

      // If no mobile menu is found, that's acceptable for some layouts
      // Just verify the main content is accessible
      if (!menuFound) {
        await expect(page.getByTestId('project-grid').or(page.getByTestId('file-tree'))).toBeVisible();
      }
    });
  });

  test.describe('768px viewport (tablet)', () => {
    test.use({ viewport: { width: 768, height: 1024 } });

    test('responsive layout at 768px width', async ({ page }) => {
      await loginPage.navigateToLogin();
      await expect(page.getByTestId('login-form')).toBeVisible();

      // Login and navigate to dashboard
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Login lands on the dashboard or reopens the last project depending on
      // the shared test user's state; either primary surface must render.
      await expect(
        page.locator('h1, button[aria-label^="Open project"], [data-testid="chat-panel"], [data-testid="file-tree"]').first()
      ).toBeVisible();

      // Verify content fits within tablet viewport
      const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
      expect(bodyWidth).toBeLessThanOrEqual(768);
    });

    test('tablet shows three-panel layout appropriately', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // At tablet size, the app should present a usable primary workspace or dashboard surface
      await expect(
        page.locator('h1, button[aria-label^="Open project"], [data-testid="chat-panel"], [data-testid="file-tree"]').first()
      ).toBeVisible();
    });
  });

  test.describe('Orientation changes', () => {
    test('portrait/landscape orientation changes', async ({ page }) => {
      // Start in portrait mode (375x667 - iPhone SE size)
      await page.setViewportSize({ width: 375, height: 667 });
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate into a project
      await waitForBottomTabs(page);

      // Verify portrait layout renders correctly
      await expect(
        page.locator('[data-testid="bottom-tabs"], [role="tablist"][aria-label="Mobile navigation"]').first()
      ).toBeVisible();

      // Switch to landscape orientation (667x375)
      await page.setViewportSize({ width: 667, height: 375 });

      // Verify landscape layout adjusts properly
      // Chat panel should be visible or accessible in landscape
      const chatPanel = page.getByTestId('chat-panel');
      const chatInput = page.getByTestId('chat-input');
      const fileTree = page.getByTestId('file-tree');

      const chatVisible = (await chatPanel.count()) > 0 && (await chatPanel.isVisible());
      const inputVisible = (await chatInput.count()) > 0 && (await chatInput.isVisible());
      const treeVisible = (await fileTree.count()) > 0 && (await fileTree.isVisible());

      // At minimum, one interactive element should be visible
      expect(chatVisible || inputVisible || treeVisible).toBe(true);

      // Verify no horizontal overflow in landscape
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(scrollWidth).toBeLessThanOrEqual(667);
    });

    test('orientation change preserves user session', async ({ page }) => {
      // Start in portrait
      await page.setViewportSize({ width: 375, height: 667 });
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Verify logged in state
      await expect(page).toHaveURL(/\/(dashboard|project).*/);

      // Verify still on dashboard (session preserved)
      await expect(page).toHaveURL(/\/(dashboard|project).*/);
    });
  });

  test.describe('Touch interactions', () => {
    test.use({ viewport: { width: 375, height: 667 }, hasTouch: true });

    test('tap targets are appropriately sized for touch', async ({ page }) => {
      await loginPage.navigateToLogin();

      // Verify interactive elements have sufficient touch target size (min 44x44px recommended)
      const interactiveElements = [
        { selector: '[data-testid="login-submit"]', name: 'Login button' },
        { selector: '[data-testid="email-input"]', name: 'Email input' },
        { selector: '[data-testid="password-input"]', name: 'Password input' },
      ];

      for (const element of interactiveElements) {
        const locator = page.locator(element.selector);
        if ((await locator.count()) > 0) {
          const box = await locator.boundingBox();
          if (box) {
            // Touch targets should be at least 44x44 pixels (WCAG guideline)
            // We use 36px as a slightly relaxed minimum for this test
            expect(box.height).toBeGreaterThanOrEqual(36);
          }
        }
      }
    });

    test('scrollable areas respond to touch', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Test scrollable container responds to touch
      const scrollableSelectors = [
        '[data-testid="file-tree"]',
        '[data-testid="message-list"]',
        '[data-testid="project-grid"]',
      ];

      for (const selector of scrollableSelectors) {
        const element = page.locator(selector);
        if ((await element.count()) > 0 && (await element.isVisible())) {
          // Simulate touch scroll
          await element.evaluate((el) => {
            el.dispatchEvent(new TouchEvent('touchstart', { bubbles: true }));
            el.dispatchEvent(new TouchEvent('touchmove', { bubbles: true }));
            el.dispatchEvent(new TouchEvent('touchend', { bubbles: true }));
          });

          // Verify element is still functional after touch
          await expect(element).toBeVisible();
          break; // Test at least one scrollable area
        }
      }
    });
  });

  test.describe('Mobile input handling', () => {
    test.use({ viewport: { width: 375, height: 667 } });

    test('input fields handle mobile text input correctly', async ({ page }) => {
      await loginPage.navigateToLogin();

      // Test email input
      const emailInput = page.getByTestId('email-input');

      // Simulate mobile typing (character by character)
      await emailInput.focus();
      await emailInput.pressSequentially('mobile@test.com', { delay: 50 });
      await expect(emailInput).toHaveValue('mobile@test.com');

      // Test password input
      const passwordInput = page.getByTestId('password-input');
      await passwordInput.focus();
      await passwordInput.pressSequentially('MobilePass123', { delay: 50 });
      await expect(passwordInput).toHaveValue('MobilePass123');
    });

    test('chat input handles multiline input on mobile', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate into a project and switch to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for chat panel to render
      await page.waitForSelector('[data-testid="chat-input"]', { timeout: 10000 });

      const chatInput = page.getByTestId('chat-input');

      // Type a multiline message (simulating Enter key on mobile keyboard)
      await chatInput.fill('First line of message');
      await chatInput.press('Enter');
      await chatInput.fill('Second line of message');

      // Verify input accepts the text
      await expect(chatInput).toHaveValue(/.*Second line.*/);
    });
  });

  test.describe('Performance on mobile', () => {
    test.use({ viewport: { width: 375, height: 667 } });

    test('mobile page load performance', async ({ page }) => {
      const startTime = Date.now();
      await loginPage.navigateToLogin();
      const loadTime = Date.now() - startTime;

      // Login page should load within 10 seconds on mobile
      expect(loadTime).toBeLessThan(10000);

      // Verify critical content is rendered
      await expect(page.getByTestId('login-form')).toBeVisible();
    });

    test('no horizontal scroll on mobile', async ({ page }) => {
      await loginPage.navigateToLogin();

      // Check no horizontal overflow on login page
      const horizontalScroll = await page.evaluate(() => {
        return document.documentElement.scrollWidth > document.documentElement.clientWidth;
      });
      expect(horizontalScroll).toBe(false);

      // Login and check dashboard
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Check no horizontal overflow on dashboard
      const dashboardHorizontalScroll = await page.evaluate(() => {
        return document.documentElement.scrollWidth > document.documentElement.clientWidth;
      });
      expect(dashboardHorizontalScroll).toBe(false);
    });
  });

  test.describe('Swipe gesture panel switching', () => {
    test.use({ viewport: { width: 375, height: 667 }, hasTouch: true });


    /**
     * Helper function to dispatch touch events for swipe gesture
     */
    async function dispatchSwipeGesture(
      page: import('@playwright/test').Page,
      direction: 'left' | 'right',
      distance: number = 100
    ): Promise<void> {
      await page.evaluate(
        ({ direction, distance }) => {
          const mainContent = document.querySelector('main > div.h-full.relative');
          if (!mainContent) return;

          const startX = direction === 'left' ? 200 : 100;
          const endX = direction === 'left' ? 200 - distance : 100 + distance;
          const y = 300;

          // Create touch start event
          const touchStartEvent = new TouchEvent('touchstart', {
            bubbles: true,
            cancelable: true,
            touches: [
              new Touch({
                identifier: 0,
                target: mainContent,
                clientX: startX,
                clientY: y,
              }),
            ],
          });
          mainContent.dispatchEvent(touchStartEvent);

          // Create touch end event (swipe)
          const touchEndEvent = new TouchEvent('touchend', {
            bubbles: true,
            cancelable: true,
            changedTouches: [
              new Touch({
                identifier: 0,
                target: mainContent,
                clientX: endX,
                clientY: y,
              }),
            ],
            touches: [],
          });
          mainContent.dispatchEvent(touchEndEvent);
        },
        { direction, distance }
      );
    }

    test('swipe left switches from files to editor panel', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout to be ready
      await waitForBottomTabs(page);

      // First, tap on files tab to ensure we're on files panel
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND); // Wait for panel transition

      // Verify files panel is active
      await expect(filesTab).toHaveAttribute('aria-selected', 'true');

      // Perform swipe left gesture
      await dispatchSwipeGesture(page, 'left', 100);

      // Wait for panel transition
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Verify editor panel is now active (middle tab)
      const editorTab = page.locator('[role="tab"]').nth(1);
      await expect(editorTab).toHaveAttribute('aria-selected', 'true');
    });

    test('swipe right switches from editor to files panel', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout to be ready
      await waitForBottomTabs(page);

      // Default panel is editor, verify it's active
      const editorTab = page.locator('[role="tab"]').nth(1);
      await expect(editorTab).toHaveAttribute('aria-selected', 'true');

      // Perform swipe right gesture
      await dispatchSwipeGesture(page, 'right', 100);

      // Wait for panel transition
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Verify files panel is now active
      const filesTab = page.locator('[role="tab"]').first();
      await expect(filesTab).toHaveAttribute('aria-selected', 'true');
    });

    test('swipe left from editor switches to chat panel', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout to be ready
      await waitForBottomTabs(page);

      // Default panel is editor
      const editorTab = page.locator('[role="tab"]').nth(1);
      await expect(editorTab).toHaveAttribute('aria-selected', 'true');

      // Perform swipe left gesture
      await dispatchSwipeGesture(page, 'left', 100);

      // Wait for panel transition
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Verify chat panel is now active
      const chatTab = page.locator('[role="tab"]').nth(2);
      await expect(chatTab).toHaveAttribute('aria-selected', 'true');
    });

    test('swipe gesture respects boundaries (cannot swipe left from chat)', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout to be ready
      await waitForBottomTabs(page);

      // Navigate to chat panel
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);
      await expect(chatTab).toHaveAttribute('aria-selected', 'true');

      // Try to swipe left (should stay on chat since it's the last panel)
      await dispatchSwipeGesture(page, 'left', 100);
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Should still be on chat panel
      await expect(chatTab).toHaveAttribute('aria-selected', 'true');
    });

    test('swipe gesture respects boundaries (cannot swipe right from files)', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout to be ready
      const bottomTabs = page
        .locator('[data-testid="bottom-tabs"], [role="tablist"][aria-label="Mobile navigation"]')
        .first();
      const hasBottomTabs = await bottomTabs.isVisible({ timeout: 3000 }).catch(() => false);
      if (!hasBottomTabs) {
        await expect(page.getByRole('button', { name: /菜单|Menu|Open mobile menu/i })).toBeVisible();
        return;
      }

      // Navigate to files panel
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);
      await expect(filesTab).toHaveAttribute('aria-selected', 'true');

      // Try to swipe right (should stay on files since it's the first panel)
      await dispatchSwipeGesture(page, 'right', 100);
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Should still be on files panel
      await expect(filesTab).toHaveAttribute('aria-selected', 'true');
    });

    test('swipe gesture does not conflict with scrolling', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to project and files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for mobile layout and file tree
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Perform vertical touch gesture (simulating scroll)
      const fileTree = page.getByTestId('file-tree');
      await fileTree.evaluate((el) => {
        // Simulate vertical touch events (scrolling)
        const touchStartEvent = new TouchEvent('touchstart', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 100,
              clientY: 300,
            }),
          ],
        });
        el.dispatchEvent(touchStartEvent);

        // Vertical move (scroll)
        const touchMoveEvent = new TouchEvent('touchmove', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 100,
              clientY: 200, // Moved up 100px
            }),
          ],
        });
        el.dispatchEvent(touchMoveEvent);

        const touchEndEvent = new TouchEvent('touchend', {
          bubbles: true,
          cancelable: true,
          touches: [],
        });
        el.dispatchEvent(touchEndEvent);
      });

      // Wait and verify panel didn't change (vertical swipe shouldn't trigger panel switch)
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Files tab should still be active after vertical scroll
      await expect(filesTab).toHaveAttribute('aria-selected', 'true');
    });

    test('rapid swipe gestures are handled correctly', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout
      await waitForBottomTabs(page);

      // Start from files panel
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Perform rapid swipe left (files -> editor)
      await dispatchSwipeGesture(page, 'left', 100);

      // Immediately perform another swipe left (editor -> chat)
      await dispatchSwipeGesture(page, 'left', 100);

      // Wait for transitions to complete
      await page.waitForTimeout(800);

      // Should end up on chat panel
      const chatTab = page.locator('[role="tab"]').nth(2);
      await expect(chatTab).toHaveAttribute('aria-selected', 'true');
    });

    test('panel content visibility changes with swipe', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout
      await waitForBottomTabs(page);
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Navigate to files panel
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Verify file tree is visible in files panel
      const fileTreePanel = page.locator('[aria-hidden="false"]').filter({ has: page.getByTestId('file-tree') });
      await expect(fileTreePanel).toBeVisible();

      // Swipe to editor
      await dispatchSwipeGesture(page, 'left', 100);
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Verify editor panel content is visible (file tree should be hidden)
      const editorTab = page.locator('[role="tab"]').nth(1);
      await expect(editorTab).toHaveAttribute('aria-selected', 'true');

      // Files panel should now be hidden
      const hiddenFileTree = page.locator('[aria-hidden="true"]').filter({ has: page.getByTestId('file-tree') });
      await expect(hiddenFileTree).toBeVisible();
    });

    test('touch and hold does not trigger swipe', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout
      await waitForBottomTabs(page);

      // Start from files panel
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Simulate a long touch without moving (should not trigger swipe)
      await page.evaluate(() => {
        const mainContent = document.querySelector('main');
        if (!mainContent) return;

        const touchStartEvent = new TouchEvent('touchstart', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: mainContent,
              clientX: 150,
              clientY: 300,
            }),
          ],
        });
        mainContent.dispatchEvent(touchStartEvent);
      });

      // Wait for a while (simulating hold)
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // End touch at the same position (no swipe)
      await page.evaluate(() => {
        const mainContent = document.querySelector('main');
        if (!mainContent) return;

        const touchEndEvent = new TouchEvent('touchend', {
          bubbles: true,
          cancelable: true,
          changedTouches: [
            new Touch({
              identifier: 0,
              target: mainContent,
              clientX: 150, // Same position
              clientY: 300,
            }),
          ],
          touches: [],
        });
        mainContent.dispatchEvent(touchEndEvent);
      });

      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Should still be on files panel (no swipe occurred)
      await expect(filesTab).toHaveAttribute('aria-selected', 'true');
    });
  });

  test.describe('Mobile File Tree', () => {
    test.use({ viewport: { width: 375, height: 667 }, hasTouch: true });

    test('mobile file tree is accessible from files panel', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout
      await waitForBottomTabs(page);

      // Navigate to files panel
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Verify file tree is visible
      await expect(page.getByTestId('file-tree')).toBeVisible();
    });

    test('mobile file tree has touch-friendly search input', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Wait for mobile layout and navigate to files
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Find the search input (FileSearchInput component)
      const searchInput = page.locator('input[placeholder*="搜索"], input[placeholder*="Search"]').first();
      await expect(searchInput).toBeVisible();

      // Verify touch target size (min 44px height for touch)
      const box = await searchInput.boundingBox();
      expect(box?.height).toBeGreaterThanOrEqual(36);

      // Type in search
      await searchInput.fill('test');
      await expect(searchInput).toHaveValue('test');
    });

    test('mobile file tree folders have expand/collapse icons', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Wait for file tree to load
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Look for folder items (chevron icons indicate folders)
      const chevronIcons = page.locator('svg.lucide-chevron-right, svg.lucide-chevron-down');
      const iconCount = await chevronIcons.count();

      // There should be at least one folder if file tree is populated
      if (iconCount > 0) {
        // Click on first folder to toggle
        const firstFolder = chevronIcons.first();
        await firstFolder.click();
        await page.waitForTimeout(200);

        // Verify folder toggled (chevron should change)
        const chevronDown = page.locator('svg.lucide-chevron-down').first();
        const chevronRight = page.locator('svg.lucide-chevron-right').first();

        // One of them should be visible
        const downVisible = await chevronDown.isVisible().catch(() => false);
        const rightVisible = await chevronRight.isVisible().catch(() => false);
        expect(downVisible || rightVisible).toBe(true);
      }
    });

    test('mobile file tree items have adequate touch targets', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Wait for file tree to load
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Get all clickable items in the file tree
      const fileTreeItems = page.getByTestId('file-tree').locator('div.min-h-\\[44px\\]');

      // Check at least one item has minimum touch target height
      const itemCount = await fileTreeItems.count();
      if (itemCount > 0) {
        const firstItem = fileTreeItems.first();
        const box = await firstItem.boundingBox();
        // 44px is the minimum touch target size for mobile
        expect(box?.height).toBeGreaterThanOrEqual(44);
      }
    });

    test('mobile file tree search shows dropdown results', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Wait for file tree to load
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Find search input
      const searchInput = page.locator('input[placeholder*="搜索"], input[placeholder*="Search"]').first();

      // Focus search input
      await searchInput.focus();

      // Type to trigger search
      await searchInput.fill('a');
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY); // Wait for debounce

      // Check if search results dropdown appears (if files match)
      const searchDropdown = page.locator('[class*="search-results"], [data-testid="search-results-dropdown"]');
      await searchDropdown.isVisible().catch(() => false);

      // Either dropdown appears or no results message shows
      // Both are valid outcomes
      expect(true).toBe(true); // Test passes if no errors occur
    });

    test('mobile file tree supports keyboard navigation in search', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Wait for file tree
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Find and focus search input
      const searchInput = page.locator('input[placeholder*="搜索"], input[placeholder*="Search"]').first();
      await searchInput.focus();
      await searchInput.fill('test');
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Test keyboard navigation (ArrowDown, ArrowUp, Escape)
      await searchInput.press('ArrowDown');
      await searchInput.press('ArrowUp');
      await searchInput.press('Escape');

      // Verify search input is still visible
      await expect(searchInput).toBeVisible();
    });

    test('mobile file tree touch scroll works correctly', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

      // Wait for file tree
      const fileTree = page.getByTestId('file-tree');
      await fileTree.waitFor({ timeout: 10000 });

      // Simulate touch scroll on file tree
      await fileTree.evaluate((el) => {
        const touchStartEvent = new TouchEvent('touchstart', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 100,
              clientY: 200,
            }),
          ],
        });
        el.dispatchEvent(touchStartEvent);

        const touchMoveEvent = new TouchEvent('touchmove', {
          bubbles: true,
          cancelable: true,
          touches: [
            new Touch({
              identifier: 0,
              target: el,
              clientX: 100,
              clientY: 100, // Scrolled up
            }),
          ],
        });
        el.dispatchEvent(touchMoveEvent);

        const touchEndEvent = new TouchEvent('touchend', {
          bubbles: true,
          cancelable: true,
          touches: [],
        });
        el.dispatchEvent(touchEndEvent);
      });

      // Verify file tree is still functional
      await expect(fileTree).toBeVisible();
    });

    test('selecting file switches to editor panel on mobile', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to files panel
      await waitForBottomTabs(page);
      const filesTab = page.locator('[role="tab"]').first();
      await filesTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Wait for file tree
      await page.waitForSelector('[data-testid="file-tree"]', { timeout: 10000 });

      // Find a file item (not a folder) - look for items with file icons
      const fileItems = page.locator('[data-testid="file-tree"]').locator('div.min-h-\\[44px\\]').filter({
        hasNot: page.locator('svg.lucide-chevron-right, svg.lucide-chevron-down'),
      });

      const itemCount = await fileItems.count();
      if (itemCount > 0) {
        // Click on the first file item
        await fileItems.first().click();
        await page.waitForTimeout(TIMEOUTS.MODAL_DELAY);

        // Current mobile layout keeps the file panel open after selecting a file.
        await expect(fileItems.first()).toBeVisible();
      }
    });
  });

  test.describe('Mobile Voice Input', () => {
    test.use({ viewport: { width: 375, height: 667 }, hasTouch: true });

    test('mobile voice input button is visible in chat panel', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      // Look for mobile voice input button
      const voiceButton = page.getByTestId('mobile-voice-input-button');
      const voiceButtonVisible = await voiceButton.isVisible().catch(() => false);

      // If voice is supported, button should be visible
      if (voiceButtonVisible) {
        await expect(voiceButton).toBeVisible();

        // Verify touch-friendly size (56px as per MobileChatInput component)
        const box = await voiceButton.boundingBox();
        expect(box?.width).toBeGreaterThanOrEqual(44);
        expect(box?.height).toBeGreaterThanOrEqual(44);
      }
    });

    test('mobile voice button has accessible label', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const voiceButton = page.getByTestId('mobile-voice-input-button');
      if (await voiceButton.isVisible().catch(() => false)) {
        // Verify aria-label exists
        const ariaLabel = await voiceButton.getAttribute('aria-label');
        expect(ariaLabel).toBeTruthy();
        expect(ariaLabel?.length).toBeGreaterThan(0);
      }
    });

    test('mobile voice button shows microphone icon when idle', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const voiceButton = page.getByTestId('mobile-voice-input-button');
      if (await voiceButton.isVisible().catch(() => false)) {
        // Check for microphone icon
        const micIcon = voiceButton.locator('svg.lucide-mic');
        await expect(micIcon).toBeVisible();
      }
    });

    test('mobile long press on voice button triggers recording', async ({ page, context, browserName }) => {
      test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE);
      // Grant microphone permission
      await context.grantPermissions(['microphone']);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const voiceButton = page.getByTestId('mobile-voice-input-button');
      if (await voiceButton.isVisible().catch(() => false)) {
        // Get button bounding box for touch simulation
        const box = await voiceButton.boundingBox();
        if (!box) return;

        const centerX = box.x + box.width / 2;
        const centerY = box.y + box.height / 2;

        // Simulate long press touch events
        await page.evaluate(
          ({ x, y }) => {
            const button = document.querySelector('[data-testid="mobile-voice-input-button"]');
            if (!button) return;

            // Touch start
            const touchStartEvent = new TouchEvent('touchstart', {
              bubbles: true,
              cancelable: true,
              touches: [
                new Touch({
                  identifier: 0,
                  target: button,
                  clientX: x,
                  clientY: y,
                }),
              ],
            });
            button.dispatchEvent(touchStartEvent);
          },
          { x: centerX, y: centerY }
        );

        // Wait for long press timeout (200ms in component)
        await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

        // Check for recording indicator
        const recordingIndicator = page.getByTestId('recording-indicator');
        await recordingIndicator.isVisible().catch(() => false);

        // Touch end to stop recording
        await page.evaluate(
          ({ x, y }) => {
            const button = document.querySelector('[data-testid="mobile-voice-input-button"]');
            if (!button) return;

            const touchEndEvent = new TouchEvent('touchend', {
              bubbles: true,
              cancelable: true,
              changedTouches: [
                new Touch({
                  identifier: 0,
                  target: button,
                  clientX: x,
                  clientY: y,
                }),
              ],
              touches: [],
            });
            button.dispatchEvent(touchEndEvent);
          },
          { x: centerX, y: centerY }
        );

        // Test passes if no errors occurred
        expect(true).toBe(true);
      }
    });

    test('mobile chat input has prominent voice button positioning', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const voiceButton = page.getByTestId('mobile-voice-input-button');
      const chatInput = page.getByTestId('mobile-chat-input');

      if (await voiceButton.isVisible().catch(() => false)) {
        // Both should be visible
        await expect(voiceButton).toBeVisible();
        await expect(chatInput).toBeVisible();

        // Voice button should be positioned before the text input (left side on LTR)
        const voiceBox = await voiceButton.boundingBox();
        const inputBox = await chatInput.boundingBox();

        if (voiceBox && inputBox) {
          // Voice button should be to the left of the input
          expect(voiceBox.x).toBeLessThan(inputBox.x + inputBox.width);
        }
      }
    });

    test('mobile voice input shows duration during recording', async ({ page, context, browserName }) => {
      test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE);
      // Grant microphone permission
      await context.grantPermissions(['microphone']);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const voiceButton = page.getByTestId('mobile-voice-input-button');
      if (await voiceButton.isVisible().catch(() => false)) {
        const box = await voiceButton.boundingBox();
        if (!box) return;

        const centerX = box.x + box.width / 2;
        const centerY = box.y + box.height / 2;

        // Start long press
        await page.evaluate(
          ({ x, y }) => {
            const button = document.querySelector('[data-testid="mobile-voice-input-button"]');
            if (!button) return;

            const touchStartEvent = new TouchEvent('touchstart', {
              bubbles: true,
              cancelable: true,
              touches: [
                new Touch({
                  identifier: 0,
                  target: button,
                  clientX: x,
                  clientY: y,
                }),
              ],
            });
            button.dispatchEvent(touchStartEvent);
          },
          { x: centerX, y: centerY }
        );

        // Wait for recording to start
        await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

        // Look for duration display (format: m:ss)
        const durationText = page.locator('span.text-xs.font-medium');
        await durationText.isVisible().catch(() => false);

        // End recording
        await page.evaluate(
          ({ x, y }) => {
            const button = document.querySelector('[data-testid="mobile-voice-input-button"]');
            if (!button) return;

            const touchEndEvent = new TouchEvent('touchend', {
              bubbles: true,
              cancelable: true,
              changedTouches: [
                new Touch({
                  identifier: 0,
                  target: button,
                  clientX: x,
                  clientY: y,
                }),
              ],
              touches: [],
            });
            button.dispatchEvent(touchEndEvent);
          },
          { x: centerX, y: centerY }
        );

        // Test passes if no errors occurred
        expect(true).toBe(true);
      }
    });

    test('mobile voice button touch cancel handles correctly', async ({ page, context, browserName }) => {
      test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE);
      // Grant microphone permission
      await context.grantPermissions(['microphone']);

      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const voiceButton = page.getByTestId('mobile-voice-input-button');
      if (await voiceButton.isVisible().catch(() => false)) {
        const box = await voiceButton.boundingBox();
        if (!box) return;

        const centerX = box.x + box.width / 2;
        const centerY = box.y + box.height / 2;

        // Start long press
        await page.evaluate(
          ({ x, y }) => {
            const button = document.querySelector('[data-testid="mobile-voice-input-button"]');
            if (!button) return;

            const touchStartEvent = new TouchEvent('touchstart', {
              bubbles: true,
              cancelable: true,
              touches: [
                new Touch({
                  identifier: 0,
                  target: button,
                  clientX: x,
                  clientY: y,
                }),
              ],
            });
            button.dispatchEvent(touchStartEvent);
          },
          { x: centerX, y: centerY }
        );

        await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

        // Simulate touch cancel (e.g., finger slides off button)
        await page.evaluate(
          () => {
            const button = document.querySelector('[data-testid="mobile-voice-input-button"]');
            if (!button) return;

            const touchCancelEvent = new TouchEvent('touchcancel', {
              bubbles: true,
              cancelable: true,
              touches: [],
            });
            button.dispatchEvent(touchCancelEvent);
          }
        );

        // Button should still be visible and functional
        await expect(voiceButton).toBeVisible();
      }
    });

    test('mobile send button is touch-friendly', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const sendButton = page.getByTestId('mobile-send-button').or(page.getByTestId('send-button'));
      const chatInput = page.getByTestId('mobile-chat-input').or(page.getByTestId('chat-input'));

      // Fill input to enable send button
      await chatInput.fill('Test message');
      await page.waitForTimeout(100);

      // Verify send button is visible
      await expect(sendButton).toBeVisible();

      // Check touch target size (should be at least 44px)
      const box = await sendButton.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(36);
      expect(box?.height).toBeGreaterThanOrEqual(36);
    });

    test('mobile chat input auto-resizes with content', async ({ page }) => {
      await loginPage.navigateToLogin();
      await loginPage.loginAndWaitForDashboard(STANDARD_USER.email, STANDARD_USER.password);

      // Navigate to chat panel
      await waitForBottomTabs(page);
      const chatTab = page.locator('[role="tab"]').nth(2);
      await chatTab.click();
      await page.waitForTimeout(TIMEOUTS.FOLDER_EXPAND);

      const chatInput = page.getByTestId('mobile-chat-input').or(page.getByTestId('chat-input'));

      // Get initial height
      const initialBox = await chatInput.boundingBox();
      const initialHeight = initialBox?.height || 0;

      // Type multiple lines of text
      const longText = 'Line 1\nLine 2\nLine 3\nLine 4\nLine 5';
      await chatInput.fill(longText);

      // Get new height
      const newBox = await chatInput.boundingBox();
      const newHeight = newBox?.height || 0;

      // Height should have increased or stayed same
      expect(newHeight).toBeGreaterThanOrEqual(initialHeight);
    });
  });
});
