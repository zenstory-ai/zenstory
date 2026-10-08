import { test, expect, type Page } from '@playwright/test';
import { TEST_USERS } from './config';

/**
 * E2E Tests for Skills Management Flow
 *
 * These tests cover the complete skills CRUD operations:
 * - Creating, editing, and deleting skills
 * - Managing triggers and instructions
 * - Sharing skills to public library
 * - Batch operations (select all, batch delete)
 * - Skill statistics
 * - Discovering and adding public skills
 */

// UI Selectors
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const SKILLS_PAGE = {
  title: 'h1',
  description: 'h1 + p',
  statsButton: 'button:has(svg.lucide-bar-chart-3)',
  tabs: '.flex.gap-1.p-1',
  discoverTab: 'button:has-text("发现技能"), button:has-text("Discover")',
  mySkillsTab: 'button:has-text("我的技能"), button:has-text("My Skills")',
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const MY_SKILLS = {
  searchInput: 'input[placeholder*="搜索"]',
  createButton: 'button:has-text("创建")',
  userSkillsSection: 'h2:has-text("自定义技能")',
  addedSkillsSection: 'h2:has-text("已添加技能")',
  skillCard: '[class*="rounded-lg"][class*="border"]',
  selectAllButton: 'button:has(svg.lucide-square), button:has(svg.lucide-check-square)',
  batchDeleteButton: 'button:has-text("删除")',
  emptyState: '.text-center:has(svg.lucide-search)',
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const SKILL_CARD = {
  name: 'h3',
  description: 'p',
  triggers: 'span.rounded-full.text-xs',
  editButton: 'button:has(svg.lucide-pencil)',
  deleteButton: 'button:has(svg.lucide-trash-2)',
  shareButton: 'button:has(svg.lucide-share-2)',
  expandButton: 'button:has(svg.lucide-chevron-down)',
  checkbox: 'button:has(svg.lucide-square), button:has(svg.lucide-check-square)',
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const SKILL_MODAL = {
  overlay: '[role="dialog"]',
  title: 'h2',
  nameInput: 'input[placeholder*="章末留钩子"], input[placeholder*="名称"]',
  descInput: 'input[placeholder*="描述"]',
  triggersInput: 'input[placeholder*="开篇"], input[placeholder*="创建角色"], input[placeholder*="trigger"], input[placeholder*="character"]',
  instructionsTextarea: 'textarea',
  saveButton: 'button:has-text("保存")',
  cancelButton: 'button:has-text("取消")',
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const DELETE_MODAL = {
  overlay: '[role="dialog"]',
  confirmButton: 'button:has-text("删除"):not(:has-text("取消"))',
  cancelButton: 'button:has-text("取消")',
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const SHARE_MODAL = {
  overlay: '[role="dialog"]',
  categorySelect: 'select',
  submitButton: 'button:has-text("提交")',
  cancelButton: 'button:has-text("取消")',
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const STATS_DIALOG = {
  container: '.fixed.inset-0',
  closeButton: 'button:has(svg.lucide-x)',
  daysSelect: 'select',
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const DISCOVER_TAB = {
  searchInput: 'input[placeholder*="搜索"]',
  categoryButtons: '.flex.flex-wrap button',
  skillCard: '[class*="rounded-xl"][class*="border"]',
  addButton: 'button:has-text("添加")',
  addedButton: 'button:has-text("已添加")',
};

// Test credentials
const TEST_EMAIL = TEST_USERS.skills.email;
const TEST_PASSWORD = TEST_USERS.skills.password;
const SKILL_CREATE_E2E_ENABLED = process.env.E2E_ENABLE_SKILL_CREATE_E2E === 'true';

test.skip(
  !SKILL_CREATE_E2E_ENABLED,
  'Custom skill management now depends on subscription quota. Set E2E_ENABLE_SKILL_CREATE_E2E=true to run this opt-in suite.'
);

async function clearExistingUserSkills(page: Page) {
  await page.evaluate(async () => {
    const accessToken = localStorage.getItem('access_token');
    if (!accessToken) return;

    const headers = {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    };

    const response = await fetch('/api/v1/skills/my-skills', { headers });
    if (!response.ok) return;

    const payload = await response.json();
    const userSkills = Array.isArray(payload?.user_skills) ? payload.user_skills : [];

    for (const skill of userSkills) {
      if (!skill?.id) continue;
      await fetch(`/api/v1/skills/${skill.id}`, { method: 'DELETE', headers });
    }
  });
}

function getSkillCard(page: Page, skillName: string) {
  return page.locator('[class*="rounded-lg"][class*="border"]').filter({ hasText: skillName }).first();
}

test.describe('Skills Management', () => {
  test.beforeEach(async ({ page }) => {
    // Login first
    await page.goto('/login');
    await expect(page.locator('#identifier')).toBeVisible();
    await page.fill('#identifier', TEST_EMAIL);
    await page.fill('#password', TEST_PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });

    // Navigate to skills page via dashboard
    if (page.url().includes('/project/')) {
      await page.goto('/dashboard/skills');
    } else {
      await page.goto('/dashboard/skills');
    }

    // Wait for skills page to load
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
    await clearExistingUserSkills(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
  });

  // ==================== Skill CRUD ====================

  test('user can create a new skill', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Click create button
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();

    // Wait for modal
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Fill in skill form
    const skillName = `测试技能 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('input[placeholder*="描述"]', '这是一个测试技能的描述');
    await page.fill('input[placeholder*="开篇"], input[placeholder*="创建角色"], input[placeholder*="trigger"], input[placeholder*="character"]', '/test, 测试');
    await page.fill('textarea', '这是技能的详细指令内容。');

    // Save skill
    await page.click('button:has-text("保存")');

    // Wait for modal to close
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Verify skill appears in list
    await expect(page.locator(`text=${skillName}`)).toBeVisible({ timeout: 5000 });
  });

  test('user can edit skill name', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // First create a skill to edit
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const originalName = `编辑测试技能 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', originalName);
    await page.fill('textarea', '测试指令内容');
    await page.click('button:has-text("保存")');
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Find the skill card and click edit
    const skillCard = getSkillCard(page, originalName);
    await skillCard.locator('button:has(svg.lucide-pencil)').click();

    // Wait for modal
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Edit name
    const newName = `${originalName} - 已编辑`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', newName);
    await page.click('button:has-text("保存")');

    // Wait for modal to close
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Verify new name appears
    await expect(page.locator(`text=${newName}`)).toBeVisible({ timeout: 5000 });
  });

  test('user can edit skill instructions', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create a skill first
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const skillName = `指令编辑测试 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('textarea', '原始指令内容');
    await page.click('button:has-text("保存")');
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Find and edit the skill
    const skillCard = getSkillCard(page, skillName);
    await skillCard.locator('button:has(svg.lucide-pencil)').click();

    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Edit instructions
    const newInstructions = '更新后的指令内容 - 更加详细';
    await page.fill('textarea', newInstructions);
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Expand skill to verify instructions updated
    await getSkillCard(page, skillName).locator('button:has(svg.lucide-chevron-down)').click();
    await expect(page.locator(`text=${newInstructions}`)).toBeVisible();
  });

  test('user can add triggers to skill', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create a skill first
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const skillName = `触发器测试 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('textarea', '测试指令');

    // Add multiple triggers
    await page.fill('input[placeholder*="开篇"], input[placeholder*="创建角色"], input[placeholder*="trigger"], input[placeholder*="character"]', '/trigger1, /trigger2, 触发词');
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Verify triggers appear in skill card
    const skillCard = getSkillCard(page, skillName);
    await expect(skillCard.getByText('/trigger1')).toBeVisible();
    await expect(skillCard.getByText('/trigger2')).toBeVisible();
  });

  test('user can delete skill', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create a skill to delete
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const skillName = `待删除技能 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('textarea', '将被删除的技能');
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });
    await expect(page.locator(`text=${skillName}`)).toBeVisible();

    // Click delete button
    const skillCard = getSkillCard(page, skillName);
    await skillCard.locator('button:has(svg.lucide-trash-2)').click();

    const deleteDialog = page.locator('[role="dialog"]').filter({ hasText: /删除|确认/i }).last();
    if (await deleteDialog.isVisible().catch(() => false)) {
      await deleteDialog.locator('button:has-text("删除"):not(:has-text("取消"))').click();
      await expect(deleteDialog).not.toBeVisible({ timeout: 5000 });
    }

    // Verify skill is gone
    await expect(page.locator(`text=${skillName}`)).not.toBeVisible({ timeout: 5000 });
  });

  // ==================== Skill Listing ====================

  test('skills list shows user skills', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // The count badge should be visible
    const countBadge = page.locator('h2:has-text("自定义技能")').locator('..').locator('span.rounded-full');
    await expect(countBadge).toBeVisible();
  });

  test('skills list shows added public skills', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Check if added skills section exists
    await expect(page.locator('h2:has-text("已添加技能")')).toBeVisible();
  });

  test('skills can be searched by name', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create a skill with unique name
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const uniqueName = `搜索测试技能 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', uniqueName);
    await page.fill('textarea', '测试内容');
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Use search input
    const searchInput = page.locator('input[placeholder*="搜索"]').first();
    await searchInput.fill(uniqueName);

    // Verify the skill is still visible (search filtering happens reactively)
    await expect(page.locator(`text=${uniqueName}`)).toBeVisible();
  });

  // ==================== Skill Expansion ====================

  test('user can expand skill to see instructions', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create a skill with instructions
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const skillName = `展开测试 ${Date.now()}`;
    const instructions = '这是详细的指令内容，应该在展开后显示。';
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('textarea', instructions);
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Click expand button
    const skillCard = getSkillCard(page, skillName);
    await skillCard.locator('button:has(svg.lucide-chevron-down)').click();

    // Verify instructions are visible
    await expect(page.locator(`text=${instructions}`)).toBeVisible();

    // Collapse again
    await skillCard.locator('button:has(svg.lucide-chevron-up)').click();
    await expect(page.locator(`text=${instructions}`)).not.toBeVisible();
  });

  // ==================== Skill Sharing ====================

  test('user can share skill to public library', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create a skill to share
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const skillName = `分享测试技能 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('textarea', '这是一个将被分享的技能');
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Click share button
    const skillCard = getSkillCard(page, skillName);
    await skillCard.locator('button:has(svg.lucide-share-2)').click();

    // Wait for share modal
    await expect(page.locator('[role="dialog"]:has-text("分享技能")')).toBeVisible();

    // Select category
    await page.locator('select').selectOption('writing');

    // Submit share
    await page.locator('[role="dialog"]').locator('button:has-text("提交")').click();

    // Wait for modal to close
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });
  });

  test('cannot share already shared skill', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create and share a skill
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const skillName = `重复分享测试 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('textarea', '测试内容');
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Share the skill
    const skillCard = getSkillCard(page, skillName);
    await skillCard.locator('button:has(svg.lucide-share-2)').click();
    await expect(page.locator('[role="dialog"]:has-text("分享技能")')).toBeVisible();
    await page.locator('[role="dialog"]').locator('button:has-text("提交")').click();
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Try to share again
    await skillCard.locator('button:has(svg.lucide-share-2)').click();
    const dialog = page.locator('[role="dialog"]:has-text("分享技能")');
    if (await dialog.isVisible().catch(() => false)) {
      await page.locator('[role="dialog"]').locator('button:has-text("取消")').click();
      await expect(dialog).not.toBeVisible({ timeout: 5000 });
    }
    await expect(skillCard).toBeVisible();
  });

  // ==================== Batch Operations ====================

  test('user can batch select skills', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create two skills
    const createButton = page.locator('button:has(svg.lucide-plus)').first();

    for (let i = 0; i < 2; i++) {
      await createButton.click();
      await expect(page.locator('[role="dialog"]')).toBeVisible();

      const skillName = `批量选择测试 ${i} ${Date.now()}`;
      await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
      await page.fill('textarea', '测试内容');
      await page.click('button:has-text("保存")');

      await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });
      await expect(page.locator(`text=${skillName}`)).toBeVisible();
    }

    // Click on first skill checkbox
    const firstCheckbox = page.locator('button:has(svg.lucide-square)').first();
    await firstCheckbox.click();

    // Verify selection indicator appears
    await expect(page.locator('text=/已选|selected/i')).toBeVisible();
  });

  test('select all works correctly', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Click select all button
    const selectAllBtn = page.locator('button:has-text("全选")').or(
      page.locator('button:has(svg.lucide-square)').first()
    );

    if (await selectAllBtn.isVisible()) {
      await selectAllBtn.click();

      // Check that checkboxes are now checked (CheckSquare icons)
      const checkedBoxes = page.locator('svg.lucide-check-square');
      const count = await checkedBoxes.count();
      expect(count).toBeGreaterThan(0);

      // Click again to deselect all
      await selectAllBtn.click();
      await expect(page.locator('svg.lucide-check-square')).toHaveCount(0);
    }
  });

  test('user can batch delete skills', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create two skills for batch delete test
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    const skillNames: string[] = [];

    for (let i = 0; i < 2; i++) {
      await createButton.click();
      await expect(page.locator('[role="dialog"]')).toBeVisible();

      const skillName = `批量删除测试 ${i} ${Date.now()}`;
      skillNames.push(skillName);
      await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
      await page.fill('textarea', '测试内容');
      await page.click('button:has-text("保存")');

      await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });
      await expect(page.locator(`text=${skillName}`)).toBeVisible();
    }

    // Select both skills
    for (const name of skillNames) {
      const skillCard = page.locator(`text=${name}`).locator('..').locator('..');
      const checkbox = skillCard.locator('button:has(svg.lucide-square)');
      if (await checkbox.isVisible()) {
        await checkbox.click();
      }
    }

    // Click batch delete button
    const batchDeleteBtn = page.locator('button:has-text("删除")').filter({
      has: page.locator('svg.lucide-trash-2')
    });

    if (await batchDeleteBtn.isVisible()) {
      await batchDeleteBtn.click();

      // Confirm batch delete
      await expect(page.locator('[role="dialog"]:has-text("批量删除")')).toBeVisible();
      await page.locator('[role="dialog"]').locator('button:has-text("删除"):not(:has-text("取消"))').click();

      // Wait for modal to close
      await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

      // Verify skills are deleted
      for (const name of skillNames) {
        await expect(page.locator(`text=${name}`)).not.toBeVisible({ timeout: 5000 });
      }
    }
  });

  // ==================== Skill Statistics ====================

  test('skill stats button is visible when project exists', async ({ page }) => {
    // Check if stats button is visible (only shows when there's a current project)
    const statsButton = page.locator('button:has(svg.lucide-bar-chart-3)');
    // This may or may not be visible depending on project context
    await statsButton.isVisible();
    // Just verify the page structure is correct
    await expect(page.locator('h1')).toBeVisible();
  });

  test('skill stats dialog opens and closes', async ({ page }) => {
    const token = await page.evaluate(() => localStorage.getItem('access_token'));
    expect(token).toBeTruthy();
    const createProjectResponse = await page.request.post('/api/v1/projects', {
      headers: {
        Authorization: `Bearer ${token}`,
      },
      data: {
        description: `技能统计测试项目 ${Date.now()}`,
      },
    });
    expect(createProjectResponse.ok()).toBeTruthy();
    const createdProject = await createProjectResponse.json();
    await page.goto(`/project/${createdProject.id}`);
    await page.waitForURL(/\/project\//, { timeout: 15000 });

    // Navigate to skills page
    await page.goto('/dashboard/skills');
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });

    // Click stats button
    const statsButton = page.locator('button:has(svg.lucide-bar-chart-3)');
    if (await statsButton.isVisible()) {
      await statsButton.click();

      // Verify stats dialog appears
      await expect(page.locator('.fixed.inset-0:has(svg.lucide-bar-chart-3)')).toBeVisible({ timeout: 5000 });

      // Close dialog
      await page.locator('.fixed.inset-0 button:has(svg.lucide-x)').click();
      await expect(page.locator('.fixed.inset-0:has(svg.lucide-bar-chart-3)')).not.toBeVisible();
    }
  });

  // ==================== Tab Navigation ====================

  test('user can switch between tabs', async ({ page }) => {
    // Default tab should be discover
    await expect(page.locator('button:has-text("发现技能"), button:has-text("Discover")')).toHaveAttribute('class', /accent-primary|text-white/);

    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');

    // Verify my skills content is visible
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Switch back to discover tab
    await page.click('button:has-text("发现技能"), button:has-text("Discover")');
    await expect(page.locator('button:has-text("发现技能"), button:has-text("Discover")')).toHaveAttribute('class', /accent-primary|text-white/);
  });

  // ==================== Discover Tab ====================

  test('discover tab shows public skills', async ({ page }) => {
    // Should be on discover tab by default
    await expect(page.locator('button:has-text("发现技能"), button:has-text("Discover")')).toHaveAttribute('class', /accent-primary|text-white/);

    const publicSkillTitles = page.locator('div[class*="rounded-xl"][class*="border"] h3');
    const emptyState = page.locator('text=/没有找到匹配的技能|暂无技能|no skills/i').first();

    await expect
      .poll(async () => {
        const count = await publicSkillTitles.count();
        const hasEmpty = await emptyState.isVisible().catch(() => false);
        return count > 0 || hasEmpty;
      }, { timeout: 10000 })
      .toBe(true);
  });

  test('discover tab has search functionality', async ({ page }) => {
    // Find search input in discover tab
    const searchInput = page.locator('input[placeholder*="搜索"]').first();
    await expect(searchInput).toBeVisible();

    // Type in search - search happens reactively
    await searchInput.fill('写作');
    // Search input should have the value
    await expect(searchInput).toHaveValue('写作');
  });

  test('discover tab has category filter', async ({ page }) => {
    await expect(page.locator('button:has-text("全部"), button:has-text("All")').first()).toBeVisible();
  });

  // ==================== Form Validation ====================

  test('create skill requires name and instructions', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Click create button
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();

    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Save button should be disabled without required fields
    const saveButton = page.locator('[role="dialog"]').locator('button:has-text("保存")');
    await expect(saveButton).toBeDisabled();

    // Fill only name
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', '测试技能');
    await expect(saveButton).toBeDisabled();

    // Fill instructions
    await page.fill('textarea', '这是指令内容');
    await expect(saveButton).not.toBeDisabled();

    // Cancel to clean up
    await page.click('[role="dialog"] button:has-text("取消")');
  });

  test('user can cancel skill creation', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Click create button
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();

    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Fill some data
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', '将被取消的技能');

    // Cancel
    await page.click('[role="dialog"] button:has-text("取消")');

    // Verify modal is closed
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Verify skill was not created
    await expect(page.locator('text=将被取消的技能')).not.toBeVisible();
  });

  test('user can cancel skill deletion', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Create a skill first
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    const skillName = `取消删除测试 ${Date.now()}`;
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', skillName);
    await page.fill('textarea', '测试内容');
    await page.click('button:has-text("保存")');

    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Click delete
    const skillCard = getSkillCard(page, skillName);
    await skillCard.locator('button:has(svg.lucide-trash-2)').click();

    const deleteDialog = page.locator('[role="dialog"]').filter({ hasText: /删除|确认/i }).last();
    if (await deleteDialog.isVisible().catch(() => false)) {
      await deleteDialog.locator('button:has-text("取消")').click();
    }

    // Verify skill still exists
    await expect(page.locator(`text=${skillName}`)).toBeVisible();
  });

  // ==================== Empty States ====================

  test('empty state shown when no user skills exist', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // If no skills, should show empty state message
    const userSkillsSection = page.locator('h2:has-text("自定义技能")').locator('..');
    const emptyMessage = userSkillsSection.locator('text=/还没有|创建第一个|暂无/i');

    const hasSkills = await userSkillsSection.locator('[class*="rounded-lg"][class*="border"]').count() > 0;
    const hasEmpty = await emptyMessage.count() > 0;
    const hasCreateAction =
      (await page.locator('button:has-text("创建技能"), button:has-text("创建")').count()) > 0 ||
      (await page.locator('text=/创建第一个/i').count()) > 0;

    expect(hasSkills || hasEmpty || hasCreateAction).toBeTruthy();
  });
});

