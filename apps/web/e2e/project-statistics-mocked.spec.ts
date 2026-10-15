import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import path from 'node:path';
import { mockResponsiveApp, responsiveProjects } from './fixtures-responsive';
import type { ProjectDashboardStatsResponse } from '../src/types/writingStats';

const projectId = responsiveProjects[0].id;
const baseline = process.env.STATS_COMPARISON_BASELINE === '1';
const artifactDir = process.env.STATS_COMPARISON_DIR;
const projectName = '归途：一部关于选择与重逢的短剧';
const facts: ProjectDashboardStatsResponse = {
  project_id: projectId, project_name: projectName, project_type: 'screenplay',
  total_word_count: 12860, words_today: 260, ai_words_today: 1200,
  words_this_week: 1800, ai_words_this_week: 7200, words_this_month: 3460, ai_words_this_month: 9400,
  chapter_completion: {
    total_chapters: 5, completed_chapters: 3, in_progress_chapters: 1,
    not_started_chapters: 26, planned_total: 30, completion_percentage: 10,
    chapter_details: [
      { outline_id: 'outline-1', draft_id: 'draft-1', title: '第1集 重逢', status: 'complete', word_count: 3200, target_word_count: 3000, completion_percentage: 100 },
      { outline_id: 'outline-2', draft_id: 'draft-2', title: '第2集 她没有回头', status: 'complete', word_count: 3100, target_word_count: 3000, completion_percentage: 100 },
      { outline_id: 'outline-3', draft_id: 'draft-3', title: '第3集 一封迟到的信', status: 'complete', word_count: 3000, target_word_count: 3000, completion_percentage: 100 },
      { outline_id: 'outline-4', draft_id: 'draft-4', title: '第4集 留下的人', status: 'in_progress', word_count: 3560, target_word_count: 4000, completion_percentage: 89 },
      { outline_id: 'outline-5', draft_id: null, title: '第5集 一个很长的标题，检查手机上是否仍能完整读到故事中的人物与转折', status: 'not_started', word_count: 0, target_word_count: null, completion_percentage: 0 },
    ],
  },
  streak: { current_streak: 3, longest_streak: 9, streak_status: 'active', days_until_break: null, last_writing_date: '2026-10-15', streak_start_date: '2026-10-13', streak_recovery_count: 0 },
  ai_usage: {
    current: { total_sessions: 4, active_session_id: 'stats-session', total_messages: 158, user_messages: 18, ai_messages: 25, tool_messages: 115, estimated_tokens: 12000, last_interaction_at: '2026-10-15T10:30:00Z', last_interaction_date: '2026-10-15T10:30:00Z' },
    today: { total: 20, user: 3, ai: 4, estimated_tokens: 2000 },
    this_week: { total: 82, user: 11, ai: 15, estimated_tokens: 7000 },
    this_month: { total: 158, user: 18, ai: 25, estimated_tokens: 12000 },
  },
  generated_at: '2026-10-15T10:30:00Z',
};

async function fixture(page: Page, locale = 'zh', theme = 'light') {
  await mockResponsiveApp(page);
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('zenstory-language', locale);
    localStorage.setItem('zenstory-theme', theme);
  }, { locale, theme });
  await page.route(/^https?:\/\/(?!127\.0\.0\.1(?::|\/)|localhost(?::|\/))/, route => route.abort());
  await page.route(`**/api/v1/projects/${projectId}/stats?**`, route => route.fulfill({ json: facts }));
  await page.route('**/api/v1/projects', route => route.fulfill({ json: [{ ...responsiveProjects[0], name: projectName, project_type: 'screenplay' }] }));
  const trends: string[] = [];
  await page.route('**/stats/word-count-trend?**', route => {
    trends.push(route.request().url());
    return route.fulfill({ json: {
      period: new URL(route.request().url()).searchParams.get('period'),
      days: 14, total_words_added: 1320, total_words_deleted: 70,
      data: [200, 0, -70, 360, 500, 120, 140, 260].map((net_words, index) => ({ date: `2026-10-${String(index + 8).padStart(2, '0')}`, period_label: null, word_count: 10000, words_added: Math.max(0, net_words), words_deleted: Math.max(0, -net_words), net_words })),
    } });
  });
  return trends;
}

