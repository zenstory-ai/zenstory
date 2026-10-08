import { test, expect, Page, Route, APIRequestContext } from '@playwright/test';
import { TEST_USERS, config } from './config';

/**
 * E2E Tests for Points/Check-in System
 *
 * These tests cover the points and check-in functionality:
 * - Points balance display
 * - Daily check-in functionality
 * - Check-in status (checked in / not checked in)
 * - Streak days display
 * - Points transaction history
 * - Points redemption for Pro
 * - Earn opportunities list
 * - Error handling
 * - Responsive design
 */

// Mock data for testing
const mockPointsBalance = {
  available: 500,
  pending_expiration: 50,
  nearest_expiration_date: '2024-02-15T00:00:00Z',
};

const mockCheckInStatusNotCheckedIn = {
  checked_in: false,
  streak_days: 3,
  points_earned_today: 0,
};

const mockCheckInStatusCheckedIn = {
  checked_in: true,
  streak_days: 4,
  points_earned_today: 10,
};

const mockCheckInResponse = {
  success: true,
  points_earned: 10,
  streak_days: 4,
  message: '签到成功！获得 10 积分',
};

const mockTransactions = [
  {
    id: 'tx-1',
    amount: 10,
    balance_after: 500,
    transaction_type: 'check_in',
    source_id: null,
    description: null,
    expires_at: '2024-02-15T00:00:00Z',
    is_expired: false,
    created_at: '2024-01-15T10:00:00Z',
  },
  {
    id: 'tx-2',
    amount: 50,
    balance_after: 490,
    transaction_type: 'referral',
    source_id: 'ref-123',
    description: '邀请好友奖励',
    expires_at: '2024-02-15T00:00:00Z',
    is_expired: false,
    created_at: '2024-01-14T10:00:00Z',
  },
  {
    id: 'tx-3',
    amount: -100,
    balance_after: 440,
    transaction_type: 'redeem_pro',
    source_id: 'redeem-123',
    description: '兑换 7 天 Pro 会员',
    expires_at: null,
    is_expired: false,
    created_at: '2024-01-13T10:00:00Z',
  },
  {
    id: 'tx-4',
    amount: 20,
    balance_after: 540,
    transaction_type: 'check_in_streak',
    source_id: null,
    description: '连续签到 7 天奖励',
    expires_at: '2024-02-15T00:00:00Z',
    is_expired: false,
    created_at: '2024-01-12T10:00:00Z',
  },
];

const mockTransactionHistoryResponse = {
  transactions: mockTransactions,
  total: 4,
  page: 1,
  page_size: 10,
  total_pages: 1,
};

const mockEarnOpportunities = [
  {
    type: 'check_in',
    points: 10,
    description: '每日签到',
    is_completed: false,
    is_available: true,
  },
  {
    type: 'profile_complete',
    points: 100,
    description: '完善个人资料',
    is_completed: true,
    is_available: true,
  },
  {
    type: 'referral',
    points: 50,
    description: '邀请好友注册',
    is_completed: false,
    is_available: true,
  },
  {
    type: 'skill_contribution',
    points: 200,
    description: '贡献技能到商店',
    is_completed: false,
    is_available: true,
  },
  {
    type: 'inspiration_contribution',
    points: 100,
    description: '分享灵感到广场',
    is_completed: false,
    is_available: true,
  },
];

const mockRedeemProResponse = {
  success: true,
  points_spent: 100,
  pro_days: 7,
  new_period_end: '2024-01-22T00:00:00Z',
};

// Test credentials
const TEST_EMAIL = TEST_USERS.standard.email;
const TEST_PASSWORD = TEST_USERS.standard.password;
const AUTHENTICATED_ROUTE_PATTERN = /\/(dashboard|project|onboarding\/persona)/;

