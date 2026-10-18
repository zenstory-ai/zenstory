import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test'
import { config } from './config'
import { login } from './helpers/common'
import {
  cleanupOwnedProject,
  createOwnedFile,
  createOwnedProject,
  type OwnedProject,
} from './fixtures/project-test-data'

/** Concurrent editing keeps one API-owned project per test to avoid the free project cap. */
const ENABLE_CONCURRENT_E2E = process.env.E2E_ENABLE_CONCURRENT_E2E === 'true'
const CONCURRENT_OPT_IN_MESSAGE = 'Concurrent editing E2E tests are opt-in. Set E2E_ENABLE_CONCURRENT_E2E=true to run.'

async function openOwnedFile(page: Page, project: OwnedProject, fileId: string): Promise<Locator> {
  await page.goto(`/project/${project.id}?file=${fileId}`)
  await page.waitForURL(new RegExp(`/project/${project.id}\\?file=${fileId}`))
  await expect(page.locator('[data-testid="file-tree-node"][data-file-type="folder"]').first()).toBeVisible()
  const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
  await expect(editor).toBeVisible()
  return editor
}

async function authenticateEditors(pages: Page[], project: OwnedProject): Promise<void> {
  // Keep the auth bootstrap sequential. Parallel dashboard navigations against
  // the single Vite worker occasionally leave one context's navigation pending.
  for (const page of pages) {
    await login(page, project.owner.email, project.owner.password)
  }
}

async function readOwnedFile(page: Page, project: OwnedProject, fileId: string) {
  const response = await page.request.get(`${config.apiBaseUrl}/api/v1/files/${fileId}`, {
    headers: project.headers,
  })
  expect(response.ok()).toBeTruthy()
  return await response.json() as { content: string; updated_at: string }
}

async function createConcurrentProject(page: Page, testInfo: TestInfo, files: string[]) {
  const project = await createOwnedProject(page, testInfo, 'concurrent')
  const ownedFiles = []
  for (const title of files) {
    ownedFiles.push(await createOwnedFile(page, project, { title, fileType: 'outline', parentFolderTitle: '大纲' }))
  }
  return { project, files: ownedFiles }
}

async function waitForSave(page: Page, fileId: string) {
  return page.waitForResponse((response) =>
    response.url().endsWith(`/api/v1/files/${fileId}`) && response.request().method() === 'PUT',
  )
}

function expectSaveRequest(
  response: Awaited<ReturnType<typeof waitForSave>>,
  expectedContent: string,
  expectedBaseUpdatedAt: string,
) {
  expect(response.request().postDataJSON()).toMatchObject({
    content: expectedContent,
    base_updated_at: expectedBaseUpdatedAt,
  })
}

async function expectConflictReview(page: Page) {
  await expect(page.getByText(/逐段审阅修改|review mode/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /全部接受|accept all/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /全部拒绝|reject all/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /应用更改|apply changes|完成审阅|finish review/i })).toBeVisible()
}

async function expectStaleWriteConflict(
  response: Awaited<ReturnType<typeof waitForSave>>,
  expectedServerContent: string,
  expectedBaseUpdatedAt: string,
) {
  expect(response.status()).toBe(409)
  const payload = await response.json() as {
    error_code: string
    error_detail: {
      reason: string
      current_content: string
      current_updated_at: string
      base_updated_at: string
    }
  }
  expect(payload).toMatchObject({
    error_code: 'ERR_RESOURCE_CONFLICT',
    error_detail: {
      reason: 'stale_write',
      current_content: expectedServerContent,
    },
  })
  expect(Date.parse(payload.error_detail.base_updated_at)).toBe(Date.parse(expectedBaseUpdatedAt))
  expect(Date.parse(payload.error_detail.current_updated_at)).toBeGreaterThan(Date.parse(expectedBaseUpdatedAt))
}

