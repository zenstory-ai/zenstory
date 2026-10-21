import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

for (const width of [390, 768, 1440]) {
  test(`${width}px: failed registration mail recovers the same account without logging in`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 960 });
    await page.addInitScript(() => { localStorage.clear(); localStorage.setItem('zenstory-language', 'zh'); });
    let corrected = false;
    let correctionBody: unknown;
    await page.route('**/api/auth/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      let status = 200;
      if (path.endsWith('/register-policy')) body = { invite_code_optional: true, variant: 'global_optional', rollout_percent: 100 };
      else if (path.endsWith('/register')) body = { email: 'writer@example.com', email_verified: false, verification_sent: false };
      else if (path.endsWith('/check-verification')) body = { email_verified: false, resend_cooldown_seconds: corrected ? 60 : 0, verification_code_ttl_seconds: corrected ? 300 : 0 };
      else if (path.endsWith('/resend-verification')) { status = 400; body = { error_code: 'ERR_AUTH_EMAIL_UNDELIVERABLE' }; }
      else if (path.endsWith('/registration-email')) {
        correctionBody = route.request().postDataJSON();
        corrected = true;
        body = { email: 'corrected@example.com', verification_sent: true };
      } else status = 401;
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto('/register?plan=pro');
    await page.getByLabel('邮箱', { exact: true }).fill('writer@example.com');
    await page.getByLabel('密码', { exact: true }).fill('OwnedPassword1!');
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: '创建账号', exact: true }).click();
    await expect(page.getByRole('heading', { name: '验证你的邮箱', exact: true })).toBeVisible();
    await expect(page.locator('p').filter({ hasText: '验证码暂未发送' }).first()).toBeVisible();
    await expect(page.getByRole('textbox')).toHaveCount(0);
    const resend = page.getByRole('button', { name: '重新发送验证码', exact: true });
    await expect(resend).toBeEnabled();
    await resend.click();
    await expect(page.getByText('无法向这个邮箱发送验证码，请检查并修改邮箱地址', { exact: true }).first()).toBeVisible();
    const directory = process.env.REGISTRATION_DETAIL_ARTIFACT_DIR;
    const screenshot = async (name: string) => {
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      if (directory) { await mkdir(directory, { recursive: true }); await page.screenshot({ path: `${directory}/${width}-${name}.png` }); }
    };
    await screenshot('delivery-failed');
    await page.getByRole('button', { name: '修改邮箱', exact: true }).click();
    await page.getByLabel('新邮箱', { exact: true }).fill('corrected@example.com');
    await page.getByLabel('注册密码', { exact: true }).fill('OwnedPassword1!');
    await screenshot('correct-address');
    await page.getByRole('button', { name: '修改并发送验证码', exact: true }).click();
    await expect(page.getByRole('heading', { name: '验证你的邮箱', exact: true })).toBeVisible();
    await expect(page.getByText(/输入发送到 corrected@example.com 的 6 位验证码/)).toBeVisible();
    expect(correctionBody).toMatchObject({ email: 'writer@example.com', new_email: 'corrected@example.com', password: 'OwnedPassword1!' });
    expect(page.url()).toMatch(/\/verify-email\?plan=pro$/);
    expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull();
    await screenshot('ready-to-verify');
  });
}
