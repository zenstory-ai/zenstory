import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test, expect, type Download, type Page } from '@playwright/test'
import { TEST_USERS, config } from './config'
import {
  authenticatedHeaders,
  cleanupOwnedProject,
  createOwnedFile,
  createOwnedProject,
  type OwnedProject,
} from './fixtures/project-test-data'

const ENABLE_EXPORT_E2E = process.env.E2E_ENABLE_EXPORT_E2E === 'true'
const EXPORT_OPT_IN_MESSAGE = 'Export E2E tests are opt-in. Set E2E_ENABLE_EXPORT_E2E=true to run.'
const EXPORT_BUTTON_NAME = /导出正文|Export manuscript/i

function exportButton(page: Page) {
  return page.getByRole('button', { name: EXPORT_BUTTON_NAME }).first()
}

async function openOwnedProject(page: Page, project: OwnedProject) {
  await page.goto(`/project/${project.id}`)
  await expect(page).toHaveURL(new RegExp(`/project/${project.id}`))
  await expect(page.getByTestId('chat-input')).toBeVisible()
  await expect(exportButton(page)).toBeVisible()
}

async function readDownload(download: Download): Promise<Buffer> {
  const directory = await mkdtemp(join(tmpdir(), 'zenstory-export-'))
  const path = join(directory, download.suggestedFilename())
  try {
    await download.saveAs(path)
    return await readFile(path)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
}

async function downloadExport(page: Page): Promise<Download> {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    exportButton(page).click(),
  ])
  return download
}

async function createDrafts(
  page: Page,
  project: OwnedProject,
  drafts: Array<{ title: string; content: string }>,
) {
  for (const draft of drafts) {
    const created = await createOwnedFile(page, project, draft)
    expect(created.title).toBe(draft.title)
    expect(created.content).toBe(draft.content)
  }
}

