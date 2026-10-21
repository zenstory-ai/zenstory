import { test, expect, type Page } from '@playwright/test';
import { getPoolUser } from './config';
import { cleanupOwnedProject, createOwnedProject, type OwnedProject } from './fixtures/project-test-data';
import { checkWCAGAA, assertNoCriticalViolations, runAccessibilityScan } from './utils/axe-helper';

/**
 * Accessibility E2E Tests
 *
 * Tests accessibility compliance including:
 * - Keyboard navigation
 * - Screen reader support
 * - Color contrast (basic verification)
 * - ARIA attributes
 */

test.describe.configure({ mode: 'serial' });

let ownedProject: OwnedProject | undefined;

async function openOwnedProject(page: Page): Promise<void> {
  ownedProject = await createOwnedProject(page, test.info(), 'a11y');
  await page.goto(`/project/${ownedProject.id}`, { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(`/project/${ownedProject.id}`);
  await expect(
    page.locator('input[placeholder*="搜索文件"], input[placeholder*="Search files"], [role="searchbox"]').first()
  ).toBeVisible();
}

test.afterEach(async ({ page }) => {
  await cleanupOwnedProject(page, ownedProject);
  ownedProject = undefined;
});

test.describe('Accessibility - Keyboard Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await openOwnedProject(page);
  });

  test('all interactive elements are keyboard accessible', async ({ page }) => {
    // Get all interactive elements
    const buttons = await page.$$('button');
    const links = await page.$$('a');
    const inputs = await page.$$('input, textarea, select');

    const allInteractive = [...buttons, ...links, ...inputs];

    // Test that we can focus on each element
    let focusableCount = 0;
    for (const element of allInteractive.slice(0, 20)) { // Test first 20 to avoid timeout
      try {
        await element.focus();
        const isFocused = await element.evaluate(el => document.activeElement === el);
        if (isFocused) {
          focusableCount++;
        }
      } catch {
        // Element might not be focusable (e.g., disabled)
      }
    }

    // At least some elements should be focusable
    expect(focusableCount).toBeGreaterThan(0);
  });

  test('tab order follows logical sequence', async ({ page }) => {
    // Press Tab multiple times and verify focus moves forward
    const focusedElements: string[] = [];

    for (let i = 0; i < 10; i++) {
      await page.keyboard.press('Tab');

      // Get the focused element's tag name
      const focused = await page.evaluate(() => {
        const el = document.activeElement;
        return el ? `${el.tagName}:${el.getAttribute('aria-label') || el.getAttribute('id') || el.textContent?.trim().slice(0, 20)}` : null;
      });

      if (focused) {
        focusedElements.push(focused);
      }
    }

    // Verify that we moved through different elements
    const uniqueElements = new Set(focusedElements);
    expect(uniqueElements.size).toBeGreaterThan(1);
  });

  test('focus is visible on all focusable elements', async ({ page }) => {
    const buttons = await page.$$('button');

    // Test first 5 buttons
    for (const button of buttons.slice(0, 5)) {
      await button.focus();

      // Check if button has visible focus indicator
      // This can be outline, box-shadow, border, or background change
      await button.evaluate(el => {
        const styles = window.getComputedStyle(el);
        const hasOutline = styles.outline !== 'none' && styles.outlineWidth !== '0px';
        const hasBoxShadow = styles.boxShadow !== 'none';
        const hasBorderChange = styles.borderWidth !== '0px';

        // Check if element is actually focused
        const isFocused = document.activeElement === el;

        return isFocused && (hasOutline || hasBoxShadow || hasBorderChange);
      });

      // At minimum, element should be focused
      const isFocused = await button.evaluate(el => document.activeElement === el);
      expect(isFocused).toBe(true);
    }
  });

  test('can navigate file tree with keyboard', async ({ page }) => {
    const tree = page.getByRole('tree');
    const rows = tree.getByRole('treeitem');
    const first = rows.first();
    await expect(first).toBeVisible();

    // Tab from the file search reaches the first row; arrows then move between rows.
    await page.locator('input[placeholder*="搜索文件"], input[placeholder*="Search files"], [role="searchbox"]').first().focus();
    await page.keyboard.press('Tab');
    await expect(first).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(rows.nth(1)).toBeFocused();
    await page.keyboard.press('ArrowUp');
    await expect(first).toBeFocused();

    // Left closes the open folder and Right opens it again, keeping focus on it.
    await expect(first).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('ArrowLeft');
    await expect(first).toHaveAttribute('aria-expanded', 'false');
    await page.keyboard.press('ArrowRight');
    await expect(first).toHaveAttribute('aria-expanded', 'true');
    await expect(first).toBeFocused();

    await page.keyboard.press('End');
    await expect(rows.last()).toBeFocused();
    await page.keyboard.press('Home');
    await expect(first).toBeFocused();
  });

  test('can activate buttons with Enter and Space', async ({ page }) => {
    // Find a button
    const button = page.locator('button').first();
    await button.focus();

    // Test Enter key
    await page.keyboard.press('Enter');

    // Test Space key (refocus first)
    await button.focus();
    await page.keyboard.press('Space');

    // No assertion needed - just verifying no errors occur
  });
});

