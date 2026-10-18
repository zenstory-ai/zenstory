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
  return await response.json() as { content: string }
}

async function createConcurrentProject(page: Page, testInfo: TestInfo, files: string[]) {
  const project = await createOwnedProject(page, testInfo, 'concurrent')
  const ownedFiles = []
  for (const title of files) {
    ownedFiles.push(await createOwnedFile(page, project, { title, fileType: 'outline', parentFolderTitle: '大纲' }))
  }
  return { project, files: ownedFiles }
}

async function waitForSave(page: Page) {
  return page.waitForResponse((response) =>
    response.url().includes('/api/v1/files/') && response.request().method() === 'PUT',
  )
}

test.describe('Concurrent Editing', () => {
  test.skip(!ENABLE_CONCURRENT_E2E, CONCURRENT_OPT_IN_MESSAGE)

  test('two tabs editing the same file opens conflict review without overwriting the first save', async ({ browser, page: setupPage }, testInfo) => {
    const { project, files } = await createConcurrentProject(setupPage, testInfo, ['并发测试文件'])
    const context1 = await browser.newContext()
    const context2 = await browser.newContext()
    const page1 = await context1.newPage()
    const page2 = await context2.newPage()

    try {
      await authenticateEditors([page1, page2], project)
      const [editor1, editor2] = await Promise.all([
        openOwnedFile(page1, project, files[0].id),
        openOwnedFile(page2, project, files[0].id),
      ])

      const save1 = waitForSave(page1)
      await editor1.fill('这是来自页面1的编辑内容')
      expect((await save1).ok()).toBeTruthy()

      // Page 2 retains the earlier version token. Its save must not silently erase page 1.
      const save2 = waitForSave(page2)
      await editor2.fill('这是来自页面2的编辑内容')
      const secondResponse = await save2
      expect([200, 204, 409]).toContain(secondResponse.status())
      if (secondResponse.status() === 409) {
        await expect(page2.getByText(/逐段审阅修改|review changes by paragraph/i)).toBeVisible()
        await expect(page2.getByRole('button', { name: /全部接受|accept all/i })).toBeVisible()
        await expect(page2.getByRole('button', { name: /全部拒绝|reject all/i })).toBeVisible()
      }

      expect((await readOwnedFile(setupPage, project, files[0].id)).content).toBe('这是来自页面1的编辑内容')
      await page1.reload()
      const persistedEditor = page1.locator('[data-editor-scroll-container="true"] textarea').first()
      await expect(persistedEditor).toHaveValue('这是来自页面1的编辑内容')
    } finally {
      await context1.close()
      await context2.close()
      await cleanupOwnedProject(setupPage, project)
    }
  })

  test('auto-save does not overwrite concurrent edits', async ({ browser, page: setupPage }, testInfo) => {
    const { project, files } = await createConcurrentProject(setupPage, testInfo, ['并发保存测试'])
    const context1 = await browser.newContext()
    const context2 = await browser.newContext()
    const page1 = await context1.newPage()
    const page2 = await context2.newPage()

    try {
      await authenticateEditors([page1, page2], project)
      const [editor1, editor2] = await Promise.all([
        openOwnedFile(page1, project, files[0].id),
        openOwnedFile(page2, project, files[0].id),
      ])
      const initialContent = '初始内容 - 版本1'
      const concurrentContent = '并发内容 - 来自页面2'

      const save1 = waitForSave(page1)
      await editor1.fill(initialContent)
      expect((await save1).ok()).toBeTruthy()

      const save2 = waitForSave(page2)
      await editor2.fill(concurrentContent)
      const secondResponse = await save2
      expect([200, 204, 409]).toContain(secondResponse.status())

      const persistedContent = (await readOwnedFile(setupPage, project, files[0].id)).content
      expect([initialContent, concurrentContent]).toContain(persistedContent)
      await page1.reload()
      const finalEditor = page1.locator('[data-editor-scroll-container="true"] textarea').first()
      await expect(finalEditor).toHaveValue(persistedContent)
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
      const save1 = waitForSave(page1)
      const save2 = waitForSave(page2)
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
