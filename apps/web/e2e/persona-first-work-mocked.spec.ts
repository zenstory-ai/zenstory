import { test, expect, type Page } from '@playwright/test';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';
import { readFileSync } from 'node:fs';

const locale = (language: 'zh' | 'en') =>
  JSON.parse(readFileSync(new URL(`../public/locales/${language}/onboarding.json`, import.meta.url), 'utf-8'));
const zhOnboarding = locale('zh');
const enOnboarding = locale('en');

/**
 * New author: the profile page's type tap opens the first work directly
 * (feature/2026-11-09-persona-opens-first-work.md). Everything else keeps the
 * previous destination.
 */
type Creation = 'created' | 'ineligible' | 'fail' | 'stall';
type Call = { path: string; method: string; body: Record<string, unknown> | null };

const copy = { zh: zhOnboarding, en: enOnboarding };

async function personaFixture(page: Page, language: 'zh' | 'en', creation: Creation = 'created', options: { required?: boolean } = {}) {
  await mockResponsiveApp(page, null, 'free', language);
  const project = { ...responsiveProjects[0], name: language === 'zh' ? '我的小说' : 'My Novel' };
  let created = false;
  let profile: Record<string, unknown> | null = null;
  const calls: Call[] = [];
  const state = () => ({
    status: created ? 'created' : creation === 'ineligible' ? 'ineligible' : 'pending',
    project_id: created ? project.id : null, project_type: created ? project.project_type : null,
    file_id: created ? 'draft-0' : null, available: created, static_guidance: created,
    opened_at: null, first_manual_write_at: null, first_ai_requested_at: null,
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = method === 'GET' ? null : request.postDataJSON();
    calls.push({ path, method, body });
    const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path.endsWith('/persona/onboarding')) {
      if (method === 'PUT') {
        profile = { ...body, version: 1, completed_at: '2026-11-09T00:00:00Z', experience_level: 'beginner' };
        return json({ required: false, profile, rollout_at: '2025-01-01T00:00:00Z', new_user_window_days: 7 });
      }
      return json({ required: Boolean(options.required) && !profile, profile, rollout_at: '2025-01-01T00:00:00Z', new_user_window_days: 7 });
    }
    if (path === '/api/v1/workspace/first-work') {
      if (method === 'POST' && creation === 'fail') return json({ detail: 'Temporary failure' }, 503);
      if (method === 'POST' && creation === 'stall') return; // never answers
      if (method === 'POST' && creation === 'created') created = true;
      return json(state());
    }
    if (path === '/api/v1/workspace/first-work/open') return json(state());
    // A brand-new work: no conversation yet, one empty manuscript.
    if (path === '/api/v1/projects') return json(created ? [project] : []);
    if (path.endsWith('/recent')) return json([]);
    if (path.endsWith('/file-tree')) {
      return json({ tree: [{ id: 'folder-drafts', title: language === 'zh' ? '正文' : 'Drafts', file_type: 'folder', children: [
        { id: 'draft-0', title: language === 'zh' ? '正文' : 'Draft', file_type: 'draft', children: [] }] }] });
    }
    if (path === '/api/v1/files/draft-0') {
      return json({ id: 'draft-0', project_id: project.id, title: language === 'zh' ? '正文' : 'Draft', content: '', file_type: 'draft',
        parent_id: 'folder-drafts', order: 0, metadata: { creation_source: 'first_work' }, created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:00:00Z' });
    }
    if (path.includes('/subscription/quota')) {
      return json({ ai_conversations: { used: 0, limit: 3, remaining: 3, reset_at: '2026-11-09T16:00:00Z' }, projects: { used: created ? 1 : 0, limit: 3 },
        material_decompositions: { used: 0, limit: 0 }, skill_creates: { used: 0, limit: 3 }, inspiration_copies: { used: 0, limit: 0 } });
    }
    if (path.includes('/agent/stream')) return json({ detail: 'UNEXPECTED_AUTOMATIC_AI' }, 500);
    return route.fallback();
  });
  const posts = () => calls.filter(call => call.method === 'POST' && call.path === '/api/v1/workspace/first-work');
  const saves = () => calls.filter(call => call.method === 'PUT' && call.path.endsWith('/persona/onboarding'));
  return { calls, posts, saves };
}

// Two representative paths: a phone in Chinese and a desktop in English dark mode.
const cases = [
  { width: 390, language: 'zh', theme: 'light' },
  { width: 1280, language: 'en', theme: 'dark' },
] as const;
for (const { width, language, theme } of cases) {
  test.describe(`${language} browser locale`, () => {
    test.use({ locale: language });
    test(`type tap opens the first work ${language} ${width} ${theme}`, async ({ page }) => {
      const height = width < 768 ? 812 : 900;
      await page.setViewportSize({ width, height });
      await page.addInitScript(theme => localStorage.setItem('zenstory-theme', theme), theme);
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      const fixture = await personaFixture(page, language);
      const text = copy[language];

      await page.goto('/onboarding/persona');
      await expect(page.getByRole('heading', { level: 1, name: text.start.title })).toBeVisible();
      await expect(page.locator('html')).toHaveClass(new RegExp(theme));

      // One tap answers: every card and the skip link are full touch targets; on phones all
      // three cards are inside the first screen.
      for (const type of ['novel', 'short', 'screenplay'] as const) {
        const card = page.getByTestId(`onboarding-type-${type}`);
        await expect(card).toContainText(text.start.options[type].title);
        await expect(card).toContainText(text.start.options[type].promise);
        for (const platform of text.start.options[type].platforms) await expect(card).toContainText(platform);
        const box = (await card.boundingBox())!;
        expect(box.height).toBeGreaterThanOrEqual(44);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
        if (width < 768) expect(box.y + box.height).toBeLessThanOrEqual(height);
      }
      const skip = page.getByRole('button', { name: text.actions.skip, exact: true });
      expect((await skip.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

      // The preview is supplementary and desktop-only; it follows hover.
      const preview = page.getByTestId('onboarding-preview');
      if (width >= 1024) {
        await expect(preview).toBeVisible();
        await expect(preview).toContainText(text.start.options.novel.sampleTitle);
        await page.getByTestId('onboarding-type-screenplay').hover();
        await expect(preview).toContainText(text.start.options.screenplay.sampleTitle);
        for (const folder of text.start.options.screenplay.folders) await expect(preview).toContainText(folder);
      } else {
        await expect(preview).toBeHidden();
      }
      await page.screenshot({ path: test.info().outputPath('persona.png') });

      await page.getByTestId('onboarding-type-novel').click();
      await expect(page).toHaveURL(/\/project\/responsive-project-0\?file=draft-0$/, { timeout: 15_000 });
      expect(fixture.saves()).toHaveLength(1);
      expect(fixture.saves()[0].body).toMatchObject({ selected_personas: ['serial'], skipped: false });
      expect(fixture.posts()).toHaveLength(1);
      expect(fixture.posts()[0].body).toEqual({ preferred_type: 'novel' });

      // Phones land on the AI tab with the starter cards; wider screens show editor and AI side by side.
      await expect(page.getByTestId('starter-cards')).toBeVisible({ timeout: 15_000 });
      if (width < 768) {
        await expect(page.getByTestId('bottom-tabs').getByRole('tab', { selected: true })).toHaveAttribute('aria-controls', 'chat-panel');
      } else {
        await expect(page.getByTestId('first-work-guidance')).toBeVisible();
      }
      await page.screenshot({ path: test.info().outputPath('first-work.png') });

      expect(fixture.calls.filter(call => call.path.includes('/agent/stream'))).toHaveLength(0);
      expect(errors).toEqual([]);
    });
  });
}

test.describe('fallbacks', () => {
  test.use({ locale: 'zh' });

  for (const creation of ['ineligible', 'fail'] as const) {
    test(`a type tap without an available first work keeps the dashboard (${creation})`, async ({ page }) => {
      const fixture = await personaFixture(page, 'zh', creation);
      await page.goto('/onboarding/persona');
      await page.getByTestId('onboarding-type-short').click();
      await expect(page).toHaveURL(/\/dashboard$/);
      expect(fixture.posts()[0].body).toEqual({ preferred_type: 'short' });
      await expect(page.getByText(zhOnboarding.errors.saveFailed)).toHaveCount(0);
    });
  }

  test('a stalled creation stops waiting and keeps the dashboard', async ({ page }) => {
    test.setTimeout(45_000);
    const fixture = await personaFixture(page, 'zh', 'stall');
    await page.goto('/onboarding/persona');
    const started = Date.now();
    await page.getByTestId('onboarding-type-screenplay').click();
    // While it waits, the page shows the work is opening and accepts no second tap.
    await expect(page.getByTestId('onboarding-type-novel')).toBeDisabled();
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 20_000 });
    expect(Date.now() - started).toBeGreaterThanOrEqual(9_000);
    expect(fixture.posts()[0].body).toEqual({ preferred_type: 'screenplay' });
  });

  test('skipping looks around on the dashboard; the dashboard prepares the first work', async ({ page }) => {
    const fixture = await personaFixture(page, 'zh');
    await page.goto('/onboarding/persona');
    await page.getByRole('button', { name: zhOnboarding.actions.skip, exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    expect(fixture.saves()[0].body).toMatchObject({ selected_personas: [], skipped: true });
    await expect(page.getByTestId('first-work-entry')).toBeVisible();
    // Only the dashboard's own request, with no type chosen on this device.
    expect(fixture.posts().map(call => call.body)).toEqual([{ preferred_type: null }]);
  });

  test('a protected page the author was heading to keeps its route and creates no first work', async ({ page }) => {
    const fixture = await personaFixture(page, 'zh', 'created', { required: true });
    await page.goto('/dashboard/billing?plan=pro&source=landing');
    await expect(page).toHaveURL(/\/onboarding\/persona$/);
    await page.getByTestId('onboarding-type-novel').click();
    await expect(page).toHaveURL(/\/dashboard\/billing\?plan=pro&source=landing$/);
    expect(fixture.posts()).toHaveLength(0);
  });

  test('a double tap saves and creates once', async ({ page }) => {
    const fixture = await personaFixture(page, 'zh');
    await page.goto('/onboarding/persona');
    const card = page.getByTestId('onboarding-type-novel');
    await card.dblclick();
    await expect(page).toHaveURL(/\/project\/responsive-project-0\?file=draft-0$/);
    expect(fixture.saves()).toHaveLength(1);
    expect(fixture.posts()).toHaveLength(1);
  });
});