test.describe('Export Functionality', () => {
  test.skip(!ENABLE_EXPORT_E2E, EXPORT_OPT_IN_MESSAGE)

  let project: OwnedProject | undefined

  test.beforeEach(async ({ page }, testInfo) => {
    project = await createOwnedProject(page, testInfo, 'E2E-export')
    await openOwnedProject(page, project)
  })

  test.afterEach(async ({ page }) => {
    await cleanupOwnedProject(page, project)
    project = undefined
  })

  test('export button is visible in project view', async ({ page }) => {
    await expect(exportButton(page)).toBeVisible()
    await expect(exportButton(page)).toBeEnabled()
  })

  test('user can export drafts as TXT file', async ({ page }) => {
    await createDrafts(page, project!, [
      { title: '第一章', content: '这是第一章的内容。包含中文测试。' },
    ])

    const download = await downloadExport(page)
    const content = (await readDownload(download)).toString('utf8')
    expect(download.suggestedFilename()).toBe(`${project!.name}_正文.txt`)
    expect(content).toContain('第一章')
    expect(content).toContain('这是第一章的内容。包含中文测试。')
  })

  test('exported TXT has correct chapter separators', async ({ page }) => {
    await createDrafts(page, project!, [
      { title: '第一章', content: '第一章的内容' },
      { title: '第二章', content: '第二章的内容' },
    ])

    const content = (await readDownload(await downloadExport(page))).toString('utf8')
    expect(content).toContain('\n\n---\n\n')
    expect(content).toContain('第一章\n\n第一章的内容')
    expect(content).toContain('第二章\n\n第二章的内容')
  })

  test('exported TXT preserves chapter order', async ({ page }) => {
    await createDrafts(page, project!, [
      { title: '第三章', content: '第三章的内容' },
      { title: '第一章', content: '第一章的内容' },
      { title: '第二章', content: '第二章的内容' },
    ])

    const content = (await readDownload(await downloadExport(page))).toString('utf8')
    const chapters = ['第一章', '第二章', '第三章']
    const positions = chapters.map(title => content.indexOf(`${title}\n\n${title}的内容`))
    for (const [index, position] of positions.entries()) {
      expect(position, `${chapters[index]} title and content must be exported`).toBeGreaterThanOrEqual(0)
    }
    expect(positions[0]).toBeLessThan(positions[1])
    expect(positions[1]).toBeLessThan(positions[2])
  })

  test('exported TXT includes chapter titles', async ({ page }) => {
    await createDrafts(page, project!, [
      { title: '第一章 开端', content: '故事从这一天开始...' },
    ])

    const content = (await readDownload(await downloadExport(page))).toString('utf8')
    expect(content).toContain('第一章 开端')
    expect(content).toContain('故事从这一天开始...')
  })

  test('export shows error when no drafts exist', async ({ page }) => {
    let downloadStarted = false
    page.once('download', () => { downloadStarted = true })
    await exportButton(page).click()
    await expect(page.getByRole('alert')).toContainText(/没有正文|没有草稿|no manuscript|no drafts/i)
    expect(downloadStarted).toBe(false)
  })

  test('downloaded file has correct filename format', async ({ page }) => {
    await createDrafts(page, project!, [{ title: '第一章', content: '测试内容' }])
    const download = await downloadExport(page)
    expect(download.suggestedFilename()).toBe(`${project!.name}_正文.txt`)
  })

  test('downloaded file has UTF-8 BOM for Windows compatibility', async ({ page }) => {
    await createDrafts(page, project!, [{ title: '第一章', content: '测试内容' }])
    const buffer = await readDownload(await downloadExport(page))
    expect([...buffer.subarray(0, 3)]).toEqual([0xEF, 0xBB, 0xBF])
  })

  test('Chinese characters are preserved in export', async ({ page }) => {
    const chineseContent = '这是中文测试。包含标点符号：逗号，句号。还有引号"测试"。以及特殊字符①②③。'
    await createDrafts(page, project!, [{ title: '第一章', content: chineseContent }])
    const content = (await readDownload(await downloadExport(page))).toString('utf8')
    expect(content).toContain(chineseContent)
  })

  test('export handles large projects with 10+ chapters', async ({ page }) => {
    const drafts = Array.from({ length: 10 }, (_, index) => ({
      title: `第${index + 1}章`,
      content: `这是第${index + 1}章的内容。`,
    }))
    await createDrafts(page, project!, drafts)

    const content = (await readDownload(await downloadExport(page))).toString('utf8')
    for (const draft of drafts) {
      expect(content).toContain(`${draft.title}\n\n${draft.content}`)
    }
  })

  test('export button disabled during processing', async ({ page }) => {
    await createDrafts(page, project!, [{ title: '第一章', content: '测试内容' }])
    let releaseExport!: () => void
    const release = new Promise<void>(resolve => { releaseExport = resolve })
    let exportRequested!: () => void
    const requested = new Promise<void>(resolve => { exportRequested = resolve })
    await page.route(`**/api/v1/projects/${project!.id}/export/drafts*`, async route => {
      exportRequested()
      await release
      await route.continue()
    })

    const downloadPromise = page.waitForEvent('download')
    await exportButton(page).click()
    await requested
    await expect(exportButton(page)).toBeDisabled()
    releaseExport()
    await downloadPromise
    await expect(exportButton(page)).toBeEnabled()
  })

  test('export progress indicator shows during download', async ({ page }) => {
    await createDrafts(page, project!, [{ title: '第一章', content: '测试内容' }])
    let releaseExport!: () => void
    const release = new Promise<void>(resolve => { releaseExport = resolve })
    let exportRequested!: () => void
    const requested = new Promise<void>(resolve => { exportRequested = resolve })
    await page.route(`**/api/v1/projects/${project!.id}/export/drafts*`, async route => {
      exportRequested()
      await release
      await route.continue()
    })

    const downloadPromise = page.waitForEvent('download')
    await exportButton(page).click()
    await requested
    await expect(exportButton(page)).toHaveAttribute('aria-busy', 'true')
    await expect(exportButton(page).getByRole('status')).toBeVisible()
    releaseExport()
    await downloadPromise
    await expect(exportButton(page)).toHaveAttribute('aria-busy', 'false')
  })
})

test.describe('Export Error Handling', () => {
  test.skip(!ENABLE_EXPORT_E2E, EXPORT_OPT_IN_MESSAGE)

  let project: OwnedProject | undefined

  test.beforeEach(async ({ page }, testInfo) => {
    project = await createOwnedProject(page, testInfo, 'E2E-export-error')
    await openOwnedProject(page, project)
  })

  test.afterEach(async ({ page }) => {
    await cleanupOwnedProject(page, project)
    project = undefined
  })

  test('export shows error for unauthorized project', async ({ page }) => {
    await createDrafts(page, project!, [{ title: '第一章', content: '私有正文' }])
    const otherHeaders = await authenticatedHeaders(page, TEST_USERS.standard)
    const response = await page.request.get(
      `${config.apiBaseUrl}/api/v1/projects/${project!.id}/export/drafts`,
      { headers: otherHeaders },
    )
    expect(response.status()).toBe(403)
    expect(await response.text()).toContain('ERR_NOT_AUTHORIZED_TO_EXPORT')
  })

  test('export handles network errors gracefully', async ({ page }) => {
    await createDrafts(page, project!, [{ title: '第一章', content: '测试内容' }])
    await page.route(`**/api/v1/projects/${project!.id}/export/drafts*`, route => route.abort('failed'))

    await exportButton(page).click()
    await expect(page.getByRole('alert')).toContainText(/网络|连接|失败|network|connection|failed/i)
  })
})
