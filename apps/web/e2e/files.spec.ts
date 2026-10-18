import { test, expect, type Response, type Locator, type Page } from '@playwright/test'
import { config } from './config'
import {
  cleanupOwnedProject,
  createOwnedFile,
  createOwnedProject,
  type OwnedFile,
  type OwnedProject,
} from './fixtures/project-test-data'

const ENABLE_FILES_E2E = process.env.E2E_ENABLE_FILES_E2E === 'true'
const FILES_OPT_IN_MESSAGE = 'Files E2E tests are opt-in. Set E2E_ENABLE_FILES_E2E=true to run.'

type TreeNode = {
  id: string
  title: string
  file_type: string
  parent_id: string | null
  children?: TreeNode[]
}

function treeNode(page: Page, title: string): Locator {
  return page.getByTestId('file-tree-node').filter({ has: page.getByText(title, { exact: true }) })
}

function editor(page: Page): Locator {
  return page.locator('[data-editor-scroll-container="true"] textarea')
}

function titleInput(page: Page): Locator {
  return page.locator('input[placeholder*="标题"], input[placeholder*="title" i]').first()
}

async function openOwnedProject(page: Page, project: OwnedProject): Promise<void> {
  await page.goto(`/project/${project.id}`)
  await expect(page).toHaveURL(new RegExp(`/project/${project.id}`))
  await expect(page.getByTestId('chat-input')).toBeVisible()
  await expect(page.getByTestId('file-tree-node').first()).toBeVisible()
}

async function readTree(page: Page, project: OwnedProject): Promise<TreeNode[]> {
  const response = await page.request.get(
    `${config.apiBaseUrl}/api/v1/projects/${project.id}/file-tree`,
    { headers: project.headers },
  )
  expect(response.ok()).toBeTruthy()
  return ((await response.json()) as { tree: TreeNode[] }).tree
}

function flattenTree(nodes: TreeNode[]): TreeNode[] {
  return nodes.flatMap(node => [node, ...flattenTree(node.children ?? [])])
}

async function readFile(page: Page, project: OwnedProject, fileId: string): Promise<OwnedFile> {
  const response = await page.request.get(`${config.apiBaseUrl}/api/v1/files/${fileId}`, {
    headers: project.headers,
  })
  expect(response.ok()).toBeTruthy()
  return await response.json() as OwnedFile
}

async function createThroughTree(page: Page, folderTitle: string, fileTitle: string): Promise<OwnedFile> {
  const folder = treeNode(page, folderTitle)
  await expect(folder).toBeVisible()
  await folder.hover()
  await folder.getByTestId('file-tree-create-child').click()
  const input = page.getByTestId('file-tree-new-item-input')
  await expect(input).toBeVisible()
  const responsePromise = page.waitForResponse(response =>
    response.url().includes('/api/v1/projects/')
      && response.url().endsWith('/files')
      && response.request().method() === 'POST',
  )
  await input.fill(fileTitle)
  await input.press('Enter')
  const response = await responsePromise
  expect(response.ok()).toBeTruthy()
  const created = await response.json() as OwnedFile
  await expect(treeNode(page, fileTitle)).toBeVisible()
  return created
}

async function saveEditor(page: Page, action: () => Promise<void>): Promise<Response> {
  const responsePromise = page.waitForResponse(response =>
    /\/api\/v1\/files\/[^/]+$/.test(new URL(response.url()).pathname)
      && response.request().method() === 'PUT',
  )
  await action()
  const response = await responsePromise
  expect(response.ok()).toBeTruthy()
  return response
}

async function prepareFile(
  page: Page,
  project: OwnedProject,
  title: string,
  content = '',
  parentFolderTitle = '大纲',
  fileType = 'outline',
): Promise<OwnedFile> {
  const file = await createOwnedFile(page, project, { title, content, parentFolderTitle, fileType })
  await openOwnedProject(page, project)
  return file
}

async function moveFileByApi(
  page: Page,
  project: OwnedProject,
  fileId: string,
  targetParentId: string,
): Promise<OwnedFile> {
  const response = await page.request.post(`${config.apiBaseUrl}/api/v1/files/${fileId}/move`, {
    headers: project.headers,
    data: { target_parent_id: targetParentId },
  })
  expect(response.ok()).toBeTruthy()
  return await response.json() as OwnedFile
}

