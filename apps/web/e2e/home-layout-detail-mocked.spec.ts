import { test, expect } from '@playwright/test';
import { mockResponsiveApp, responsiveUser } from './fixtures-responsive';

const widths = [320, 390, 768, 1024, 1280, 1920];

for (const width of [390, 768, 1920]) {
  test(`a long account name leaves room for quota at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockResponsiveApp(page);
    const user = { ...responsiveUser, nickname: 'StorywriterWithAVeryLongUnbrokenDisplayName' };
    await page.addInitScript(user => localStorage.setItem('user', JSON.stringify(user)), user);
    await page.route('**/api/auth/me', route => route.fulfill({ json: user }));
    await page.goto('/dashboard');
    await expect(page.locator('h1')).toContainText(user.nickname);
    const heading = (await page.locator('h1').boundingBox())!;
    const section = (await page.locator('.dashboard-start').boundingBox())!;
    expect(heading.x).toBeGreaterThanOrEqual(section.x);
    expect(heading.x + heading.width).toBeLessThanOrEqual(section.x + section.width);
    expect(await page.locator('h1').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    if (width >= 768) {
      const quota = (await page.getByText('今日 AI 消息', { exact: false }).boundingBox())!;
      expect(heading.x + heading.width + 12).toBeLessThanOrEqual(quota.x);
    }
  });
}

for (const width of widths) {
  test(`dashboard sections share the same edges at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockResponsiveApp(page);
    await page.goto('/dashboard');
    await expect(page.getByTestId('project-card').first()).toBeVisible();
    const start = (await page.locator('.dashboard-start').boundingBox())!;
    const composer = (await page.getByTestId('dashboard-composer').boundingBox())!;
    const projects = (await page.getByTestId('project-card').first().locator('..').boundingBox())!;
    for (const area of [composer, projects]) {
      expect(Math.abs(area.x - start.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(area.x + area.width - start.x - start.width)).toBeLessThanOrEqual(1);
    }
    const formats = await page.locator('[data-tour-id="dashboard-project-type-tabs"] button').all();
    const action = (await page.getByTestId('create-project-button').boundingBox())!;
    for (const format of formats) {
      const bounds = (await format.boundingBox())!;
      const separated = bounds.x + bounds.width <= action.x || action.x + action.width <= bounds.x || bounds.y + bounds.height <= action.y || action.y + action.height <= bounds.y;
      expect(separated).toBe(true);
    }
  });

  for (const language of ['zh', 'en']) {
    test(`demo playback controls stay aligned below the scene choices at ${width}px in ${language}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await page.addInitScript(lang => localStorage.setItem('zenstory-language', lang), language);
      await page.goto('/');
      const pause = page.getByRole('button', { name: language === 'zh' ? '暂停演示' : 'Pause demo', exact: true });
      await pause.click();
      const resume = page.getByRole('button', { name: language === 'zh' ? '继续演示' : 'Resume demo', exact: true });
      await expect(resume).toBeVisible();
      const bounds = (await resume.boundingBox())!;
      const playback = page.getByTestId('home-demo-playback-controls');
      const progress = page.getByRole('progressbar', {
        name: language === 'zh' ? '演示播放进度' : 'Demo playback progress',
      });
      const progressBounds = (await progress.boundingBox())!;
      const positionBounds = (await playback.locator('span').last().boundingBox())!;
      const scenes = await page.locator('[role="region"] button[aria-pressed]').all();
      expect(scenes).toHaveLength(3);
      for (const scene of scenes) {
        const box = (await scene.boundingBox())!;
        expect(bounds.y - box.y - box.height).toBeGreaterThanOrEqual(12);
      }
      expect(Math.abs(bounds.y + bounds.height / 2 - progressBounds.y - progressBounds.height / 2)).toBeLessThanOrEqual(2);
      expect(Math.abs(bounds.y + bounds.height / 2 - positionBounds.y - positionBounds.height / 2)).toBeLessThanOrEqual(2);
      expect(await playback.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);

      const demo = page.getByRole('region', { name: language === 'zh' ? '查看演示' : 'View Demo' });
      const currentFile = language === 'zh' ? '第一章 · 废柴觉醒' : 'Chapter 1 · The Broken Meridian';
      for (const scene of scenes) {
        await scene.click();
        await expect(scene).toHaveAttribute('aria-pressed', 'true');
        await expect(demo).toContainText(currentFile);
        await expect(demo).not.toContainText('75%');
        await expect(demo).not.toContainText('Tab');
      }

      if (width === 390 || width === 1920) {
        await expect(page.getByTestId('home-demo-scene')).toHaveCSS('opacity', '1');
        await demo.screenshot({ path: testInfo.outputPath(`home-demo-${language}-${width}.png`) });
        const cards = page.getByTestId('home-project-type-card-novel').locator('..');
        await cards.screenshot({ path: testInfo.outputPath(`home-project-cards-${language}-${width}.png`) });
      }
      await resume.click();
      await expect(pause).toBeVisible();
    });
  }
}

for (const language of ['zh', 'en']) {
for (const width of [1024, 1280, 1920]) {
test(`project type cards share vertical anchors at ${width}px in ${language}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1080 });
  await page.addInitScript(language => localStorage.setItem('zenstory-language', language), language);
  await page.goto('/');

  const cards = ['novel', 'short', 'screenplay'].map(type => page.getByTestId(`home-project-type-card-${type}`));
  const cardBounds = await Promise.all(cards.map(card => card.boundingBox()));
  expect(new Set(cardBounds.map(bounds => Math.round(bounds!.height))).size).toBe(1);

  for (const slot of ['header', 'description', 'features']) {
    const slotBounds = await Promise.all(cards.map(card => card.locator(`[data-card-slot="${slot}"]`).boundingBox()));
    expect(Math.max(...slotBounds.map(bounds => bounds!.y)) - Math.min(...slotBounds.map(bounds => bounds!.y))).toBeLessThanOrEqual(1);
  }
  const firstFeatures = await Promise.all(cards.map(card => card.locator('[data-card-slot="features"] > span').first().boundingBox()));
  expect(Math.max(...firstFeatures.map(bounds => bounds!.y)) - Math.min(...firstFeatures.map(bounds => bounds!.y))).toBeLessThanOrEqual(1);
});

}
}