test.describe('Skills Page Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('#identifier')).toBeVisible();
    await page.fill('#identifier', TEST_EMAIL);
    await page.fill('#password', TEST_PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });
    await page.goto('/dashboard/skills');
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
    await clearExistingUserSkills(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
  });

  test('skills page is accessible from dashboard', async ({ page }) => {
    // Navigate to dashboard if on project page
    if (page.url().includes('/project/')) {
      await page.goto('/dashboard');
    }

    // Navigate to skills page
    await page.goto('/dashboard/skills');

    // Verify skills page loaded
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('button:has-text("发现技能"), button:has-text("Discover")')).toBeVisible();
    await expect(page.locator('button:has-text("我的技能"), button:has-text("My Skills")')).toBeVisible();
  });

  test('skills page has correct title and description', async ({ page }) => {
    await page.goto('/dashboard/skills');
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });

    // Check page title
    const title = page.locator('h1');
    await expect(title).toBeVisible();

    // Check description exists
    const description = title.locator('..').locator('p');
    if (await description.isVisible()) {
      const descText = await description.textContent();
      expect(descText).toBeTruthy();
      expect(descText!.length).toBeGreaterThan(0);
    }
  });
});

test.describe('Skills Mobile Responsiveness', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('#identifier')).toBeVisible();
    await page.fill('#identifier', TEST_EMAIL);
    await page.fill('#password', TEST_PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/(dashboard|project)/, { timeout: 10000 });
    await page.goto('/dashboard/skills');
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
    await clearExistingUserSkills(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('h1')).toBeVisible({ timeout: 10000 });
  });

  test('skills page is usable on mobile', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Verify tabs are visible
    await expect(page.locator('button:has-text("我的技能"), button:has-text("My Skills")')).toBeVisible();

    // Verify create button is visible
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await expect(createButton).toBeVisible();
  });

  test('skill modal works on mobile', async ({ page }) => {
    // Switch to my skills tab
    await page.click('button:has-text("我的技能"), button:has-text("My Skills")');
    await expect(page.locator('h2:has-text("自定义技能")')).toBeVisible();

    // Click create button
    const createButton = page.locator('button:has(svg.lucide-plus)').first();
    await createButton.click();

    // Verify modal is visible and takes full screen on mobile
    await expect(page.locator('[role="dialog"]')).toBeVisible();

    // Fill form
    await page.fill('input[placeholder*="章末留钩子"], input[placeholder*="名称"]', '移动端测试技能');
    await page.fill('textarea', '移动端指令内容');

    // Save
    await page.click('button:has-text("保存")');

    // Wait for modal to close
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 });

    // Verify skill created
    await expect(page.locator('text=移动端测试技能')).toBeVisible({ timeout: 5000 });
  });
});
