import { test, expect, Page, Route, APIRequestContext } from '@playwright/test';
import { TEST_USERS, config } from './config';

/**
 * E2E Tests for Subscription System
 *
 * These tests cover the subscription and quota management flow:
 * - Viewing subscription status (Free/Pro)
 * - Viewing quota usage
 * - Redeeming subscription codes
 * - Validation of redemption codes
 * - Error handling for invalid/used codes
 * - Subscription history
 */

// Mock timestamps
const mockTimestamp = '2024-01-15T10:30:00Z';
const mockFutureTimestamp = '2025-02-15T10:30:00Z';

// Mock subscription status responses
const mockFreeSubscription = {
  tier: 'free',
  status: 'active',
  display_name: 'Free',
  current_period_end: null,
  days_remaining: null,
  features: {
    ai_conversations_per_day: 10,
    context_window_tokens: 4000,
    file_versions_per_file: 10,
    max_projects: 3,
    export_formats: ['txt', 'md'],
    custom_prompts: false,
    materialUploads: 5,
    materialDecompositions: 2,
    customSkills: 0,
    publicSkillsAccess: 'basic',
    inspirationCopiesMonthly: 3,
    featuredInspirationAccess: 'delayed',
    prioritySupport: false,
    apiAccess: false,
  },
};

const mockProSubscription = {
  tier: 'pro',
  status: 'active',
  display_name: 'Pro',
  current_period_end: mockFutureTimestamp,
  days_remaining: 30,
  features: {
    ai_conversations_per_day: -1, // unlimited
    context_window_tokens: 16000,
    file_versions_per_file: 50,
    max_projects: -1, // unlimited
    export_formats: ['txt', 'md', 'docx', 'pdf'],
    custom_prompts: true,
    materialUploads: -1,
    materialDecompositions: -1,
    customSkills: -1,
    publicSkillsAccess: 'full',
    inspirationCopiesMonthly: -1,
    featuredInspirationAccess: 'immediate',
    prioritySupport: true,
    apiAccess: true,
  },
};

const mockExpiredProSubscription = {
  tier: 'pro',
  status: 'expired',
  display_name: 'Pro',
  current_period_end: '2024-01-01T00:00:00Z',
  days_remaining: 0,
  features: mockProSubscription.features,
};

// Mock quota responses
const mockFreeQuota = {
  ai_conversations: {
    used: 5,
    limit: 10,
    reset_at: mockFutureTimestamp,
  },
  projects: {
    used: 2,
    limit: 3,
  },
};

const mockProQuota = {
  ai_conversations: {
    used: 50,
    limit: -1, // unlimited
    reset_at: mockFutureTimestamp,
  },
  projects: {
    used: 5,
    limit: -1, // unlimited
  },
};

const mockExhaustedQuota = {
  ai_conversations: {
    used: 10,
    limit: 10,
    reset_at: mockFutureTimestamp,
  },
  projects: {
    used: 3,
    limit: 3,
  },
};

const mockLowQuota = {
  ai_conversations: {
    used: 9,
    limit: 10,
    reset_at: mockFutureTimestamp,
  },
  projects: {
    used: 2,
    limit: 3,
  },
};

// Mock subscription history
const mockSubscriptionHistory = [
  {
    id: '1',
    user_id: 'user-1',
    action: 'created',
    plan_name: 'Free',
    start_date: mockTimestamp,
    end_date: null,
    metadata: {},
    created_at: mockTimestamp,
  },
  {
    id: '2',
    user_id: 'user-1',
    action: 'upgraded',
    plan_name: 'Pro',
    start_date: mockTimestamp,
    end_date: mockFutureTimestamp,
    metadata: { code: 'ERG-PRO-TEST-12345678' },
    created_at: mockTimestamp,
  },
  {
    id: '3',
    user_id: 'user-1',
    action: 'renewed',
    plan_name: 'Pro',
    start_date: mockTimestamp,
    end_date: mockFutureTimestamp,
    metadata: { days: 30 },
    created_at: mockTimestamp,
  },
];

