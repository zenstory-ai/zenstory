import { test, expect } from '@playwright/test'
import { TEST_USERS } from './config'
import { LoginPage } from './fixtures/page-objects/LoginPage'
import { DashboardPage } from './fixtures/page-objects/DashboardPage'

const TEST_EMAIL = TEST_USERS.standard.email
const TEST_PASSWORD = TEST_USERS.standard.password

test.describe('Production Bundle Smoke', () => {
  test('project page and chat panel load without runtime initialization errors', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (error) => {
      pageErrors.push(error.message)
    })

    const loginPage = new LoginPage(page)
    const dashboardPage = new DashboardPage(page)

    await loginPage.navigateToLogin()
    // Ensure this smoke suite runs against a built production bundle (vite preview / dist),
    // never against the Vite dev server.
    await expect(page.locator('script[src*="/@vite/client"]')).toHaveCount(0)
    expect(await page.locator('script[type="module"][src*="/assets/"]').count()).toBeGreaterThan(0)

    await loginPage.login(TEST_EMAIL, TEST_PASSWORD)
    await expect(page).toHaveURL(/\/(dashboard|project)/, { timeout: 10000 })

    if (!page.url().includes('/project')) {
      const projectCount = await dashboardPage.getProjectCount()
      if (projectCount === 0) {
        await dashboardPage.createProject(`Prod Smoke ${Date.now()}`, 'production bundle smoke test')
        await expect(page).toHaveURL(/\/project\//, { timeout: 15000 })
      }

      if (!page.url().includes('/project')) {
        await page.getByTestId('project-card').first().click()
        await expect(page).toHaveURL(/\/project/, { timeout: 10000 })
      }
    }

    await expect(page.getByTestId('chat-panel')).toBeVisible({ timeout: 10000 })
    await expect(page.getByTestId('chat-input')).toBeVisible({ timeout: 10000 })

    expect(pageErrors).toEqual([])
  })
})
