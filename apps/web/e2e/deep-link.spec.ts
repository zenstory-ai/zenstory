import { test as base, expect, type Page } from '@playwright/test'
import { config, TEST_USERS } from './config'
import { login } from './helpers/common'
import {
  cleanupOwnedProject,
  createOwnedFile,
  createOwnedProject,
  gotoDashboardReady,
  type OwnedProject,
} from './fixtures/project-test-data'

const ENABLE_DEEP_LINK_E2E = process.env.E2E_ENABLE_DEEP_LINK_E2E === 'true'
const DEEP_LINK_OPT_IN_MESSAGE = 'Deep-link E2E tests are opt-in. Set E2E_ENABLE_DEEP_LINK_E2E=true to run.'

const test = base.extend<{ ownedProject: OwnedProject }>({
  ownedProject: async ({ page }, provide, testInfo) => {
    const project = await createOwnedProject(page, testInfo, 'deep-link')
    try {
      await gotoDashboardReady(page)
      await provide(project)
    } finally {
      await cleanupOwnedProject(page, project)
    }
  },
})

async function openProject(page: Page, project: OwnedProject, query = '') {
  await page.goto(`/project/${project.id}${query}`)
  await page.waitForURL(new RegExp(`/project/${project.id}`))
  await expect(page.locator('[data-testid="file-tree-node"][data-file-type="folder"]').first()).toBeVisible()
}

async function selectedEditor(page: Page) {
  const editor = page.locator('[data-editor-scroll-container="true"] textarea').first()
  await expect(editor).toBeVisible()
  return editor
}

test.describe('Deep Linking', () => {
  test.skip(!ENABLE_DEEP_LINK_E2E, DEEP_LINK_OPT_IN_MESSAGE)

  test('direct file URL loads correct file', async ({ page, ownedProject }) => {
    const file = await createOwnedFile(page, ownedProject, {
      title: '深度链接测试文件',
      content: '这是深度链接测试的内容',
    })
    await openProject(page, ownedProject, `?file=${file.id}`)
    await expect(await selectedEditor(page)).toHaveValue('这是深度链接测试的内容')
    await expect(page.locator(`[data-testid="file-tree-node"][data-file-id="${file.id}"]`)).toBeVisible()
  })

  test('unauthenticated deep link redirects to login then back', async ({ page, ownedProject }) => {
    const file = await createOwnedFile(page, ownedProject, {
      title: '登录后恢复的深链正文',
      content: '深链目标在登录后自动恢复，不由测试重新导航',
    })
    const projectUrl = `/project/${ownedProject.id}?file=${file.id}`
    await page.evaluate(() => localStorage.clear())
    await page.context().clearCookies()
    await page.goto(projectUrl)
    await expect(page).toHaveURL(/\/login/)

    await page.locator('#identifier').fill(ownedProject.owner.email)
    await page.locator('#password').fill(ownedProject.owner.password)
    await page.locator('button[type="submit"]').click()
    await expect(page).toHaveURL(new RegExp(`/project/${ownedProject.id}\\?file=${file.id}$`))
    await expect(await selectedEditor(page)).toHaveValue(file.content)
  })

  test('invalid file ID shows error', async ({ page, ownedProject }) => {
    await openProject(page, ownedProject, '?file=invalid-file-id-12345')
    await expect(page.getByTestId('file-tree-node').first()).toBeVisible()
    await expect(page.locator('[data-editor-scroll-container="true"] textarea')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: /开始写作|start writing/i })).toBeVisible()
    await expect(page.getByText(/打开一个文件|open a file/i)).toBeVisible()
  })

  test('unsupported session query does not interfere with a file link', async ({ page, ownedProject }) => {
    const file = await createOwnedFile(page, ownedProject, {
      title: '带额外参数的正文',
      content: '额外参数不会改变有效文件链接选择的正文',
    })
    // Session selection is not a supported URL contract. Only file chooses
    // content; an unknown session parameter must not break that valid link.
    await openProject(page, ownedProject, `?file=${file.id}&session=unsupported-session-id`)
    expect(page.url()).toContain(`file=${file.id}`)
    await expect(await selectedEditor(page)).toHaveValue(file.content)
    await expect(page.getByTestId('message-list')).toBeVisible()
    await expect(page.getByTestId('message-list').getByTestId('chat-message')).toHaveCount(0)
  })

  test('deep link to non-existent project shows error', async ({ page, ownedProject }) => {
    const fakeProjectId = 'non-existent-project-id-12345'
    const listResponse = await page.request.get(`${config.apiBaseUrl}/api/v1/projects`, {
      headers: ownedProject.headers,
    })
    expect(listResponse.ok()).toBeTruthy()
    const projects = (await listResponse.json()) as Array<{ id: string }>
    expect(projects.some((project) => project.id === fakeProjectId)).toBe(false)
    expect(ownedProject.id).not.toBe(fakeProjectId)
    await page.goto(`/project/${fakeProjectId}`)
    await expect(page.getByText(/找不到这个项目|project.*not found/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /再试一次|try again/i })).toBeVisible()
  })

  test('deep link preserves file selection across reload', async ({ page, ownedProject }) => {
    const marker = `重载前的内容-${Date.now()}`
    const file = await createOwnedFile(page, ownedProject, {
      title: '重载测试文件',
      content: marker,
    })
    await openProject(page, ownedProject, `?file=${file.id}`)
    await expect(await selectedEditor(page)).toHaveValue(marker)

    await page.reload()
    expect(page.url()).toContain(`file=${file.id}`)
    await expect(await selectedEditor(page)).toHaveValue(marker)
    await expect(page.locator(`[data-testid="file-tree-node"][data-file-id="${file.id}"]`)).toBeVisible()
  })
})