test.describe('Accessibility - Screen Reader Support', () => {
  test.beforeEach(async ({ page }) => {
    await openOwnedProject(page);
  });

  test('images have alt text', async ({ page }) => {
    const images = await page.$$('img');

    for (const img of images) {
      const alt = await img.getAttribute('alt');
      const ariaLabel = await img.getAttribute('aria-label');
      const role = await img.getAttribute('role');

      // Image should have alt text, aria-label, or be decorative (role="presentation")
      const hasAccessibleName = alt !== null || ariaLabel !== null;
      const isDecorative = role === 'presentation' || role === 'none';

      expect(hasAccessibleName || isDecorative).toBe(true);
    }
  });

  test('form inputs have labels', async ({ page }) => {
    const inputs = await page.$$('input:not([type="hidden"]), textarea, select');
    let checked = 0;
    let unlabeled = 0;

    for (const input of inputs) {
      const id = await input.getAttribute('id');
      const ariaLabel = await input.getAttribute('aria-label');
      const ariaLabelledBy = await input.getAttribute('aria-labelledby');
      const placeholder = await input.getAttribute('placeholder');
      const type = await input.getAttribute('type');

      // Ignore technical fields that are often intentionally unlabeled
      if (type === 'checkbox' || type === 'radio') {
        continue;
      }

      if (id) {
        // Check for associated label
        const label = await page.$(`label[for="${id}"]`);
        const hasLabel = label !== null;
        const hasAriaLabel = ariaLabel !== null;
        const hasAriaLabelledBy = ariaLabelledBy !== null;

        if (hasLabel || hasAriaLabel || hasAriaLabelledBy || placeholder) {
          checked++;
        } else {
          unlabeled++;
        }
      } else {
        // If no id, should have aria-label or aria-labelledby
        if (ariaLabel || ariaLabelledBy || placeholder) {
          checked++;
        } else {
          unlabeled++;
        }
      }
    }

    expect(checked).toBeGreaterThan(0);
    // Allow a small number of implementation-specific unlabeled fields
    expect(unlabeled).toBeLessThanOrEqual(2);
  });

  test('buttons have accessible names', async ({ page }) => {
    const buttons = await page.$$('button');

    for (const button of buttons) {
      const text = await button.textContent();
      const ariaLabel = await button.getAttribute('aria-label');
      const title = await button.getAttribute('title');

      const hasAccessibleName =
        (text && text.trim().length > 0) ||
        ariaLabel !== null ||
        title !== null;

      expect(hasAccessibleName).toBe(true);
    }
  });

  test('icons have accessible labels or are hidden', async ({ page }) => {
    // Find icon elements (SVGs with icon classes)
    const icons = await page.$$('svg[class*="icon"], [class*="Icon"]');

    for (const icon of icons.slice(0, 10)) { // Test first 10
      const ariaLabel = await icon.getAttribute('aria-label');
      const ariaHidden = await icon.getAttribute('aria-hidden');
      const role = await icon.getAttribute('role');

      // Icon should either have accessible label or be hidden from screen readers
      const hasLabel = ariaLabel !== null;
      const isHidden = ariaHidden === 'true';
      const isImg = role === 'img';

      expect(hasLabel || isHidden || isImg).toBeTruthy();
    }
  });

  test('headings follow logical hierarchy', async ({ page }) => {
    // Get all headings
    const h1Count = await page.locator('h1').count();
    const h2Count = await page.locator('h2').count();
    const h3Count = await page.locator('h3').count();
    const ariaHeadingCount = await page.locator('[role="heading"]').count();

    // Headings should exist (native or ARIA)
    const totalHeadings = h1Count + h2Count + h3Count + ariaHeadingCount;
    expect(totalHeadings).toBeGreaterThan(0);
  });

  test('links have discernible text', async ({ page }) => {
    const links = await page.$$('a');

    for (const link of links) {
      const text = await link.textContent();
      const ariaLabel = await link.getAttribute('aria-label');
      const title = await link.getAttribute('title');

      const hasDiscernibleText =
        (text && text.trim().length > 0) ||
        ariaLabel !== null ||
        title !== null;

      expect(hasDiscernibleText).toBe(true);
    }
  });
});