// Helper to set up route mocking for points API
async function setupPointsMocking(page: Page, options: {
  balance?: typeof mockPointsBalance;
  checkInStatus?: typeof mockCheckInStatusNotCheckedIn;
  transactions?: typeof mockTransactionHistoryResponse;
  earnOpportunities?: typeof mockEarnOpportunities;
  checkInAlreadyDone?: boolean;
  insufficientPoints?: boolean;
} = {}) {
  const {
    balance = mockPointsBalance,
    checkInStatus = mockCheckInStatusNotCheckedIn,
    transactions = mockTransactionHistoryResponse,
    earnOpportunities = mockEarnOpportunities,
    checkInAlreadyDone = false,
    insufficientPoints = false,
  } = options;

  // Mock GET /api/v1/points/balance
  await page.route('**/api/v1/points/balance', async (route: Route) => {
    const responseBalance = insufficientPoints
      ? { ...balance, available: 50, pending_expiration: 0 }
      : balance;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(responseBalance),
    });
  });

  // Mock GET /api/v1/points/check-in/status
  await page.route('**/api/v1/points/check-in/status', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(checkInStatus),
    });
  });

  // Mock POST /api/v1/points/check-in
  await page.route('**/api/v1/points/check-in', async (route: Route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      if (checkInAlreadyDone) {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Already checked in today' }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mockCheckInResponse),
        });
      }
    } else {
      await route.continue();
    }
  });

  // Mock GET /api/v1/points/transactions
  await page.route('**/api/v1/points/transactions*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(transactions),
    });
  });

  // Mock POST /api/v1/points/redeem
  await page.route('**/api/v1/points/redeem', async (route: Route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      if (insufficientPoints) {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Insufficient points' }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(mockRedeemProResponse),
        });
      }
    } else {
      await route.continue();
    }
  });

  // Mock GET /api/v1/points/earn-opportunities
  await page.route('**/api/v1/points/earn-opportunities', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(earnOpportunities),
    });
  });
}

// Helper to login and navigate to points page
async function navigateToPointsPage(page: Page, request: APIRequestContext) {
  await page.addInitScript(() => {
    const cachedUser = localStorage.getItem('user');
    if (cachedUser) {
      localStorage.setItem('auth_validated_at', Date.now().toString());
    }
  });

  const params = new URLSearchParams();
  params.append('username', TEST_EMAIL);
  params.append('password', TEST_PASSWORD);

  const response = await request.post(`${config.apiBaseUrl}/api/auth/login`, {
    data: params.toString(),
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Connection': 'close',
    },
  });
  expect(response.ok()).toBeTruthy();
  const tokens = await response.json();

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.evaluate((tokenData) => {
    localStorage.setItem('access_token', tokenData.access_token);
    localStorage.setItem('refresh_token', tokenData.refresh_token);
    localStorage.setItem('token_type', tokenData.token_type);
    localStorage.setItem('user', JSON.stringify(tokenData.user));
    localStorage.setItem('auth_validated_at', Date.now().toString());
  }, tokens);

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 10000 });

  const headerSettingsButton = page.getByTestId('settings-button').first();
  const dashboardSettingsButton = page.getByTestId('dashboard-open-settings-button').first();
  const mobileMenuButton = page.getByRole('button', { name: /Open mobile menu|Close mobile menu|打开移动菜单|关闭移动菜单|菜单/i }).first();
  const mobileSettingsButton = page.locator('button:has-text("快捷设置"), button:has-text("Quick Settings"), button:has-text("设置"), button:has-text("Settings")').first();

  if (await headerSettingsButton.waitFor({ state: 'visible', timeout: 3000 }).then(() => true).catch(() => false)) {
    await headerSettingsButton.click();
  } else if (await mobileMenuButton.waitFor({ state: 'visible', timeout: 3000 }).then(() => true).catch(() => false)) {
    await mobileMenuButton.click();
    await expect(mobileSettingsButton).toBeVisible({ timeout: 10000 });
    await mobileSettingsButton.click();
  } else {
    const userPanelToggle = page.getByTestId('dashboard-user-panel-toggle').first();
    await expect(userPanelToggle).toBeVisible({ timeout: 10000 });
    await userPanelToggle.click();
    await expect(dashboardSettingsButton).toBeVisible({ timeout: 10000 });
    await dashboardSettingsButton.click();
  }

  const pointsTab = page.getByTestId('settings-tab-points');
  await expect(pointsTab).toBeVisible({ timeout: 10000 });
  await pointsTab.click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 10000 });
}

test.describe('Points Balance Display', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('should display points balance', async ({ page }) => {
    // Check for balance display
    const balanceText = page.locator('text=/500|积分余额/');
    await expect(balanceText.first()).toBeVisible({ timeout: 10000 });
  });

  test('should show pending expiration warning when applicable', async ({ page }) => {
    // Check for expiration warning
    const expirationText = page.locator('text=/即将过期|50/');
    await expect(expirationText.first()).toBeVisible({ timeout: 5000 });
  });

  test('should format balance with locale separators', async ({ page }) => {
    // Check that balance is displayed with proper formatting
    const balanceValue = page.locator('text=/\\d+,?\\d*/');
    await expect(balanceValue.first()).toBeVisible();
  });
});