test.describe('Deep Link Security', () => {
  test.skip(!ENABLE_DEEP_LINK_E2E, DEEP_LINK_OPT_IN_MESSAGE)

  test('deep link to another user project fails', async ({ page, ownedProject }) => {
    await page.evaluate(() => localStorage.clear())
    const otherUser = TEST_USERS.pool.find((user) => user.email !== ownedProject.owner.email)
    if (!otherUser) throw new Error('Deep-link security test requires a second seeded pool user')
    await login(page, otherUser.email, otherUser.password)

    const accessToken = await page.evaluate(() => localStorage.getItem('access_token'))
    if (!accessToken) throw new Error('Second seeded user login did not yield an access token')
    const listResponse = await page.request.get(`${config.apiBaseUrl}/api/v1/projects`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    expect(listResponse.ok()).toBeTruthy()
    const visibleProjects = (await listResponse.json()) as Array<{ id: string }>
    expect(visibleProjects.some((project) => project.id === ownedProject.id)).toBe(false)

    await page.goto(`/project/${ownedProject.id}`)
    await expect.poll(() => page.url()).toMatch(/\/(project\/|dashboard|login)/)
    if (page.url().includes('/project/')) {
      await expect(page.getByText(/找不到这个项目|project.*not found/i)).toBeVisible()
    }
  })

  test('malformed deep link URL is handled gracefully', async ({ page, ownedProject }) => {
    const malformedUrls = [
      `/project/${ownedProject.id}?file=<script>alert(1)</script>`,
      `/project/${ownedProject.id}?file=../../../etc/passwd`,
      `/project/${ownedProject.id}?file=null`,
      `/project/${ownedProject.id}?file=undefined`,
    ]

    const dialogs: string[] = []
    const pageErrors: string[] = []
    page.on('dialog', async (dialog) => {
      dialogs.push(dialog.message())
      await dialog.dismiss()
    })
    page.on('pageerror', (error) => pageErrors.push(error.message))
    for (const url of malformedUrls) {
      await page.goto(url)
      await page.waitForLoadState('domcontentloaded')
      await expect(page.locator('body')).not.toContainText('Unhandled Runtime Error')
      await expect(page).toHaveURL(new RegExp(`/project/${ownedProject.id}`))
      await expect(page.locator('[data-editor-scroll-container="true"] textarea')).toHaveCount(0)
      await expect(page.getByRole('heading', { name: /开始写作|start writing/i })).toBeVisible()
      expect(dialogs).toEqual([])
      expect(pageErrors).toEqual([])
    }
  })
})
