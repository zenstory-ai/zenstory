import { test, expect } from '@playwright/test'
import { config } from './config'
import {
  cleanupOwnedProject,
  createOwnedProject,
  type OwnedProject,
} from './fixtures/project-test-data'

const API_BASE = config.apiBaseUrl

interface UploadedDraft {
  title: string
  content: string
}

interface UploadDraftsResponse {
  files: UploadedDraft[]
  total: number
  errors: string[]
}

test.describe('Draft Upload', () => {
  let project: OwnedProject | undefined

  test.beforeEach(async ({ page }, testInfo) => {
    project = await createOwnedProject(page, testInfo, 'draft-upload')
  })

  test.afterEach(async ({ page }) => {
    await cleanupOwnedProject(page, project)
    project = undefined
  })

  test('opens a txt/markdown file chooser from the draft folder upload control', async ({ page }) => {
    if (!project) throw new Error('Owned draft-upload project fixture was not initialized')

    await page.goto(`/project/${project.id}`)
    await page.waitForURL(new RegExp(`/project/${project.id}(?:[/?#]|$)`))

    const draftFolder = page.locator(
      '[data-testid="file-tree-node"][data-file-type="folder"][data-file-title="正文"]',
    )
    await expect(draftFolder).toBeVisible()
    await draftFolder.hover()

    const uploadButton = draftFolder.getByTestId('file-tree-upload-draft')
    await expect(uploadButton).toBeVisible()
    const [fileChooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      uploadButton.click(),
    ])

    const input = fileChooser.element()
    expect(await input.getAttribute('type')).toBe('file')
    expect(await input.getAttribute('accept')).toBe('.txt,.md')
    expect(await input.getAttribute('multiple')).not.toBeNull()
  })

  test('uploads UTF-8 text and creates one draft per chapter heading', async ({ page }) => {
    if (!project) throw new Error('Owned draft-upload project fixture was not initialized')

    const content = '第一章 开始\n这是第一章的内容。\n\n第二章 发展\n这是第二章的内容。'
    const uploadRes = await page.request.post(
      `${API_BASE}/api/v1/projects/${project.id}/files/upload-drafts`,
      {
        headers: { Authorization: project.headers.Authorization },
        multipart: {
          files: {
            name: 'test_novel.txt',
            mimeType: 'text/plain',
            buffer: Buffer.from(content, 'utf-8'),
          },
        },
      },
    )

    expect(uploadRes.ok()).toBeTruthy()
    const data = await uploadRes.json() as UploadDraftsResponse
    expect(data.total).toBe(2)
    expect(data.errors).toEqual([])
    expect(data.files.map(({ title, content }) => ({ title, content }))).toEqual([
      {
        title: 'test_novel - 第一章 开始',
        content: '这是第一章的内容。',
      },
      {
        title: 'test_novel - 第二章 发展',
        content: '这是第二章的内容。',
      },
    ])
  })

  test('returns a per-file validation error for an invalid draft upload type', async ({ page }) => {
    if (!project) throw new Error('Owned draft-upload project fixture was not initialized')

    const uploadRes = await page.request.post(
      `${API_BASE}/api/v1/projects/${project.id}/files/upload-drafts`,
      {
        headers: { Authorization: project.headers.Authorization },
        multipart: {
          files: {
            name: 'document.pdf',
            mimeType: 'application/pdf',
            buffer: Buffer.from('%PDF-1.4 fake content'),
          },
        },
      },
    )

    expect(uploadRes.ok()).toBeTruthy()
    const data = await uploadRes.json() as UploadDraftsResponse
    expect(data.total).toBe(0)
    expect(data.files).toEqual([])
    expect(data.errors).toEqual(['document.pdf: ERR_FILE_TYPE_INVALID'])
  })

  test('preserves full-width paragraph indentation after stripping a chapter heading', async ({ page }) => {
    if (!project) throw new Error('Owned draft-upload project fixture was not initialized')

    const content = '第一章 缩进\n　　段落有全角空格缩进。\n　　第二段也有缩进。'
    const uploadRes = await page.request.post(
      `${API_BASE}/api/v1/projects/${project.id}/files/upload-drafts`,
      {
        headers: { Authorization: project.headers.Authorization },
        multipart: {
          files: {
            name: 'indent_test.txt',
            mimeType: 'text/plain',
            buffer: Buffer.from(content, 'utf-8'),
          },
        },
      },
    )

    expect(uploadRes.ok()).toBeTruthy()
    const data = await uploadRes.json() as UploadDraftsResponse
    expect(data.total).toBe(1)
    expect(data.errors).toEqual([])
    expect(data.files).toHaveLength(1)
    expect(data.files[0]).toEqual(expect.objectContaining({
      title: 'indent_test',
      content: '　　段落有全角空格缩进。\n　　第二段也有缩进。',
    }))
  })
})