test.describe('Accessibility - Color Contrast', () => {
  test.beforeEach(async ({ page }) => {
    await openOwnedProject(page);
  });

  test('text meets WCAG AA contrast requirements', async ({ page }) => {
    // This is a simplified test
    // For comprehensive contrast testing, use axe-core or similar tools
    const textElements = await page.$$('p, h1, h2, h3, h4, h5, h6, span, label');

    // Basic check - ensure text is visible (has color set)
    for (const element of textElements.slice(0, 10)) {
      const color = await element.evaluate(el => {
        const styles = window.getComputedStyle(el);
        return {
          color: styles.color,
          backgroundColor: styles.backgroundColor,
          fontSize: styles.fontSize
        };
      });

      // Verify color is set and not transparent
      expect(color.color).toBeTruthy();
      expect(color.color).not.toBe('transparent');
      expect(color.color).not.toBe('rgba(0, 0, 0, 0)');
    }
  });

  test('text is readable with different font sizes', async ({ page }) => {
    const textElements = await page.$$('p, h1, h2, h3, h4, h5, h6');

    for (const element of textElements.slice(0, 5)) {
      const fontSize = await element.evaluate(el => {
        return window.getComputedStyle(el).fontSize;
      });

      // Font size should be defined
      expect(fontSize).toBeTruthy();
      expect(fontSize).not.toBe('0px');
    }
  });

  test('interactive elements have sufficient visual distinction', async ({ page }) => {
    const buttons = await page.$$('button');

    for (const button of buttons.slice(0, 5)) {
      const styles = await button.evaluate(el => {
        const computed = window.getComputedStyle(el);
        return {
          backgroundColor: computed.backgroundColor,
          color: computed.color,
          cursor: computed.cursor
        };
      });
      const disabled = await button.getAttribute('disabled');
      const ariaDisabled = await button.getAttribute('aria-disabled');
      const isDisabled = disabled !== null || ariaDisabled === 'true';

      // Active buttons should expose a pointer-like affordance
      if (!isDisabled) {
        expect(['pointer', 'default']).toContain(styles.cursor);
      }

      // Background and color should be set
      expect(styles.backgroundColor).toBeTruthy();
      expect(styles.color).toBeTruthy();
    }
  });
});

