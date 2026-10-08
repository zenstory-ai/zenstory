import { expect, test, type Page } from '@playwright/test';

const AUTHENTICATED_ROUTE_PATTERN = /\/(project|dashboard|onboarding\/persona)/;
const TEST_EMAIL = process.env.E2E_TEST_EMAIL || 'e2e-test@example.com';
const TEST_PASSWORD = process.env.E2E_TEST_PASSWORD || 'E2eTestPassword123!';
const PERSONA_KEY_PREFIX = 'zenstory_onboarding_persona_v1';
const TOUR_KEY_PREFIX = 'zenstory:tours:v1';
const TOUR_FLAG_ENABLED = process.env.VITE_DASHBOARD_COACHMARK_TOUR_ENABLED === 'true';

async function gotoWithRetry(page: Page, url: string, timeout = 25000) {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
      return;
    } catch (error) {
      if (attempt === 2) throw error;
      await page.waitForTimeout(500 * attempt);
    }
  }
}

async function ensurePersonaOnboardingCompleted(page: Page) {
  await page.evaluate((keyPrefix) => {
    const rawUser = localStorage.getItem('user');
    if (!rawUser) return;
    const user = JSON.parse(rawUser) as { id?: string };
    if (!user.id) return;
    localStorage.setItem(
      `${keyPrefix}:${user.id}`,
      JSON.stringify({
        version: 1,
        completed_at: new Date().toISOString(),
        selected_personas: ['explorer'],
        selected_goals: ['finishBook'],
        experience_level: 'beginner',
        skipped: false,
      }),
    );
  }, PERSONA_KEY_PREFIX);
}

async function clearTourState(page: Page) {
  await page.evaluate((tourKeyPrefix) => {
    const rawUser = localStorage.getItem('user');
    if (!rawUser) return;
    const user = JSON.parse(rawUser) as { id?: string };
    if (!user.id) return;
    localStorage.removeItem(`${tourKeyPrefix}:${user.id}`);
  }, TOUR_KEY_PREFIX);
}

async function loginAndPrepare(page: Page) {
  await gotoWithRetry(page, '/login');
  await page.fill('#identifier', TEST_EMAIL);
  await page.fill('#password', TEST_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL(AUTHENTICATED_ROUTE_PATTERN, { timeout: 15000 });

  if (page.url().includes('/onboarding/persona')) {
    await ensurePersonaOnboardingCompleted(page);
  }

  await ensurePersonaOnboardingCompleted(page);
  await clearTourState(page);
  await gotoWithRetry(page, '/dashboard');
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 10000 });
}

async function startTourFromReplay(page: Page) {
  await page.getByRole('button', { name: /账户菜单|Account menu/ }).click();
  await page.getByRole('button', { name: /重新查看新手引导|Replay guide/ }).click();
}

test.describe('Dashboard coachmark tour', () => {
  test.skip(!TOUR_FLAG_ENABLED, 'Set VITE_DASHBOARD_COACHMARK_TOUR_ENABLED=true to run coachmark e2e');

  test.beforeEach(async ({ page }) => {
    await loginAndPrepare(page);
  });

  test('replay entry can launch the dashboard tour and advance through steps', async ({ page }) => {
    await startTourFromReplay(page);
    await expect(page.getByText('先选你要写什么')).toBeVisible();
    await page.getByRole('button', { name: '知道了' }).click();

    await expect(page.getByText('从一句核心冲突开始')).toBeVisible();
    await page.getByRole('button', { name: '下一步' }).click();

    await expect(page.getByText('没想法就先来这里')).toBeVisible();
    await page.getByRole('button', { name: '下一步' }).click();

    await expect(page.getByText('点这里就开始')).toBeVisible();
  });

  test('typing an idea skips the inspiration-library fallback step', async ({ page }) => {
    await startTourFromReplay(page);
    await expect(page.getByText('先选你要写什么')).toBeVisible();
    await page.getByRole('button', { name: '知道了' }).click();

    await expect(page.getByText('从一句核心冲突开始')).toBeVisible();
    await page.getByRole('textbox').fill('一个从背叛开局的短剧故事');
    await page.getByRole('button', { name: '下一步' }).click();

    await expect(page.getByText('点这里就开始')).toBeVisible();
    await expect(page.getByText('没想法就先来这里')).toHaveCount(0);
  });

  test('skip persists and replay entry can reopen the tour', async ({ page }) => {
    await startTourFromReplay(page);
    await expect(page.getByText('先选你要写什么')).toBeVisible();
    await page.getByRole('button', { name: '跳过引导' }).click();
    await expect(page.getByText('先选你要写什么')).not.toBeVisible();

    await startTourFromReplay(page);
    await expect(page.getByText('先选你要写什么')).toBeVisible();
  });
});