for (const width of [390, 768, 1440]) for (const locale of ['zh', 'en']) for (const theme of ['light', 'dark']) {
  test(`statistics stay factual and readable at ${width}px ${locale} ${theme}`, async ({ page }) => {
    test.skip(baseline, 'The baseline screenshots have their own read-only capture below');
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const trends = await fixture(page, locale, theme);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`/project/${projectId}/dashboard`);
    await expect(page.getByRole('heading', { level: 1, name: locale === 'zh' ? '项目统计' : 'Project statistics' })).toBeVisible();
    await expect(page.getByText('12,860', { exact: true })).toBeVisible();
    expect(trends).toHaveLength(0);
    await expect(page.getByRole('button', { name: locale === 'zh' ? '本周' : 'This week', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await page.getByRole('button', { name: locale === 'zh' ? '本周' : 'This week', exact: true }).click();
    await expect(page.getByText('9,000', { exact: true })).toBeVisible();
    expect(trends).toHaveLength(0);
    const progress = page.getByRole('region', { name: locale === 'zh' ? '正文进度' : 'Manuscript progress' });
    const activity = page.getByRole('region', { name: locale === 'zh' ? '手写活动' : 'Handwritten activity' });
    await expect(progress.getByText(locale === 'zh' ? '3 集已完成' : '3 episodes finished', { exact: true })).toBeVisible();
    await expect(progress.getByText(locale === 'zh' ? '1 集写作中' : '1 episode in progress', { exact: true })).toBeVisible();
    await expect(page.getByText(locale === 'zh' ? '更多记录' : 'More records', { exact: true })).toHaveCount(0);
    const lastEdit = activity.getByText(locale === 'zh' ? '最近手写' : 'Last handwritten edit', { exact: true });
    await expect(lastEdit).toBeVisible();
    if (width === 390) {
      const lastEditBox = (await lastEdit.boundingBox())!;
      expect(lastEditBox.y + lastEditBox.height).toBeLessThanOrEqual(844);
    }
    if (width === 1440) {
      const progressBox = (await progress.boundingBox())!;
      const activityBox = (await activity.boundingBox())!;
      expect(activityBox.x).toBeGreaterThan(progressBox.x + progressBox.width);
      expect(Math.abs(activityBox.y - progressBox.y)).toBeLessThanOrEqual(16);
    }
    const chapterToggle = page.locator('[aria-controls="project-chapter-details"]');
    await expect(chapterToggle).toHaveText(locale === 'zh' ? '查看分集明细（5 条）' : 'View episode details (5)');
    const trendToggle = page.getByRole('button', { name: /手写字数趋势|net word trend/ });
    for (const toggle of [chapterToggle, trendToggle]) {
      await toggle.scrollIntoViewIfNeeded();
      await toggle.focus();
      await page.keyboard.press('Enter');
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
      if (width === 390) expect((await toggle.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    }
    await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '10');
    await expect(page.getByTestId('writing-trend-series')).toContainText('-70');
    expect(trends).toHaveLength(1);
    expect(new URL(trends[0]).searchParams.get('period')).toBe('weekly');
    expect(new URL(trends[0]).searchParams.get('days')).toBe('84');
    const reads = trends.length;
    await page.getByRole('button', { name: locale === 'zh' ? '刷新' : 'Refresh', exact: true }).click();
    await expect.poll(() => trends.length).toBeGreaterThan(reads);
    expect(await page.locator('html').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    expect(errors).toEqual([]);
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(axe.violations.filter(item => item.impact === 'critical' || item.impact === 'serious')).toEqual([]);
  });
}

for (const width of [320, 360]) for (const locale of ['zh', 'en']) {
  test(`million-word totals stay on one line without sideways scrolling at ${width}px ${locale}`, async ({ page }) => {
    test.skip(baseline, 'The baseline screenshots have their own read-only capture below');
    await page.setViewportSize({ width, height: 740 });
    await fixture(page, locale);
    await page.route(`**/api/v1/projects/${projectId}/stats?**`, route => route.fulfill({ json: {
      ...facts, total_word_count: 12345678, words_today: 1180000, ai_words_today: 98000,
    } }));
    await page.goto(`/project/${projectId}/dashboard`);
    for (const value of ['12,345,678', '1,278,000']) {
      const figure = page.getByText(value, { exact: true });
      await expect(figure).toBeVisible();
      const lineHeight = await figure.evaluate(element => parseFloat(getComputedStyle(element).lineHeight));
      expect((await figure.boundingBox())!.height).toBeLessThan(lineHeight * 1.5);
      expect(await figure.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    }
    const periodGroup = page.getByRole('group', { name: locale === 'zh' ? '字数周期' : 'Word count period' });
    expect(await periodGroup.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await page.locator('html').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  });
}

for (const width of [768, 1440]) {
  test(`English period labels stay on one line at ${width}px`, async ({ page }) => {
    test.skip(baseline, 'The baseline screenshots have their own read-only capture below');
    await page.setViewportSize({ width, height: 900 });
    await fixture(page, 'en');
    await page.goto(`/project/${projectId}/dashboard`);
    const buttons = page.getByRole('group', { name: 'Word count period' }).getByRole('button');
    await expect(buttons).toHaveCount(3);
    // Stretched flex items share a height, so count rendered text lines instead.
    const lines = await buttons.evaluateAll(items => items.map(button => {
      const range = document.createRange();
      range.selectNodeContents(button);
      return new Set([...range.getClientRects()].map(rect => Math.round(rect.top))).size;
    }));
    expect(lines).toEqual([1, 1, 1]);
  });
}

for (const width of [390, 1440]) {
  test(`comparison capture ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await fixture(page);
    await page.goto(`/project/${projectId}/dashboard`);
    await expect(page.getByText(projectName, { exact: true }).first()).toBeVisible();
    const name = `${baseline ? 'before' : 'after'}-${width}`;
    await page.screenshot({ path: artifactDir ? path.join(artifactDir, `${name}.png`) : test.info().outputPath(`${name}.png`) });
    await page.screenshot({ path: artifactDir ? path.join(artifactDir, `${name}-full.png`) : test.info().outputPath(`${name}-full.png`), fullPage: true });
  });
}