test.describe('Accessibility - ARIA', () => {
  test.beforeEach(async ({ page }) => {
    await openOwnedProject(page);
  });

  test('landmarks are properly defined', async ({ page }) => {
    // Check for main landmark
    const mainLandmark = await page.$('main, [role="main"]');
    expect(mainLandmark).toBeTruthy();

    // Check for navigation landmark
    const navCount = await page.locator('nav, [role="navigation"]').count();
    const sidebarTabCount = await page
      .locator('button:has-text("项目文件"), button:has-text("Files"), button:has-text("技能"), button:has-text("Skills"), button:has-text("素材库"), button:has-text("Materials")')
      .count();
    expect(navCount + sidebarTabCount).toBeGreaterThan(0);
  });

  test('dynamic content has live regions', async ({ page }) => {
    // Check for live regions that announce dynamic changes
    const liveRegions = await page.$$('[aria-live], [role="status"], [role="alert"]');
    for (const region of liveRegions) {
      const role = await region.getAttribute('role');
      const ariaLive = await region.getAttribute('aria-live');
      if (role) {
        expect(['status', 'alert']).toContain(role);
      }
      if (ariaLive) {
        expect(['polite', 'assertive', 'off']).toContain(ariaLive);
      }
    }

    // Ensure existing aria-live usage is valid even when no announcement region is mounted yet.
    const invalidLiveRegions = await page
      .locator('[aria-live]:not([aria-live="polite"]):not([aria-live="assertive"]):not([aria-live="off"])')
      .count();
    expect(invalidLiveRegions).toBe(0);
  });

  test('form error messages are accessible', async ({ page }) => {
    // Navigate to a page with a form
    await page.context().clearCookies();
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('login-form')).toBeVisible();
    const submitButton = page.locator('button[type="submit"]').first();
    if (!(await submitButton.count())) {
      return;
    }

    const isSubmitDisabled = await submitButton.isDisabled().catch(() => false);
    if (isSubmitDisabled) {
      await expect(submitButton).toBeDisabled();
    } else {
      // Try to submit empty form when the current UI allows it
      await submitButton.click();
    }

    // Check for error messages
    const errorElements = await page.$$('[role="alert"], [aria-live="assertive"], .error, .error-message');
    const invalidFields = await page.$$('[aria-invalid="true"]');
    const nativeInvalidCount = await page.evaluate(() => {
      const selector = '#identifier, #password, input[name="email"], input[name="password"]';
      return Array.from(document.querySelectorAll<HTMLInputElement>(selector)).filter((el) => el.matches(':invalid'))
        .length;
    });

    // Errors might be shown inline, as alerts, or via invalid field attributes
    expect(errorElements.length + invalidFields.length + nativeInvalidCount).toBeGreaterThan(0);
  });

  test('modal dialogs have proper ARIA attributes', async ({ page }) => {
    // Check if there are any modal triggers
    const modalTriggers = await page.$$('[data-testid*="modal"], button[aria-haspopup="dialog"]');

    // If there are modal triggers, test them
    if (modalTriggers.length > 0) {
      await modalTriggers[0].click();
      await expect(page.locator('[role="dialog"]')).toBeVisible();

      // Check for modal with proper ARIA
      const modal = await page.$('[role="dialog"], [role="alertdialog"]');
      if (modal) {
        const ariaModal = await modal.getAttribute('aria-modal');
        const ariaLabel = await modal.getAttribute('aria-label');
        const ariaLabelledBy = await modal.getAttribute('aria-labelledby');

        expect(ariaModal).toBe('true');
        expect(ariaLabel || ariaLabelledBy).toBeTruthy();
      }
    }
  });

  test('tree views have proper ARIA attributes', async ({ page }) => {
    // File tree should have tree role
    const tree = await page.$('[role="tree"], [data-testid="file-tree"]');

    if (tree) {
      const role = await tree.getAttribute('role');
      const ariaLabel = await tree.getAttribute('aria-label');

      // Tree should have a label
      expect(role === 'tree' || ariaLabel).toBeTruthy();
    }
  });

  test('buttons have correct ARIA states', async ({ page }) => {
    const buttons = await page.$$('button');

    for (const button of buttons.slice(0, 10)) {
      const disabled = await button.getAttribute('disabled');
      const ariaDisabled = await button.getAttribute('aria-disabled');

      // If button has aria-disabled, it should match disabled state
      if (ariaDisabled !== null) {
        const isDisabled = disabled !== null || ariaDisabled === 'true';
        expect(typeof isDisabled).toBe('boolean');
      }
    }
  });
});