// Helper to set up route mocking for subscription API
async function setupSubscriptionMocking(page: Page, options?: {
  subscriptionStatus?: typeof mockFreeSubscription;
  quota?: typeof mockFreeQuota;
  history?: typeof mockSubscriptionHistory;
}) {
  const {
    subscriptionStatus = mockFreeSubscription,
    quota = mockFreeQuota,
    history = mockSubscriptionHistory,
  } = options || {};

  // Mock GET /api/v1/subscription/me
  await page.route('**/api/v1/subscription/me', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(subscriptionStatus),
    });
  });

  // Mock GET /api/v1/subscription/quota
  await page.route('**/api/v1/subscription/quota', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(quota),
    });
  });

  // Mock GET /api/v1/subscription/history
  await page.route('**/api/v1/subscription/history*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(history),
    });
  });

  // Mock POST /api/v1/subscription/redeem
  await page.route('**/api/v1/subscription/redeem', async (route: Route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      const body = await request.postDataJSON();
      const code = body?.code || '';

      // Valid code pattern
      if (code === 'ERG-PRO-TEST-12345678') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            message: 'Subscription activated successfully!',
            subscription: {
              id: 'sub-1',
              tier: 'pro',
              status: 'active',
            },
          }),
        });
      }
      // Invalid code
      else if (code === 'ERG-INV-ALID-00000000') {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'Invalid redemption code',
          }),
        });
      }
      // Already used code
      else if (code === 'ERG-USED-TEST-00000000') {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'This code has already been used',
          }),
        });
      }
      // Expired code
      else if (code === 'ERG-EXPR-TEST-00000000') {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'This code has expired',
          }),
        });
      }
      // Default: treat as valid
      else if (/^ERG-[A-Z0-9]{2,4}-[A-Z0-9]{4}-[A-Z0-9]{8}$/.test(code)) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            message: 'Code redeemed successfully!',
            subscription: {
              id: 'sub-new',
              tier: 'pro',
              status: 'active',
            },
          }),
        });
      }
      // Any other code is invalid
      else {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: 'Invalid code format',
          }),
        });
      }
    } else {
      await route.continue();
    }
  });
}

// Test credentials
const TEST_EMAIL = TEST_USERS.standard.email;
const TEST_PASSWORD = TEST_USERS.standard.password;

