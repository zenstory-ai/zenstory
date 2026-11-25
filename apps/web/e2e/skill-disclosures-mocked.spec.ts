import { test, expect } from '@playwright/test';

const user = { id: 'skill-preview-user', username: 'writer', email: 'writer@example.com', email_verified: true, is_active: true, is_superuser: false, ui_language: 'zh' };
const base = { category: 'writing', tags: [], author_id: null, status: 'approved', created_at: '2026-10-13T00:00:00Z', add_count: 32 };
const official = { ...base, id: 'official-1', name: '创建角色', source: 'official', description: '用欲望、顾虑和关系建立人物。', instructions: 'Official instruction content', instructions_truncated: true };
const community = { ...base, id: 'community-1', name: '对白节奏', source: 'community', description: '检查对白的节奏和人物声音。', instructions: 'Community preview', instructions_truncated: true };
const empty = { ...base, id: 'community-2', name: '暂无正文', source: 'community', description: 'Description only', instructions: '' };

for (const width of [390, 1280]) {
  test(`skill disclosures only offer useful content at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.addInitScript(value => {
      localStorage.setItem('access_token', 'mock-token');
      localStorage.setItem('refresh_token', 'mock-refresh');
      localStorage.setItem('user', JSON.stringify(value));
      localStorage.setItem('auth_validated_at', Date.now().toString());
    }, user);
    let added = 0;
    const details: string[] = [];
    await page.route('**/api/**', route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let body: unknown = {};
      if (path.endsWith('/auth/me')) body = user;
      if (path.endsWith('/projects')) body = [];
      if (path.endsWith('/subscription/me')) body = { tier: 'free', features: {} };
      if (path.endsWith('/skills/my-skills')) body = { user_skills: [], added_skills: [], total: 0 };
      if (path.endsWith('/public-skills/categories')) body = { categories: [] };
      if (path.endsWith('/public-skills')) body = { skills: [official, community, empty], total: 3, page: 1, page_size: 20 };
      if (path.endsWith('/public-skills/official-1/add')) { added++; body = { success: true }; }
      if (path.endsWith('/public-skills/community-1')) { details.push(path); body = { ...community, instructions: 'Complete community instructions' }; }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto('/dashboard/skills');
    await expect(page.getByRole('heading', { name: '创建角色', exact: true })).toBeVisible();
    const card = (name: string) => page.locator('div.flex.flex-col.h-full').filter({ has: page.getByRole('heading', { name, exact: true }) });
    const expand = /展开|Expand/;
    await expect(card('创建角色').getByRole('button', { name: expand })).toHaveCount(0);
    await expect(card('暂无正文').getByRole('button', { name: expand })).toHaveCount(0);
    await expect(page.getByText('官方技能添加后即可使用，AI 会在合适的时候调用。')).toHaveCount(1);
    await expect(page.getByText('Official instruction content')).toHaveCount(0);
    expect(details).toEqual([]);
    await card('对白节奏').getByRole('button', { name: expand }).click();
    await expect(page.getByText('Complete community instructions')).toBeVisible();
    expect(details).toEqual(['/api/v1/public-skills/community-1']);
    await card('创建角色').getByRole('button', { name: /添加|Add/ }).click();
    await expect(card('创建角色').getByRole('button', { name: /已添加|Added/ })).toBeDisabled();
    expect(added).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}