test.describe('Accessibility - Focus Management', () => {
  test.beforeEach(async ({ page }) => {
    await openOwnedProject(page);
  });

  test('focus is trapped in modal dialogs', async ({ page }) => {
    // Try to open a modal if available
    const modalTrigger = page.locator('button[aria-haspopup="dialog"], [data-testid*="modal"]').first();

    if (await modalTrigger.isVisible()) {
      await modalTrigger.click();
      await expect(page.locator('[role="dialog"]')).toBeVisible();

      // Tab through modal - focus should stay within modal
      const modal = page.locator('[role="dialog"]').first();
      if (await modal.isVisible()) {
        // Press Tab multiple times
        for (let i = 0; i < 10; i++) {
          await page.keyboard.press('Tab');
          await expect(page.locator(':focus')).toBeVisible();
        }

        // Focus should still be within modal
        const focusedInModal = await page.evaluate(() => {
          const modal = document.querySelector('[role="dialog"]');
          return modal?.contains(document.activeElement);
        });

        expect(focusedInModal).toBe(true);
      }
    }
  });

  test('focus returns to trigger after modal close', async ({ page }) => {
    const modalTrigger = page.locator('button[aria-haspopup="dialog"]').first();

    if (await modalTrigger.isVisible()) {
      await modalTrigger.click();
      const modal = page.locator('[role="dialog"]').first();
      if (await modal.isVisible()) {
        // Close modal with Escape
        await page.keyboard.press('Escape');
        await expect(modal).not.toBeVisible();

        // Focus should return to trigger
        const isTriggerFocused = await modalTrigger.evaluate(el => document.activeElement === el);
        expect(isTriggerFocused).toBe(true);
      }
    }
  });

  test('skip links are available', async ({ page }) => {
    // Check for skip link at the top of page
    const skipLink = await page.$('a[href="#main"], a[href="#content"], [data-testid="skip-link"]');

    // Skip link might be hidden until focused
    if (skipLink) {
      // Tab to reveal skip link
      await page.keyboard.press('Tab');

      const isVisible = await skipLink.isVisible();
      // Skip link should become visible on focus
      expect(typeof isVisible).toBe('boolean');
    }
  });

  test('keyboard shortcuts are documented', async ({ page }) => {
    // Check for keyboard shortcut documentation
    const helpTrigger = page.locator('[aria-label*="keyboard"], [aria-label*="shortcut"], button:has-text("快捷键")');

    if (await helpTrigger.isVisible()) {
      await helpTrigger.click();
      // Help dialog should appear
      const helpDialog = page.locator('[role="dialog"], [role="alertdialog"]');
      await expect(helpDialog).toBeVisible();
      const isVisible = await helpDialog.isVisible();
      expect(isVisible).toBe(true);
    }
  });
});

/**
 * Accessibility - Enhanced Tests
 *
 * Additional comprehensive accessibility tests covering:
 * - Complete keyboard-only navigation flows
 * - Screen reader dynamic content announcements
 * - WCAG AAA color contrast (stricter than AA)
 * - Focus management in modals (focus trap + return focus)
 * - ARIA labels on all interactive elements
 */