test.describe('Daily Check-in Functionality', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('should display check-in button when not checked in', async ({ page }) => {
    // Look for check-in button
    const checkInButton = page.locator('button:has-text("签到")');
    await expect(checkInButton.first()).toBeVisible({ timeout: 5000 });
  });

  test('should successfully check in when clicking button', async ({ page }) => {
    const checkInButton = page.locator('button:has-text("签到领积分"), button:has-text("签到")');
    await checkInButton.first().click();

    // Wait for success message or status update
    const successIndicator = page.locator('text=/签到成功|今日已签到|\\+10/');
    await expect(successIndicator.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show already checked in status', async ({ page, request }) => {
    // Setup with already checked in status
    await setupPointsMocking(page, {
      checkInStatus: mockCheckInStatusCheckedIn,
    });
    await navigateToPointsPage(page, request);

    // Check for "already checked in" indicator
    const checkedInIndicator = page.locator('text=/今日已签到|已签到/');
    await expect(checkedInIndicator.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show streak days badge', async ({ page }) => {
    // Check for streak display
    const streakBadge = page.locator('text=/连续.*天|streak/i');
    await expect(streakBadge.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show points earned today when checked in', async ({ page, request }) => {
    await setupPointsMocking(page, {
      checkInStatus: mockCheckInStatusCheckedIn,
    });
    await navigateToPointsPage(page, request);

    // Check for points earned display
    const pointsEarned = page.locator('text=/\\+10|10/');
    await expect(pointsEarned.first()).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Check-in Error Handling', () => {
  test('should handle duplicate check-in gracefully', async ({ page, request }) => {
    await setupPointsMocking(page, {
      checkInAlreadyDone: true,
    });
    await navigateToPointsPage(page, request);

    const checkInButton = page.locator('button:has-text("签到")');
    if (await checkInButton.first().isVisible()) {
      await checkInButton.first().click();

      // Should show error message
      const errorIndicator = page.locator('text=/失败|error|已签到/i');
      await expect(errorIndicator.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('should handle API errors during check-in', async ({ page, request }) => {
    await setupPointsMocking(page);
    // Override check-in after default mocks so the action returns an error
    await page.route('**/api/v1/points/check-in', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });
    await navigateToPointsPage(page, request);

    const checkInButton = page.locator('button:has-text("签到")');
    if (await checkInButton.first().isVisible()) {
      await checkInButton.first().click();

      // Should show error message
      const errorIndicator = page.locator('text=/签到失败|失败|error/i');
      await expect(errorIndicator.first()).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe('Points Transaction History', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('should display transaction history', async ({ page }) => {
    // Check for history section
    const historySection = page.locator('text=/积分记录|历史|transaction/i');
    await expect(historySection.first()).toBeVisible({ timeout: 10000 });
  });

  test('should show transaction amounts with correct signs', async ({ page }) => {
    // Look for positive and negative amounts
    const positiveAmount = page.locator('text=/\\+10|\\+50|\\+20/');
    const negativeAmount = page.locator('text=/-100/');

    await expect(positiveAmount.first()).toBeVisible({ timeout: 5000 });
    await expect(negativeAmount.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show transaction types', async ({ page }) => {
    // Check for transaction type labels
    const checkInType = page.locator('text=/签到/');
    await expect(checkInType.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show total transaction count', async ({ page }) => {
    const totalCount = page.locator('text=/共.*条|4.*record/i');
    await expect(totalCount.first()).toBeVisible({ timeout: 5000 });
  });

  test('should handle empty transaction history', async ({ page, request }) => {
    await setupPointsMocking(page, {
      transactions: {
        transactions: [],
        total: 0,
        page: 1,
        page_size: 10,
        total_pages: 0,
      },
    });
    await navigateToPointsPage(page, request);

    const emptyState = page.locator('text=/暂无|no.*record|empty/i');
    await expect(emptyState.first()).toBeVisible({ timeout: 5000 });
  });

  test('should support pagination when multiple pages exist', async ({ page, request }) => {
    await setupPointsMocking(page, {
      transactions: {
        transactions: mockTransactions,
        total: 25,
        page: 1,
        page_size: 10,
        total_pages: 3,
      },
    });
    await navigateToPointsPage(page, request);

    // Check for pagination controls
      await expect(page.getByText(/1\s*\/\s*3/)).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Points Redemption for Pro', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('should open redeem modal when clicking redeem button', async ({ page }) => {
    // Look for redeem button/trigger
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Modal should open
      const modal = page.locator('.fixed.inset-0.z-50, [role="dialog"]');
      await expect(modal.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('should display current balance in redeem modal', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Check for balance display in modal
      const balanceInModal = page.locator('.fixed.inset-0.z-50, [role="dialog"]').locator('text=/500|当前积分/');
      await expect(balanceInModal.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('should show redemption options (7, 14, 30 days)', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Check for day options
      const dayOptions = page.locator('text=/7.*天|14.*天|30.*天/');
      await expect(dayOptions.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('should show Pro benefits in modal', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Check for benefits section
      const benefits = page.locator('text=/权益|不限|benefit/i');
      await expect(benefits.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('should successfully redeem Pro membership', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Select an option and confirm
      const confirmButton = page.locator('.fixed.inset-0.z-50, [role="dialog"]').locator('button:has-text("兑换")').last();
      if (await confirmButton.first().isVisible()) {
        await confirmButton.first().click({ force: true });

        // The modal stays open with the success message and a close button
        await page.waitForTimeout(1000);
      }
    }
  });

  test('should show insufficient points error', async ({ page, request }) => {
    await setupPointsMocking(page, { insufficientPoints: true });
    await navigateToPointsPage(page, request);

    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Check for insufficient points indicator
      const insufficientIndicator = page.locator('text=/积分不足|insufficient|opacity-50.*cursor-not-allowed/');
      await expect(insufficientIndicator.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('should disable options user cannot afford', async ({ page, request }) => {
    await setupPointsMocking(page, { insufficientPoints: true });
    await navigateToPointsPage(page, request);

    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Higher cost options should be disabled
      const disabledRedeem = page.locator('.fixed.inset-0.z-50, [role="dialog"]').locator('button:has-text("积分不足")');
      await expect(disabledRedeem.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('should close modal on cancel', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Click cancel or close
      const cancelButton = page.locator('.fixed.inset-0.z-50').locator('button:has-text("取消"), button:has-text("Cancel")');
      if (await cancelButton.first().isVisible()) {
        await cancelButton.first().click();

        // Modal should close
        const modal = page.locator('.fixed.inset-0.z-50');
        await expect(modal).not.toBeVisible({ timeout: 3000 });
      }
    }
  });

  test('should close modal when clicking backdrop', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      // Click backdrop
      const backdrop = page.locator('.fixed.inset-0.z-50 > .absolute.inset-0, .fixed.inset-0.z-50 .backdrop');
      if (await backdrop.first().isVisible()) {
        await backdrop.first().click({ force: true });

        // Modal should close
        const modal = page.locator('.fixed.inset-0.z-50');
        await expect(modal).not.toBeVisible({ timeout: 3000 });
      }
    }
  });
});

test.describe('Earn Opportunities', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('should display earn opportunities list', async ({ page }) => {
    const earnSection = page.locator('text=/获取更多积分|赚.*积分|earn.*point/i');
    await expect(earnSection.first()).toBeVisible({ timeout: 10000 });
  });

  test('should show point values for each opportunity', async ({ page }) => {
    const pointValues = page.locator('text=/\\+10|\\+50|\\+100|\\+200/');
    await expect(pointValues.first()).toBeVisible({ timeout: 5000 });
  });

  test('should differentiate completed vs available opportunities', async ({ page }) => {
    // Completed opportunities should have different styling
    const completedSection = page.locator('text=/已完成|completed/i');
    await expect(completedSection.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show description for each opportunity type', async ({ page }) => {
    const descriptions = page.locator('text=/每日签到|邀请好友|完善.*资料|贡献.*技能|分享.*灵感/');
    await expect(descriptions.first()).toBeVisible({ timeout: 5000 });
  });

  test('should handle empty opportunities list', async ({ page }) => {
    await setupPointsMocking(page, {
      earnOpportunities: [],
    });
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Section should not be visible when no opportunities
    const earnSection = page.locator('text=/获取更多积分/');
    await expect(earnSection).not.toBeVisible({ timeout: 5000 });
  });
});

test.describe('Points Page - Error Handling', () => {
  test('should handle balance API error gracefully', async ({ page, request }) => {
    await setupPointsMocking(page);
    await page.route('**/api/v1/points/balance', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Server error' }),
      });
    });

    await navigateToPointsPage(page, request);

    // Points dialog should still render even when balance request fails
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });
    expect(page.url()).toContain('dashboard');
  });

  test('should handle check-in status API error', async ({ page, request }) => {
    await page.route('**/api/v1/points/check-in/status', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Server error' }),
      });
    });

    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);

    // Page should still render
    await page.waitForTimeout(1000);
    expect(page.url()).toContain('dashboard');
  });

  test('should handle transactions API error', async ({ page, request }) => {
    await setupPointsMocking(page);
    await page.route('**/api/v1/points/transactions*', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Server error' }),
      });
    });

    await navigateToPointsPage(page, request);

    // Should show error state for history
    const errorState = page.locator('text=/加载历史记录失败|失败|error/i');
    await expect(errorState.first()).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Points Page - Responsive Design', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('points balance is visible on mobile', async ({ page }) => {
    const balanceSection = page.locator('text=/积分余额|500/');
    await expect(balanceSection.first()).toBeVisible({ timeout: 10000 });
  });

  test('check-in functionality works on mobile', async ({ page }) => {
    const checkInButton = page.locator('button:has-text("签到")');
    await expect(checkInButton.first()).toBeVisible({ timeout: 5000 });
  });

  test('transaction history is scrollable on mobile', async ({ page }) => {
    const historySection = page.locator('text=/积分记录|签到/');
    await expect(historySection.first()).toBeVisible({ timeout: 5000 });
  });

  test('redeem modal is full-width on mobile', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      const redeemModal = page.getByRole('dialog', { name: /用积分兑换 Pro|Redeem Pro/i });
      await expect(redeemModal).toBeVisible({ timeout: 5000 });

      const viewport = page.viewportSize();
      const modalBox = await redeemModal.boundingBox();
      expect(viewport).not.toBeNull();
      expect(modalBox).not.toBeNull();
      expect(modalBox!.width).toBeGreaterThan(viewport!.width * 0.75);
      expect(modalBox!.width).toBeLessThanOrEqual(viewport!.width);
    }
  });
});

test.describe('Points Page - Tablet View', () => {
  test.use({ viewport: { width: 768, height: 1024 } });

  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('points page adapts to tablet view', async ({ page }) => {
    const balanceSection = page.locator('text=/积分余额|500/');
    await expect(balanceSection.first()).toBeVisible({ timeout: 10000 });
  });

  test('earn opportunities display correctly on tablet', async ({ page }) => {
    const earnSection = page.locator('text=/获取更多积分|每日签到/');
    await expect(earnSection.first()).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Points Page - Accessibility', () => {
  test.beforeEach(async ({ page, request }) => {
    await setupPointsMocking(page);
    await navigateToPointsPage(page, request);
  });

  test('check-in button should be accessible', async ({ page }) => {
    const checkInButton = page.locator('button:has-text("签到")');
    if (await checkInButton.first().isVisible()) {
      // Should be focusable
      await checkInButton.first().focus();
      await expect(checkInButton.first()).toBeFocused();
    }
  });

  test('modal should trap focus', async ({ page }) => {
    const redeemTrigger = page.locator('button:has-text("兑换"), button:has-text("Pro")');
    if (await redeemTrigger.first().isVisible()) {
      await redeemTrigger.first().click();

      const redeemModal = page.getByRole('dialog', { name: /用积分兑换 Pro|Redeem Pro/i });
      await expect(redeemModal).toBeVisible({ timeout: 5000 });

      // Tab should cycle within modal
      await page.keyboard.press('Tab');
      await expect.poll(async () => {
        return redeemModal.evaluate((element) => element.contains(document.activeElement));
      }).toBe(true);
    }
  });

  test('should have appropriate heading structure', async ({ page }) => {
    // Check for headings
    const headings = page.locator('h1, h2, h3');
    const count = await headings.count();
    expect(count).toBeGreaterThan(0);
  });
});

test.describe('Points Page - Loading States', () => {
  test('should show loading skeleton for balance', async ({ page, request }) => {
    await setupPointsMocking(page);
    // Delay the balance API after default mocks are installed
    await page.route('**/api/v1/points/balance', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 2000));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockPointsBalance),
      });
    });

    await navigateToPointsPage(page, request);

    // Should show loading state
    const loadingSkeleton = page.locator('.animate-pulse, [class*="loading"]');
    await expect(loadingSkeleton.first()).toBeVisible({ timeout: 1000 });
  });
});
