/**
 * E2E Test Helper Functions
 *
 * Common utilities for E2E tests including authentication, API operations,
 * and performance measurement helpers.
 */

import fs from 'node:fs'
import { type Page, type APIRequestContext, expect } from '@playwright/test'
import { config } from '../config'

// Test credentials
const TEST_EMAIL = process.env.E2E_TEST_EMAIL || 'e2e-test@example.com'
const TEST_PASSWORD = process.env.E2E_TEST_PASSWORD || 'E2eTestPassword123!'
const AUTH_FILE = process.env.PLAYWRIGHT_AUTH_FILE || 'playwright/.auth/user.json'

type CachedAuth = {
  access_token: string
  refresh_token: string
  token_type?: string
  user?: unknown
}

const cachedAuthByEmail = new Map<string, CachedAuth>()

function readAuthFile(): CachedAuth | null {
  if (!fs.existsSync(AUTH_FILE)) return null
  try {
    const existing = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf8')) as {
      origins?: Array<{ origin?: string; localStorage?: Array<{ name?: string; value?: string }> }>
    }
    const origin = existing.origins?.find((item) => item.origin === new URL(config.baseUrl).origin)
    const localStorageItems = origin?.localStorage ?? []
    const map = Object.fromEntries(localStorageItems.map((item) => [item.name, item.value]))
    if (!map.access_token || !map.refresh_token) return null
    return {
      access_token: map.access_token,
      refresh_token: map.refresh_token,
      token_type: map.token_type,
      user: map.user ? JSON.parse(map.user) : undefined,
    }
  } catch {
    return null
  }
}

async function createFreshAuth(page: Page, email: string, password: string): Promise<CachedAuth> {
  const fileAuth = readAuthFile()
  const fileAuthEmail = (
    fileAuth?.user && typeof fileAuth.user === 'object' && fileAuth.user !== null && 'email' in fileAuth.user
      ? String((fileAuth.user as { email?: unknown }).email ?? '')
      : ''
  ).trim().toLowerCase()

  if (fileAuth?.refresh_token && fileAuthEmail === email.trim().toLowerCase()) {
    const refreshResponse = await page.request.post(`${config.apiBaseUrl}/api/auth/refresh`, {
      headers: { 'Content-Type': 'application/json' },
      data: { refresh_token: fileAuth.refresh_token },
    })
    if (refreshResponse.ok()) {
      return await refreshResponse.json() as CachedAuth
    }
  }

  const params = new URLSearchParams()
  params.append('username', email)
  params.append('password', password)
  const loginResponse = await page.request.post(`${config.apiBaseUrl}/api/auth/login`, {
    data: params.toString(),
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  })
  expect(loginResponse.ok()).toBeTruthy()
  return await loginResponse.json() as CachedAuth
}

/**
 * Login helper - navigates to login page and authenticates
 */
export async function login(
  page: Page,
  email: string = TEST_EMAIL,
  password: string = TEST_PASSWORD
): Promise<void> {
  // Email is the identity key for this E2E suite. If the suite later introduces
  // same-email/different-password scenarios, promote the cache key accordingly.
  const normalizedEmail = email.trim().toLowerCase()
  let cachedAuth = cachedAuthByEmail.get(normalizedEmail)
  if (!cachedAuth) {
    cachedAuth = await createFreshAuth(page, email, password)
    cachedAuthByEmail.set(normalizedEmail, cachedAuth)
  }

  await page.goto('/')
  await page.evaluate((tokenData) => {
    localStorage.setItem('access_token', tokenData.access_token)
    localStorage.setItem('refresh_token', tokenData.refresh_token)
    if (tokenData.token_type) {
      localStorage.setItem('token_type', tokenData.token_type)
    }
    if (tokenData.user) {
      localStorage.setItem('user', JSON.stringify(tokenData.user))
    }
  }, cachedAuth)
}

/**
 * Get auth token via API for direct API calls
 */
export async function getAuthToken(request: APIRequestContext): Promise<string> {
  const response = await request.post('/api/v1/login', {
    data: {
      email: TEST_EMAIL,
      password: TEST_PASSWORD,
    },
  })
  const data = await response.json()
  return data.access_token
}

/**
 * Create a project via API and return project ID
 */
export async function createProjectViaAPI(
  request: APIRequestContext,
  token: string,
  name: string
): Promise<string> {
  const response = await request.post('/api/v1/projects', {
    headers: { Authorization: `Bearer ${token}` },
    data: { name, description: 'E2E performance test project' },
  })
  expect(response.ok()).toBeTruthy()
  const data = await response.json()
  return data.id
}