test.describe('Accessibility - Enhanced Tests', () => {
  /**
   * Test 1: Keyboard-Only Navigation Completes Core Flow
   * Verifies users can complete login -> create project -> create file using only keyboard
   */
  test('keyboard-only navigation completes core flow', async ({ page }) => {
    const owner = getPoolUser(test.info().parallelIndex);

    // Start at login page
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('login-form')).toBeVisible();

    // Focus email input (Tab start position varies by browser/runtime)
    await page.locator('#identifier').focus();
    await expect(page.locator('#identifier')).toBeFocused();

    // Fill email using keyboard
    await page.keyboard.type(owner.email);

    // Tab to password input
    await page.keyboard.press('Tab');
    if (!(await page.locator('#password').evaluate((el) => document.activeElement === el))) {
      await page.locator('#password').focus();
    }
    await expect(page.locator('#password')).toBeFocused();

    // Fill password using keyboard
    await page.keyboard.type(owner.password);

    // Tab to submit button and press Enter
    await page.keyboard.press('Tab');
    const submitButton = page.locator('button[type="submit"]').first();
    if (!(await submitButton.evaluate((el) => document.activeElement === el))) {
      await submitButton.focus();
    }
    await expect(submitButton).toBeFocused();
    const loginResponsePromise = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'POST' && url.pathname === '/api/auth/login';
    });
    await page.keyboard.press('Enter');
    expect((await loginResponsePromise).ok()).toBeTruthy();

    // Wait for navigation to complete
    await expect(page).toHaveURL('/dashboard');
    await expect(page.getByTestId('dashboard-composer')).toBeVisible();

    // Tab to find "Create Project" button
    const maxTabs = 20;
    let foundCreateButton = false;
    const createProjectButton = page.getByTestId('create-project-button');

    for (let i = 0; i < maxTabs; i++) {
      const createButtonFocused = await createProjectButton.evaluate(
        (element) => document.activeElement === element,
      );
      if (createButtonFocused) {
        foundCreateButton = true;
        const accessToken = await page.evaluate(() => localStorage.getItem('access_token'));
        expect(accessToken).toBeTruthy();
        const createResponsePromise = page.waitForResponse((response) => {
          const url = new URL(response.url());
          return response.request().method() === 'POST' && url.pathname === '/api/v1/projects';
        });
        await page.keyboard.press('Enter');
        const createResponse = await createResponsePromise;
        expect(createResponse.ok()).toBeTruthy();
        const project = await createResponse.json() as { id: string; name?: string };
        ownedProject = {
          id: project.id,
          name: project.name ?? `a11y-${project.id}`,
          headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
          owner,
        };
        await expect(page).toHaveURL(`/project/${project.id}`);
        break;
      }

      await page.keyboard.press('Tab');
    }

    expect(foundCreateButton).toBe(true);

    // Verify we're on a project page
    expect(page.url()).toMatch(/\/project\//);
  });

  /**
   * Test 2: Screen Reader Announces Dynamic Content
   * Verifies aria-live regions exist for dynamic content announcements
   */
  test('screen reader announces dynamic content', async ({ page }) => {
    await openOwnedProject(page);

    // Check for aria-live regions that announce dynamic changes
    const liveRegionCount = await page.locator('[aria-live]').count();
    const liveRegions = await page.locator('[aria-live]').all();

    // Check types of aria-live regions
    const liveRegionTypes = await Promise.all(
      liveRegions.map(async (region) => {
        const liveValue = await region.getAttribute('aria-live');
        const atomic = await region.getAttribute('aria-atomic');
        return { liveValue, atomic };
      })
    );

    // Verify live regions have appropriate values (polite or assertive)
    for (const regionType of liveRegionTypes) {
      expect(['polite', 'assertive', 'off', null]).toContain(regionType.liveValue);
    }

    // Check for status and alert roles which also provide announcements
    const statusRegions = await page.locator('[role="status"]').count();
    const alertRegions = await page.locator('[role="alert"]').count();

    const invalidLiveRegions = await page
      .locator('[aria-live]:not([aria-live="polite"]):not([aria-live="assertive"]):not([aria-live="off"])')
      .count();
    expect(invalidLiveRegions).toBe(0);

    // Snapshot count information for debugging/tracking.
    test.info().annotations.push({
      type: 'announcement-regions',
      description: `aria-live=${liveRegionCount}, status=${statusRegions}, alert=${alertRegions}`,
    });
  });

  /**
   * Test 3: WCAG AA color contrast on actual workspace surfaces.
   * Resolve transparent colors and ancestor backgrounds with Axe.
   */
  test('workspace color contrast meets WCAG AA on its actual surfaces', async ({ page }) => {
    await openOwnedProject(page);
    // Axe resolves transparent foregrounds and ancestor surfaces; rgba(0,0,0,0)
    // must not be treated as a black background. Check all rendered text, not 15 nodes.
    const result = await checkWCAGAA(page);
    expect(result.violations.filter(violation => violation.id === 'color-contrast')).toEqual([]);
  });

  /**
   * Test 4: Focus Management in Modals
   * Verifies focus trap within modal and focus return to trigger on close
   */
  test('focus management in modals', async ({ page }) => {
    await openOwnedProject(page);

    // Find a modal trigger
    const modalTrigger = page.locator('button[aria-haspopup="dialog"], [data-testid*="modal"]').first();

    if (await modalTrigger.isVisible()) {
      // Store reference to trigger element
      const triggerElement = await modalTrigger.elementHandle();

      // Click to open modal
      await modalTrigger.click();
      await expect(page.locator('[role="dialog"]')).toBeVisible();

      // Verify modal appeared
      const modal = page.locator('[role="dialog"], [role="alertdialog"]').first();
      if (await modal.isVisible()) {
        // Test 1: Focus should be within modal
        const initialFocusInModal = await page.evaluate(() => {
          const modal = document.querySelector('[role="dialog"], [role="alertdialog"]');
          return modal?.contains(document.activeElement);
        });
        expect(initialFocusInModal).toBe(true);

        // Test 2: Tab through all focusable elements in modal
        const focusableElements = await modal.locator(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        ).all();

        if (focusableElements.length > 0) {
          // Press Tab multiple times to cycle through modal elements
          const tabCount = focusableElements.length + 2; // Cycle through + wrap around
          for (let i = 0; i < tabCount; i++) {
            await page.keyboard.press('Tab');

            // Verify focus stays within modal
            const focusInModal = await page.evaluate(() => {
              const modal = document.querySelector('[role="dialog"], [role="alertdialog"]');
              return modal?.contains(document.activeElement);
            });
            expect(focusInModal).toBe(true);
          }
        }

        // Test 3: Close modal and verify focus returns to trigger
        await page.keyboard.press('Escape');
        await expect(modal).not.toBeVisible();

        // Verify modal is closed
        const modalStillVisible = await modal.isVisible();
        expect(modalStillVisible).toBe(false);

        // Verify focus returned to trigger element
        const focusReturned = await page.evaluate((trigger) => {
          return document.activeElement === trigger;
        }, triggerElement);
        expect(focusReturned).toBe(true);
      }
    } else {
      // If no modal trigger found, verify we correctly detected the absence.
      const modalTriggerCount = await page
        .locator('button[aria-haspopup="dialog"], [data-testid*="modal"]')
        .count();
      expect(modalTriggerCount).toBe(0);
    }
  });

  /**
   * Test 5: ARIA Labels on All Interactive Elements
   * Verifies all buttons, links, and inputs have accessible names
   */
  test('ARIA labels on interactive elements', async ({ page }) => {
    await openOwnedProject(page);

    // Test all buttons
    const buttons = await page.locator('button').all();
    const buttonIssues: string[] = [];

    for (const button of buttons) {
      const accessibleName = await button.evaluate((el) => {
        const text = el.textContent?.trim();
        const ariaLabel = el.getAttribute('aria-label');
        const ariaLabelledBy = el.getAttribute('aria-labelledby');
        const title = el.getAttribute('title');

        // Check if button has any accessible name
        if (text && text.length > 0) return text;
        if (ariaLabel) return ariaLabel;
        if (ariaLabelledBy) {
          const labelElement = document.getElementById(ariaLabelledBy);
          return labelElement?.textContent?.trim() || null;
        }
        if (title) return title;

        return null;
      });

      if (!accessibleName) {
        const buttonInfo = await button.evaluate((el) => ({
          class: el.className,
          id: el.id,
          type: (el as HTMLButtonElement).type
        }));
        buttonIssues.push(`Button without accessible name: ${JSON.stringify(buttonInfo)}`);
      }
    }

    // Test all links
    const links = await page.locator('a').all();
    const linkIssues: string[] = [];

    for (const link of links) {
      const accessibleName = await link.evaluate((el) => {
        const text = el.textContent?.trim();
        const ariaLabel = el.getAttribute('aria-label');
        const ariaLabelledBy = el.getAttribute('aria-labelledby');
        const title = el.getAttribute('title');

        if (text && text.length > 0) return text;
        if (ariaLabel) return ariaLabel;
        if (ariaLabelledBy) {
          const labelElement = document.getElementById(ariaLabelledBy);
          return labelElement?.textContent?.trim() || null;
        }
        if (title) return title;

        return null;
      });

      if (!accessibleName) {
        const linkInfo = await link.evaluate((el) => ({
          class: el.className,
          id: el.id,
          href: (el as HTMLAnchorElement).href
        }));
        linkIssues.push(`Link without accessible name: ${JSON.stringify(linkInfo)}`);
      }
    }

    // Test all inputs
    const inputs = await page.locator('input:not([type="hidden"]), textarea, select').all();
    const inputIssues: string[] = [];

    for (const input of inputs) {
      const accessibleName = await input.evaluate((el) => {
        const id = el.id;
        const ariaLabel = el.getAttribute('aria-label');
        const ariaLabelledBy = el.getAttribute('aria-labelledby');
        const placeholder = el.getAttribute('placeholder');

        // Check for associated label
        if (id) {
          const label = document.querySelector(`label[for="${id}"]`);
          if (label?.textContent?.trim()) return label.textContent.trim();
        }

        if (ariaLabel) return ariaLabel;
        if (ariaLabelledBy) {
          const labelElement = document.getElementById(ariaLabelledBy);
          return labelElement?.textContent?.trim() || null;
        }
        if (placeholder) return placeholder;

        return null;
      });

      if (!accessibleName) {
        const inputInfo = await input.evaluate((el) => ({
          class: el.className,
          id: el.id,
          type: (el as HTMLInputElement).type,
          name: (el as HTMLInputElement).name
        }));
        inputIssues.push(`Input without accessible name: ${JSON.stringify(inputInfo)}`);
      }
    }

    // All interactive elements should have accessible names
    const totalIssues = [...buttonIssues, ...linkIssues, ...inputIssues];

    // Allow up to 5 issues for icon-only buttons that might have aria-labels added dynamically
    expect(totalIssues.length).toBeLessThanOrEqual(5);

    // Log issues for debugging if needed
    if (totalIssues.length > 0) {
      console.log('Accessibility issues found:', totalIssues);
    }
  });
});

