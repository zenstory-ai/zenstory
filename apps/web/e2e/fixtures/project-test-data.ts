import { expect, type Page, type TestInfo } from '@playwright/test'
import { config, getPoolUser, type TestUser } from '../config'
import { login } from '../helpers/common'

type AuthHeaders = Record<string, string>

export interface OwnedProject {
  id: string
  name: string
  headers: AuthHeaders
  owner: TestUser
}

export interface OwnedFile {
  id: string
  title: string
  file_type: string
  content: string
  parent_id: string | null
}

function uniqueName(prefix: string, testInfo: TestInfo): string {
  const title = testInfo.title.replace(/[^\p{L}\p{N}]+/gu, '-').slice(0, 36)
  return `${prefix}-${title}-w${testInfo.workerIndex}-${Date.now()}`
}

export async function authenticatedHeaders(page: Page, user?: TestUser): Promise<AuthHeaders> {
  await login(page, user?.email, user?.password)
  const accessToken = await page.evaluate(() => localStorage.getItem('access_token'))
  if (!accessToken) throw new Error('E2E auth bootstrap completed without an access token')

  return {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }
}

export async function gotoDashboardReady(page: Page): Promise<void> {
  await page.goto('/dashboard')
  await expect(page.getByTestId('dashboard-composer')).toBeVisible()
  await expect(page.getByTestId('dashboard-inspiration-input')).toBeVisible()
}

export async function createOwnedProject(
  page: Page,
  testInfo: TestInfo,
  prefix = 'E2E',
): Promise<OwnedProject> {
  const owner = getPoolUser(testInfo.parallelIndex)
  const headers = await authenticatedHeaders(page, owner)
  const name = uniqueName(prefix, testInfo)
  const response = await page.request.post(`${config.apiBaseUrl}/api/v1/projects`, {
    headers,
    data: { name, project_type: 'novel', description: 'Owned E2E fixture' },
  })
  if (!response.ok()) {
    throw new Error(`Failed to create owned project: ${response.status()} ${await response.text()}`)
  }
  const project = (await response.json()) as { id: string }
  return { id: project.id, name, headers, owner }
}

export async function createOwnedFile(
  page: Page,
  project: OwnedProject,
  input: {
    title: string
    fileType?: string
    content?: string
    parentFolderTitle?: string
  },
): Promise<OwnedFile> {
  const treeResponse = await page.request.get(
    `${config.apiBaseUrl}/api/v1/projects/${project.id}/file-tree`,
    { headers: project.headers },
  )
  if (!treeResponse.ok()) {
    throw new Error(`Failed to read owned project tree: ${treeResponse.status()} ${await treeResponse.text()}`)
  }
  const payload = (await treeResponse.json()) as {
    tree: Array<{ id: string; title: string; file_type: string }>
  }
  const tree = payload.tree
  const parentTitle = input.parentFolderTitle ?? '正文'
  const parent = tree.find((node) => node.file_type === 'folder' && node.title === parentTitle)
  if (!parent) throw new Error(`Owned project is missing the ${parentTitle} folder`)

  const response = await page.request.post(
    `${config.apiBaseUrl}/api/v1/projects/${project.id}/files`,
    {
      headers: project.headers,
      data: {
        title: input.title,
        file_type: input.fileType ?? 'draft',
        content: input.content ?? '',
        parent_id: parent.id,
      },
    },
  )
  if (!response.ok()) {
    throw new Error(`Failed to create owned file: ${response.status()} ${await response.text()}`)
  }
  return (await response.json()) as OwnedFile
}

export async function cleanupOwnedProject(page: Page, project?: OwnedProject): Promise<void> {
  if (!project) return
  const response = await page.request.delete(`${config.apiBaseUrl}/api/v1/projects/${project.id}`, {
    headers: project.headers,
  })
  if (!response.ok() && response.status() !== 404) {
    throw new Error(`Failed to clean up owned project ${project.id}: ${response.status()} ${await response.text()}`)
  }
}