/**
 * Create a file via API and return file ID
 */
export async function createFileViaAPI(
  request: APIRequestContext,
  token: string,
  projectId: string,
  title: string,
  fileType: string,
  content: string = ''
): Promise<string> {
  const response = await request.post('/api/v1/files', {
    headers: { Authorization: `Bearer ${token}` },
    data: { project_id: projectId, title, file_type: fileType, content },
  })
  expect(response.ok()).toBeTruthy()
  const data = await response.json()
  return data.id
}

/**
 * Update file content via API (creates a version)
 */
export async function updateFileContentViaAPI(
  request: APIRequestContext,
  token: string,
  fileId: string,
  content: string
): Promise<void> {
  const response = await request.put(`/api/v1/files/${fileId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { content },
  })
  expect(response.ok()).toBeTruthy()
}

/**
 * Batch create files via API (50 at a time)
 */
export async function batchCreateFilesViaAPI(
  request: APIRequestContext,
  token: string,
  projectId: string,
  files: Array<{ title: string; fileType: string; content?: string }>
): Promise<string[]> {
  const batchSize = 50
  const fileIds: string[] = []

  for (let i = 0; i < files.length; i += batchSize) {
    const batch = files.slice(i, i + batchSize)
    const responses = await Promise.all(
      batch.map((file) =>
        request.post('/api/v1/files', {
          headers: { Authorization: `Bearer ${token}` },
          data: {
            project_id: projectId,
            title: file.title,
            file_type: file.fileType,
            content: file.content || '',
          },
        })
      )
    )

    for (const response of responses) {
      expect(response.ok()).toBeTruthy()
      const data = await response.json()
      fileIds.push(data.id)
    }
  }

  return fileIds
}

/**
 * Login and navigate to a specific project
 */
export async function loginAndNavigateToProject(
  page: Page,
  projectId?: string
): Promise<string> {
  await login(page)

  // Navigate to dashboard if needed
  if (page.url().includes('/project/')) {
    await page.goto('/dashboard')
  }

  if (projectId) {
    // Navigate to existing project
    await page.goto(`/project/${projectId}`)
  } else {
    // Create a new test project
    const inspirationInput = page.getByTestId('dashboard-inspiration-input')
    await inspirationInput.fill(`测试项目 ${Date.now()}`)
    await page.getByTestId('create-project-button').click()
  }

  await page.waitForURL(/\/project\//, { timeout: 15000 })
  const url = page.url()
  const match = url.match(/\/project\/([^/?]+)/)
  return match ? match[1] : ''
}

/**
 * Create a test file via UI
 */
export async function createTestFile(
  page: Page,
  fileName: string,
  folderName: string = '大纲'
): Promise<void> {
  await page.waitForSelector('.overflow-auto', { timeout: 5000 })

  const folder = page.locator(`text=${folderName}`).first()
  await folder.click()
  await expect(folder).toHaveAttribute('aria-expanded', 'true', { timeout: 3000 })

  await folder.hover()
  const addButton = folder.locator('..').locator('button:has(svg.lucide-plus)').first()
  await addButton.click({ force: true })

  const fileInput = page.locator(`input[placeholder*="${folderName}"]`)
  await expect(fileInput).toBeVisible()
  await fileInput.fill(fileName)
  await fileInput.press('Enter')
  await expect(page.locator(`text=${fileName}`)).toBeVisible({ timeout: 5000 })
}

/**
 * Select a file in the tree
 */
export async function selectFile(page: Page, fileName: string): Promise<void> {
  const file = page.locator('.overflow-auto').locator(`text=${fileName}`).first()
  await file.click()
  // Wait for editor to load the file
  await expect(page.locator('textarea').first()).toBeVisible({ timeout: 5000 })
}

/**
 * Edit file content and wait for auto-save
 */
export async function editFileContent(page: Page, content: string): Promise<void> {
  const editor = page.locator('textarea').first()
  await expect(editor).toBeVisible()
  await editor.fill(content)
  // Wait for auto-save to complete by monitoring network idle or save indicator
  await page.waitForLoadState('networkidle', { timeout: 10000 })
}

/**
 * Measure execution time of an async operation
 */
export async function measureTime<T>(
  operation: () => Promise<T>
): Promise<{ result: T; durationMs: number }> {
  const startTime = Date.now()
  const result = await operation()
  const durationMs = Date.now() - startTime
  return { result, durationMs }
}