test.describe('Files', () => {
  test.skip(!ENABLE_FILES_E2E, FILES_OPT_IN_MESSAGE)

  let project: OwnedProject | undefined

  test.beforeEach(async ({ page }, testInfo) => {
    project = await createOwnedProject(page, testInfo, 'E2E-files')
    await openOwnedProject(page, project)
  })

  test.afterEach(async ({ page }) => {
    await cleanupOwnedProject(page, project)
    project = undefined
  })

  test('user can create a new file', async ({ page }) => {
    const created = await createThroughTree(page, '大纲', '第一章')
    expect((await readFile(page, project!, created.id)).title).toBe('第一章')
  })

  test('user can create different file types', async ({ page }) => {
    const character = await createThroughTree(page, '角色', '主角张三')
    const lore = await createThroughTree(page, '设定', '世界观设定')
    expect((await readFile(page, project!, character.id)).file_type).toBe('character')
    expect((await readFile(page, project!, lore.id)).file_type).toBe('lore')
  })

  test('user can select and view file content', async ({ page }) => {
    await prepareFile(page, project!, '内容文件', '真实文件内容')
    await treeNode(page, '内容文件').click()
    await expect(editor(page)).toHaveValue('真实文件内容')
  })

  test('user can edit file title', async ({ page }) => {
    const file = await prepareFile(page, project!, '测试文件')
    await treeNode(page, file.title).click()
    await saveEditor(page, async () => {
      await titleInput(page).fill('新文件名')
      await titleInput(page).press('Tab')
    })
    await expect(treeNode(page, '新文件名')).toBeVisible()
    expect((await readFile(page, project!, file.id)).title).toBe('新文件名')
  })

  test('user can edit file content', async ({ page }) => {
    const file = await prepareFile(page, project!, '编辑测试文件')
    await treeNode(page, file.title).click()
    const content = '这是测试内容。用于验证编辑功能正常工作。'
    await saveEditor(page, () => editor(page).fill(content))
    await expect(editor(page)).toHaveValue(content)
    expect((await readFile(page, project!, file.id)).content).toBe(content)
  })

  test('auto-save works on content change', async ({ page }) => {
    const file = await prepareFile(page, project!, '自动保存测试')
    await treeNode(page, file.title).click()
    const content = `自动保存内容 ${Date.now()}`
    await saveEditor(page, () => editor(page).fill(content))
    await page.reload()
    await expect(treeNode(page, file.title)).toBeVisible()
    await treeNode(page, file.title).click()
    await expect(editor(page)).toHaveValue(content)
    expect((await readFile(page, project!, file.id)).content).toBe(content)
  })

  test('user can delete a file', async ({ page }) => {
    const file = await prepareFile(page, project!, '待删除文件')
    const node = treeNode(page, file.title)
    await node.hover()
    await node.getByTitle(/删除|delete/i).click()
    const responsePromise = page.waitForResponse(response =>
      response.url().endsWith(`/api/v1/files/${file.id}`)
        && response.request().method() === 'DELETE',
    )
    await page.getByRole('dialog').getByRole('button', { name: /删除|delete/i }).click()
    expect((await responsePromise).ok()).toBeTruthy()
    await expect(treeNode(page, file.title)).toHaveCount(0)
    const getResponse = await page.request.get(`${config.apiBaseUrl}/api/v1/files/${file.id}`, {
      headers: project!.headers,
    })
    expect(getResponse.status()).toBe(404)
  })

  test('user can expand and collapse folders', async ({ page }) => {
    const folder = treeNode(page, '大纲')
    await expect(folder.locator('svg.lucide-chevron-down')).toBeVisible()
    await folder.click()
    await expect(folder.locator('svg.lucide-chevron-right')).toBeVisible()
    await folder.click()
    await expect(folder.locator('svg.lucide-chevron-down')).toBeVisible()
  })

  test('file tree shows folder children count', async ({ page }) => {
    await createThroughTree(page, '大纲', '计数测试文件')
    await expect(treeNode(page, '大纲').locator('span.text-xs')).toHaveText('1')
  })

  test('version history shows file versions', async ({ page }) => {
    const file = await prepareFile(page, project!, '版本测试文件')
    await treeNode(page, file.title).click()
    await saveEditor(page, () => editor(page).fill('第一个版本的内容'))
    await page.getByRole('button', { name: /历史版本|历史|versions|history/i }).last().click()
    await expect(page.getByTestId('version-list')).toBeVisible()
    await expect(page.getByTestId('version-list-rows').locator('button').first()).toBeVisible()
  })

  test('user can switch between files', async ({ page }) => {
    await createOwnedFile(page, project!, { title: '文件A', content: '文件A的内容', parentFolderTitle: '大纲', fileType: 'outline' })
    await createOwnedFile(page, project!, { title: '文件B', content: '文件B的内容', parentFolderTitle: '大纲', fileType: 'outline' })
    await openOwnedProject(page, project!)
    await treeNode(page, '文件A').click()
    await expect(editor(page)).toHaveValue('文件A的内容')
    await treeNode(page, '文件B').click()
    await expect(editor(page)).toHaveValue('文件B的内容')
    await treeNode(page, '文件A').click()
    await expect(editor(page)).toHaveValue('文件A的内容')
  })

  test('empty folder shows empty state message', async ({ page }) => {
    await expect(treeNode(page, '正文').locator('..')).toContainText(/空文件夹|暂无文件|empty/i)
  })

  test('file tree persists across page reload', async ({ page }) => {
    const created = await createThroughTree(page, '大纲', '持久化测试文件')
    await page.reload()
    await expect(treeNode(page, created.title)).toBeVisible()
    expect(flattenTree(await readTree(page, project!)).some(node => node.id === created.id)).toBe(true)
  })

  test('skills folder is visible', async ({ page }) => {
    await expect(page.getByRole('button', { name: /技能|skills/i }).first()).toBeVisible()
  })

  test('reference library folder is visible', async ({ page }) => {
    await expect(treeNode(page, '素材')).toBeVisible()
  })

  test('folder hierarchy is displayed correctly', async ({ page }) => {
    for (const folderName of ['设定', '角色', '素材', '大纲', '正文']) {
      await expect(treeNode(page, folderName)).toHaveAttribute('data-file-type', 'folder')
    }
  })

  test('different file types show appropriate icons', async ({ page }) => {
    await createOwnedFile(page, project!, { title: '大纲文件', fileType: 'outline', parentFolderTitle: '大纲' })
    await createOwnedFile(page, project!, { title: '角色文件', fileType: 'character', parentFolderTitle: '角色' })
    await createOwnedFile(page, project!, { title: '设定文件', fileType: 'lore', parentFolderTitle: '设定' })
    await createOwnedFile(page, project!, { title: '正文文件', fileType: 'draft', parentFolderTitle: '正文' })
    await openOwnedProject(page, project!)
    await expect(treeNode(page, '大纲').locator('svg.lucide-folder')).toBeVisible()
    await expect(treeNode(page, '大纲文件').locator('svg.lucide-file-text')).toBeVisible()
    await expect(treeNode(page, '角色文件').locator('svg.lucide-users')).toBeVisible()
    await expect(treeNode(page, '设定文件').locator('svg.lucide-sparkles')).toBeVisible()
    await expect(treeNode(page, '正文文件').locator('svg.lucide-book-open')).toBeVisible()
  })

  test('API can move file to a different folder', async ({ page }) => {
    const file = await prepareFile(page, project!, '待移动文件')
    const target = flattenTree(await readTree(page, project!)).find(node => node.title === '角色')!
    await moveFileByApi(page, project!, file.id, target.id)
    expect((await readFile(page, project!, file.id)).parent_id).toBe(target.id)
  })

  test('API move response confirms the new parent', async ({ page }) => {
    const file = await prepareFile(page, project!, '确认测试文件')
    const target = flattenTree(await readTree(page, project!)).find(node => node.title === '角色')!
    const moved = await moveFileByApi(page, project!, file.id, target.id)
    expect(moved.id).toBe(file.id)
    expect(moved.parent_id).toBe(target.id)
  })

  test('API move to the same location is idempotent', async ({ page }) => {
    const file = await prepareFile(page, project!, '同位置测试')
    const moved = await moveFileByApi(page, project!, file.id, file.parent_id!)
    expect(moved.parent_id).toBe(file.parent_id)
    expect((await readFile(page, project!, file.id)).parent_id).toBe(file.parent_id)
  })

  test('API move is reflected in the file tree after refresh', async ({ page }) => {
    const file = await prepareFile(page, project!, '即时更新测试')
    const target = flattenTree(await readTree(page, project!)).find(node => node.title === '角色')!
    await moveFileByApi(page, project!, file.id, target.id)
    await page.reload()
    const targetWrapper = treeNode(page, target.title).locator('..')
    await expect(targetWrapper.getByTestId('file-tree-node').filter({ hasText: file.title })).toBeVisible()
    const sourceWrapper = treeNode(page, '大纲').locator('..')
    await expect(sourceWrapper.getByTestId('file-tree-node').filter({ hasText: file.title })).toHaveCount(0)
  })

  test('API move persists after page reload', async ({ page }) => {
    const file = await prepareFile(page, project!, '持久化移动测试')
    const target = flattenTree(await readTree(page, project!)).find(node => node.title === '角色')!
    await moveFileByApi(page, project!, file.id, target.id)
    await page.reload()
    await expect(treeNode(page, target.title).locator('..').getByTestId('file-tree-node').filter({ hasText: file.title })).toBeVisible()
    expect((await readFile(page, project!, file.id)).parent_id).toBe(target.id)
  })
})