test.describe('Concurrent Editing', () => {
  test.skip(!ENABLE_CONCURRENT_E2E, CONCURRENT_OPT_IN_MESSAGE)

  test('two tabs editing the same file opens conflict review without overwriting the first save', async ({ browser, page: setupPage }, testInfo) => {
    const originalContent = '并发测试的原始服务端内容'
    const firstContent = '这是来自页面1的编辑内容'
    const staleContent = '这是来自页面2的编辑内容'
    const project = await createOwnedProject(setupPage, testInfo, 'concurrent')
    const file = await createOwnedFile(setupPage, project, {
      title: '并发测试文件',
      fileType: 'outline',
      parentFolderTitle: '大纲',
      content: originalContent,
    })
    const originalServerFile = await readOwnedFile(setupPage, project, file.id)
    const context1 = await browser.newContext()
    const context2 = await browser.newContext()
    const page1 = await context1.newPage()
    const page2 = await context2.newPage()

    try {
      await authenticateEditors([page1, page2], project)
      const [editor1, editor2] = await Promise.all([
        openOwnedFile(page1, project, file.id),
        openOwnedFile(page2, project, file.id),
      ])
      await expect(editor1).toHaveValue(originalContent)
      await expect(editor2).toHaveValue(originalContent)

      const save1 = waitForSave(page1, file.id)
      await editor1.fill(firstContent)
      const firstResponse = await save1
      expectSaveRequest(firstResponse, firstContent, originalServerFile.updated_at)
      expect(firstResponse.ok()).toBeTruthy()

      // Page 2 retains the earlier version token. Its save must not silently erase page 1.
      const save2 = waitForSave(page2, file.id)
      await editor2.fill(staleContent)
      const secondResponse = await save2
      expectSaveRequest(secondResponse, staleContent, originalServerFile.updated_at)
      await expectStaleWriteConflict(secondResponse, firstContent, originalServerFile.updated_at)
      await expectConflictReview(page2)

      // Merely opening the review must retain the exact winning server bytes.
      expect((await readOwnedFile(setupPage, project, file.id)).content).toBe(firstContent)
      await page1.reload()
      const persistedEditor = page1.locator('[data-editor-scroll-container="true"] textarea').first()
      await expect(persistedEditor).toHaveValue(firstContent)
    } finally {
      await context1.close()
      await context2.close()
      await cleanupOwnedProject(setupPage, project)
    }
  })

  test('auto-save does not overwrite concurrent edits', async ({ browser, page: setupPage }, testInfo) => {
    const originalContent = '自动保存并发测试的原始服务端内容'
    const firstContent = '初始内容 - 版本1'
    const staleContent = '并发内容 - 来自页面2'
    const project = await createOwnedProject(setupPage, testInfo, 'concurrent')
    const file = await createOwnedFile(setupPage, project, {
      title: '并发保存测试',
      fileType: 'outline',
      parentFolderTitle: '大纲',
      content: originalContent,
    })
    const originalServerFile = await readOwnedFile(setupPage, project, file.id)
    const context1 = await browser.newContext()
    const context2 = await browser.newContext()
    const page1 = await context1.newPage()
    const page2 = await context2.newPage()

    try {
      await authenticateEditors([page1, page2], project)
      const [editor1, editor2] = await Promise.all([
        openOwnedFile(page1, project, file.id),
        openOwnedFile(page2, project, file.id),
      ])
      await expect(editor1).toHaveValue(originalContent)
      await expect(editor2).toHaveValue(originalContent)

      const save1 = waitForSave(page1, file.id)
      await editor1.fill(firstContent)
      const firstResponse = await save1
      expectSaveRequest(firstResponse, firstContent, originalServerFile.updated_at)
      expect(firstResponse.ok()).toBeTruthy()

      const save2 = waitForSave(page2, file.id)
      await editor2.fill(staleContent)
      const secondResponse = await save2
      expectSaveRequest(secondResponse, staleContent, originalServerFile.updated_at)
      await expectStaleWriteConflict(secondResponse, firstContent, originalServerFile.updated_at)
      await expectConflictReview(page2)

      const persistedContent = (await readOwnedFile(setupPage, project, file.id)).content
      expect(persistedContent).toBe(firstContent)
      await page1.reload()
      const finalEditor = page1.locator('[data-editor-scroll-container="true"] textarea').first()
      await expect(finalEditor).toHaveValue(firstContent)
    } finally {
      await context1.close()
      await context2.close()
      await cleanupOwnedProject(setupPage, project)
    }
  })

  test('editing different files does not cause conflicts', async ({ browser, page: setupPage }, testInfo) => {
    const { project, files } = await createConcurrentProject(setupPage, testInfo, ['文件A', '文件B'])
    const context1 = await browser.newContext()
    const context2 = await browser.newContext()
    const page1 = await context1.newPage()
    const page2 = await context2.newPage()

    try {
      await authenticateEditors([page1, page2], project)
      const [editor1, editor2] = await Promise.all([
        openOwnedFile(page1, project, files[0].id),
        openOwnedFile(page2, project, files[1].id),
      ])
      const save1 = waitForSave(page1, files[0].id)
      const save2 = waitForSave(page2, files[1].id)
      await Promise.all([
        editor1.fill('文件A的内容 - 来自页面1'),
        editor2.fill('文件B的内容 - 来自页面2'),
      ])
      const [response1, response2] = await Promise.all([save1, save2])
      expect(response1.status()).toBeLessThan(400)
      expect(response2.status()).toBeLessThan(400)

      await expect(await openOwnedFile(page1, project, files[0].id)).toHaveValue(/文件A的内容/)
      await expect(await openOwnedFile(page1, project, files[1].id)).toHaveValue(/文件B的内容/)
    } finally {
      await context1.close()
      await context2.close()
      await cleanupOwnedProject(setupPage, project)
    }
  })
})