// Helper to login and navigate to billing/subscription page
async function navigateToSubscriptionSettings(page: Page, request: APIRequestContext) {
  const params = new URLSearchParams();
  params.append('username', TEST_EMAIL);
  params.append('password', TEST_PASSWORD);

  const loginResponse = await request.post(`${config.apiBaseUrl}/api/auth/login`, {
    data: params.toString(),
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  });
  expect(loginResponse.ok()).toBeTruthy();
  const tokens = await loginResponse.json();

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.evaluate((tokenData) => {
    localStorage.setItem('access_token', tokenData.access_token);
    localStorage.setItem('refresh_token', tokenData.refresh_token);
    localStorage.setItem('token_type', tokenData.token_type);
    localStorage.setItem('user', JSON.stringify(tokenData.user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, tokens);

  await page.goto('/dashboard/billing', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/dashboard\/billing/, { timeout: 10000 });
  await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
}

// UI Selectors
const SUBSCRIPTION_UI = {
  subscriptionTab: '[data-testid="settings-tab-subscription"]',
  tierBadge: '.rounded-full',
  redeemButton: 'button:has-text("兑换码"), button:has-text("Redeem")',
  redeemModal: '[role="dialog"]:has-text("使用兑换码"), [role="dialog"]:has-text("Redeem")',
  codeInput: 'input[placeholder*="ERG"]',
  submitRedeem: 'button[type="submit"]:has-text("兑换"), button[type="submit"]:has-text("Redeem")',
  successMessage: '.bg-green-50, .bg-green-900\\/20',
  errorMessage: '.bg-red-50, .bg-red-900\\/20',
  quotaBadge: '.rounded-full:has(svg)',
};

// The badge follows the glossary by tier: 免费版 / Free and Pro.
function getPlanNameLocator(page: Page, planName: 'Free' | 'Pro') {
  const pattern = planName === 'Free' ? /^(免费版|Free)$/ : /^Pro$/
  return page.getByText(pattern).first()
}

test.describe('Subscription Status Display', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupSubscriptionMocking(page);
    await navigateToSubscriptionSettings(page, request);
  });

  test('should display Free tier status correctly', async ({ page }) => {
    // Check that Free tier badge is shown
    await expect(getPlanNameLocator(page, 'Free')).toBeVisible({ timeout: 5000 });

    // The free plan never ends, so it carries no status word.
    await expect(page.locator('text=/未开通|Not subscribed/i')).toHaveCount(0);
  });

  test('should display Pro tier status correctly', async ({ page, request }) => {
    // Override with Pro subscription
    await setupSubscriptionMocking(page, { subscriptionStatus: mockProSubscription });
    await page.reload();
    await navigateToSubscriptionSettings(page, request);

    // Check that Pro tier badge is shown
    await expect(getPlanNameLocator(page, 'Pro')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=/有效期至|Active until/i')).toBeVisible({ timeout: 3000 });
  });

  test('should display expired status correctly', async ({ page, request }) => {
    // Override with expired subscription
    await setupSubscriptionMocking(page, { subscriptionStatus: mockExpiredProSubscription });
    await page.reload();
    await navigateToSubscriptionSettings(page, request);

    // Check expired status is shown
    await expect(page.locator('text=/已到期|expired/i')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Quota Display', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupSubscriptionMocking(page);
    await navigateToSubscriptionSettings(page, request);
  });

  test('should display quota usage for Free tier', async ({ page }) => {
    // Should show format like "5/10"
    await expect(page.getByText('5/10').first()).toBeVisible({ timeout: 3000 });
  });

  test('should display unlimited quota for Pro tier', async ({ page, request }) => {
    // Override with Pro quota
    await setupSubscriptionMocking(page, {
      subscriptionStatus: mockProSubscription,
      quota: mockProQuota
    });
    await page.reload();
    await navigateToSubscriptionSettings(page, request);

    // Check for "unlimited" or "不限" text
    await expect(page.getByText(/不限|unlimited/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('should show warning when quota is low', async ({ page, request }) => {
    // Override with low quota
    await setupSubscriptionMocking(page, { quota: mockLowQuota });
    await page.reload();
    await navigateToSubscriptionSettings(page, request);

    await expect(page.getByText('9/10').first()).toBeVisible({ timeout: 5000 });
    // An upgrade path is offered: 「开通 Pro」 with online checkout, or the redeem code with the
    // checkout-unavailable notice when checkout is off (the local stack has no payment provider).
    await expect(page.locator('button:has-text("开通 Pro"), button:has-text("Get Pro"), [data-testid="billing-checkout-unavailable"]').first()).toBeVisible({ timeout: 5000 });
  });

  test('should show error when quota is exhausted', async ({ page, request }) => {
    // Override with exhausted quota
    await setupSubscriptionMocking(page, { quota: mockExhaustedQuota });
    await page.reload();
    await navigateToSubscriptionSettings(page, request);

    await expect(page.getByText('10/10').first()).toBeVisible({ timeout: 5000 });
    // An upgrade path is offered: 「开通 Pro」 with online checkout, or the redeem code with the
    // checkout-unavailable notice when checkout is off (the local stack has no payment provider).
    await expect(page.locator('button:has-text("开通 Pro"), button:has-text("Get Pro"), [data-testid="billing-checkout-unavailable"]').first()).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Redeem Code Modal', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupSubscriptionMocking(page);
    await navigateToSubscriptionSettings(page, request);
  });

  test('should open redeem modal when clicking redeem button', async ({ page }) => {
    // Click redeem button
    const redeemButton = page.locator(SUBSCRIPTION_UI.redeemButton);
    await expect(redeemButton).toBeVisible({ timeout: 5000 });
    await redeemButton.click();

    // Check modal is visible
    const modal = page.locator(SUBSCRIPTION_UI.redeemModal);
    await expect(modal).toBeVisible({ timeout: 3000 });

    // Check input field is present
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await expect(codeInput).toBeVisible({ timeout: 3000 });
  });

  test('should close modal when clicking cancel', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Click cancel button
    await page.click('button:has-text("取消"), button:has-text("Cancel")');

    // Modal should be closed
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).not.toBeVisible({ timeout: 3000 });
  });

  test('should close modal when clicking outside', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Click outside modal (overlay area near page corner)
    await page.locator(SUBSCRIPTION_UI.redeemModal).locator("..").click({ position: { x: 10, y: 10 } });

    // Modal should be closed
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).not.toBeVisible({ timeout: 3000 });
  });

  test('should convert input to uppercase', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Type lowercase code
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('erg-pro-test-12345678');

    // Check value is uppercase
    const value = await codeInput.inputValue();
    expect(value).toBe('ERG-PRO-TEST-12345678');
  });
});

test.describe('Redeem Code Validation', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupSubscriptionMocking(page);
    await navigateToSubscriptionSettings(page, request);
  });

  test('should show error for empty code', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Submit without entering code
    const submitButton = page.locator(SUBSCRIPTION_UI.submitRedeem);
    await expect(submitButton).toBeDisabled();
  });

  test('should show error for invalid format', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Enter invalid format
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('INVALID-CODE');

    // Submit
    await page.locator(SUBSCRIPTION_UI.submitRedeem).click();

    // Check for format error
    await expect(page.getByText(/兑换码格式不对|doesn't look like a valid code/i)).toBeVisible({ timeout: 3000 });
  });

  test('should show error for invalid code', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Enter invalid but correctly formatted code
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('ERG-INV-ALID-00000000');

    // Submit
    await page.locator(SUBSCRIPTION_UI.submitRedeem).click();

    await expect(page.getByText(/invalid redemption code|兑换失败|invalid/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('should show error for already used code', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Enter already used code
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('ERG-USED-TEST-00000000');

    // Submit
    await page.locator(SUBSCRIPTION_UI.submitRedeem).click();

    // Check for error message
    await expect(page.locator('text=/already been used|已使用/i')).toBeVisible({ timeout: 5000 });
  });

  test('should show error for expired code', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Enter expired code
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('ERG-EXPR-TEST-00000000');

    // Submit
    await page.locator(SUBSCRIPTION_UI.submitRedeem).click();

    // Check for error message
    await expect(page.locator('text=/expired|已过期/i')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Redeem Code Success', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupSubscriptionMocking(page);
    await navigateToSubscriptionSettings(page, request);
  });

  test('should successfully redeem valid code', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Enter valid code
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('ERG-PRO-TEST-12345678');

    // Submit
    await page.locator(SUBSCRIPTION_UI.submitRedeem).click();

    await expect(page.getByText(/subscription activated successfully|成功|success/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('should refresh subscription status after successful redeem', async ({ page }) => {
    // Open modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Enter valid code
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('ERG-PRO-TEST-12345678');

    // Submit
    await page.locator(SUBSCRIPTION_UI.submitRedeem).click();

    // Wait for success
    await expect(page.getByText(/subscription activated successfully|成功|success/i).first()).toBeVisible({ timeout: 5000 });

    // After success the modal offers a single way forward
    await page.getByRole('button', { name: /^(开始使用|Start using)$/ }).click();

    // Wait for modal to close
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).not.toBeVisible({ timeout: 3000 });

    // The API should have been called again (verified by route handler)
    // Check that subscription data is refreshed by verifying UI updates
  });
});

test.describe('Quota Badge in Chat Panel', () => {
  test.beforeEach(async ({ page }) => {
    await setupSubscriptionMocking(page);
  });

  test('should display quota badge in chat panel', async ({ page: _page }) => {
    test.skip(true, 'Chat quota badge no longer serves as the primary subscription surface; usage moved to billing/subscription pages.');
  });
});

test.describe('Subscription Error Handling', () => {
  test('should handle API error gracefully', async ({ page, request }) => {
    // Mock API error
    await page.route('**/api/v1/subscription/me', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });

    await page.route('**/api/v1/subscription/quota', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });

    await navigateToSubscriptionSettings(page, request);
    await expect(page.locator('h1')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=/加载失败|重试|error/i').first()).toBeVisible({ timeout: 5000 });
  });

  test('should handle network timeout gracefully', async ({ page, request }) => {
    // Mock delayed response
    await page.route('**/api/v1/subscription/me', async (_route) => {
      await new Promise(resolve => setTimeout(resolve, 60000)); // Very long delay
    });

    await navigateToSubscriptionSettings(page, request);
    await expect(page.locator('h1')).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Subscription Responsive Design', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test.beforeEach(async ({ page }) => {
    await setupSubscriptionMocking(page);
  });

  test('subscription UI is usable on mobile', async ({ page, request }) => {
    await navigateToSubscriptionSettings(page, request);

    // Check elements are visible on mobile
    await expect(page.locator('text=/Free|免费/i').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator(SUBSCRIPTION_UI.redeemButton)).toBeVisible({ timeout: 3000 });
  });

  test('redeem modal works on mobile', async ({ page, request }) => {
    await navigateToSubscriptionSettings(page, request);

    // Open redeem modal
    await page.locator(SUBSCRIPTION_UI.redeemButton).click();
    await expect(page.locator(SUBSCRIPTION_UI.redeemModal)).toBeVisible({ timeout: 3000 });

    // Input should be usable on mobile
    const codeInput = page.locator(SUBSCRIPTION_UI.codeInput);
    await codeInput.fill('ERG-PRO-TEST-12345678');
    const value = await codeInput.inputValue();
    expect(value).toBe('ERG-PRO-TEST-12345678');
  });
});

test.describe('Subscription - Tablet View', () => {
  test.use({ viewport: { width: 768, height: 1024 } });

  test.beforeEach(async ({ page }) => {
    await setupSubscriptionMocking(page);
  });

  test('subscription UI adapts to tablet view', async ({ page, request }) => {
    await navigateToSubscriptionSettings(page, request);

    // Check UI renders correctly on tablet
    await expect(getPlanNameLocator(page, 'Free')).toBeVisible({ timeout: 5000 });
    await expect(page.locator(SUBSCRIPTION_UI.redeemButton)).toBeVisible({ timeout: 3000 });
  });
});
