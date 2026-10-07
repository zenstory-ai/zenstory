import { expect, test, type Page } from '@playwright/test';
import { login } from './helpers/common';

const EDITOR_PLACEHOLDER = '开始你的创作...';
const NATURAL_POLISH_BUTTON = '去AI味';

async function getAccessToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem('access_token'));
  expect(token).toBeTruthy();
  return token as string;
}

async function createProjectAndFile(page: Page) {
  const token = await getAccessToken(page);
  const headers = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };

  const quotaResponse = await page.request.get('/api/v1/subscription/quota', { headers });
  expect(quotaResponse.ok()).toBeTruthy();
  const quota = await quotaResponse.json() as { projects?: { limit?: number } };
  const projectLimit = quota.projects?.limit;

  if (typeof projectLimit === 'number' && projectLimit >= 0) {
    const projectsResponse = await page.request.get('/api/v1/projects', { headers });
    expect(projectsResponse.ok()).toBeTruthy();
    const projects = await projectsResponse.json() as Array<{ id: string; updated_at?: string; created_at?: string }>;
    projects.sort(
      (a, b) =>
        new Date(a.updated_at || a.created_at || 0).getTime() -
        new Date(b.updated_at || b.created_at || 0).getTime()
    );

    while (projects.length >= projectLimit) {
      const project = projects.shift();
      if (!project?.id) continue;
      await page.request.delete(`/api/v1/projects/${project.id}`, { headers });
    }
  }

  const projectResponse = await page.request.post('/api/v1/projects', {
    headers,
    data: {
      name: `Natural polish E2E ${Date.now()}`,
      project_type: 'novel',
    },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json() as { id: string };

  const fileResponse = await page.request.post(`/api/v1/projects/${project.id}/files`, {
    headers,
    data: {
      title: '去AI味测试草稿',
      file_type: 'draft',
      content: '这是非常AI味的一段文案。它采用模板化表达，缺少真实口语感。',
    },
  });
  expect(fileResponse.ok()).toBeTruthy();
  const file = await fileResponse.json() as { id: string };

  return { token, projectId: project.id, fileId: file.id };
}

async function cleanupProject(page: Page, projectId: string | null, token: string | null) {
  if (!projectId || !token) return;
  await page.request.delete(`/api/v1/projects/${projectId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

test.describe('Natural polish', () => {
  test('regular users can run 去AI味 and apply reviewed changes', async ({ page }) => {
    test.slow();

    let projectId: string | null = null;
    let token: string | null = null;

    try {
      await login(page);

      const storedUser = await page.evaluate(() => {
        const raw = localStorage.getItem('user');
        return raw ? JSON.parse(raw) as { is_superuser?: boolean } : null;
      });
      expect(storedUser?.is_superuser ?? false).toBeFalsy();

      const created = await createProjectAndFile(page);
      projectId = created.projectId;
      token = created.token;

      const selectedText = '这是非常AI味的一段文案';
      const rewrittenText = '这是更自然的一段文案';
      let naturalPolishPayload: Record<string, unknown> | null = null;

      await page.route('**/api/v1/editor/natural-polish', async (route) => {
        naturalPolishPayload = route.request().postDataJSON() as Record<string, unknown>;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ text: rewrittenText }),
        });
      });

      await page.goto(`/project/${projectId}?file=${created.fileId}`);
      const editor = page.locator(`textarea[placeholder="${EDITOR_PLACEHOLDER}"]`).first();
      await expect(editor).toBeVisible({ timeout: 15000 });
      await expect(editor).toHaveValue(/这是非常AI味的一段文案。它采用模板化表达，缺少真实口语感。/);

      const naturalPolishButton = page.getByRole('button', { name: NATURAL_POLISH_BUTTON });
      await expect(naturalPolishButton).toBeVisible();
      await expect(naturalPolishButton).toBeDisabled();

      await editor.evaluate((el, phrase) => {
        el.focus();
        const start = (el as HTMLTextAreaElement).value.indexOf(phrase);
        const end = start + phrase.length;
        (el as HTMLTextAreaElement).setSelectionRange(start, end);
        el.dispatchEvent(new Event('select', { bubbles: true }));
        el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
      }, selectedText);

      await expect(naturalPolishButton).toBeEnabled();
      await naturalPolishButton.click();

      await expect.poll(() => naturalPolishPayload).not.toBeNull();
      expect(naturalPolishPayload).toMatchObject({
        project_id: projectId,
        selected_text: selectedText,
      });

      await expect(page.getByRole('button', { name: /完成审阅|应用更改/ })).toBeVisible({ timeout: 10000 });
      await expect(page.getByText('段落审阅', { exact: true })).toBeVisible();

      await page.screenshot({
        path: test.info().outputPath('natural-polish-review.png'),
        fullPage: true,
      });

      const saveResponsePromise = page.waitForResponse((response) =>
        response.url().includes(`/api/v1/files/${created.fileId}`) &&
        response.request().method() === 'PUT' &&
        response.ok()
      );

      await page.getByRole('button', { name: /完成审阅|应用更改/ }).click();
      await saveResponsePromise;

      const updatedEditor = page.locator(`textarea[placeholder="${EDITOR_PLACEHOLDER}"]`).first();
      await expect(updatedEditor).toBeVisible({ timeout: 10000 });
      await expect(updatedEditor).toHaveValue(/这是更自然的一段文案。它采用模板化表达，缺少真实口语感。/);

      await page.reload();
      await expect(page.locator(`textarea[placeholder="${EDITOR_PLACEHOLDER}"]`).first()).toHaveValue(
        /这是更自然的一段文案。它采用模板化表达，缺少真实口语感。/,
        { timeout: 15000 }
      );
    } finally {
      await cleanupProject(page, projectId, token);
    }
  });
});