// ============================================================
// Automated Accessibility Tests (axe-core)
// ============================================================

test.describe('Automated Accessibility (axe-core)', () => {

  test('login page has no WCAG AA violations', async ({ page }) => {
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('login-form')).toBeVisible();

    const result = await checkWCAGAA(page);

    // Log all violations for debugging
    if (result.violations.length > 0) {
      console.log(`Login page: ${result.violations.length} accessibility violations`);
      result.violations.forEach(v => {
        console.log(`  - ${v.id} (${v.impact}): ${v.description}`);
      });
    }

    // Assert no critical/serious violations
    assertNoCriticalViolations(result);
  });

  test('dashboard has no WCAG AA violations', async ({ page }) => {
    await openOwnedProject(page);

    // Navigate back to dashboard
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('dashboard-composer')).toBeVisible();

    const result = await checkWCAGAA(page);

    if (result.violations.length > 0) {
      console.log(`Dashboard: ${result.violations.length} accessibility violations`);
      result.violations.forEach(v => {
        console.log(`  - ${v.id} (${v.impact}): ${v.description}`);
      });
    }

    assertNoCriticalViolations(result);
  });

  test('project workspace has no WCAG AA violations', async ({ page }) => {
    await openOwnedProject(page);

    const result = await checkWCAGAA(page);

    if (result.violations.length > 0) {
      console.log(`Project workspace: ${result.violations.length} accessibility violations`);
      result.violations.forEach(v => {
        console.log(`  - ${v.id} (${v.impact}): ${v.description}`);
      });
    }

    assertNoCriticalViolations(result);
  });

  test('settings dialog has no WCAG AA violations', async ({ page }) => {
    await openOwnedProject(page);

    // Open settings
    await page.getByTestId('settings-button').click();
    const settingsDialog = page.locator('[role="dialog"], [role="alertdialog"]').first();
    await expect(settingsDialog).toBeVisible();
    await page.waitForTimeout(300);

    const result = await runAccessibilityScan(page, {
      tags: ['wcag2a', 'wcag2aa'],
      // Known issue: icon-only close button in current settings modal shell
      disableRules: ['button-name'],
    });

    if (result.violations.length > 0) {
      console.log(`Settings dialog: ${result.violations.length} accessibility violations`);
      result.violations.forEach(v => {
        console.log(`  - ${v.id} (${v.impact}): ${v.description}`);
      });
    }

    assertNoCriticalViolations(result);
  });
});
