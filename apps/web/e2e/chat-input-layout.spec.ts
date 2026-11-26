import { test, expect, Page } from '@playwright/test'
import { TEST_USERS } from './config'

const TEST_EMAIL = TEST_USERS.standard.email
const TEST_PASSWORD = TEST_USERS.standard.password
const API_BASE_URL = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'
const CHAT_INPUT_PANEL_HEIGHT_KEY = 'zenstory_chat_input_panel_height_px'
const CHAT_INPUT_PANEL_MIN_HEIGHT_PX = 120

async function loginAndNavigateToProject(page: Page) {
  await page.goto('/login')
  await expect(page.locator('#identifier')).toBeVisible()
  await page.fill('#identifier', TEST_EMAIL)
  await page.fill('#password', TEST_PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForURL(/\/(dashboard|project|onboarding)/, { timeout: 15000 })

  // Bypass onboarding if redirected
  if (page.url().includes('/onboarding')) {
    const PERSONA_KEY = 'zenstory_onboarding_persona_v1'
    await page.evaluate((keyPrefix) => {
      const rawUser = localStorage.getItem('user')
      if (!rawUser) return
      const user = JSON.parse(rawUser)
      if (!user.id) return
      localStorage.setItem(
        `${keyPrefix}:${user.id}`,
        JSON.stringify({
          version: 1,
          completed_at: new Date().toISOString(),
          selected_personas: ['explorer'],
          selected_goals: ['finishBook'],
          experience_level: 'beginner',
          skipped: false,
        })
      )
    }, PERSONA_KEY)
    await page.goto('/dashboard')
    await page.waitForURL(/\/dashboard/, { timeout: 10000 })
  }

  const currentUrl = page.url()
  if (currentUrl.includes('/dashboard')) {
    const accessToken = await page.evaluate(() => localStorage.getItem('access_token'))
    if (!accessToken) {
      throw new Error('Missing access token after login')
    }

    const authHeaders = {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    }

    const listResponse = await page.request.get(`${API_BASE_URL}/api/v1/projects`, { headers: authHeaders })
    if (!listResponse.ok()) {
      throw new Error(`Failed to list projects: ${listResponse.status()} ${listResponse.statusText()}`)
    }

    let projects = (await listResponse.json()) as Array<{ id: string }>

    if (projects.length === 0) {
      const createResponse = await page.request.post(`${API_BASE_URL}/api/v1/projects`, {
        headers: authHeaders,
        data: {
          name: `E2E Chat Input Layout ${Date.now()}`,
          project_type: 'novel',
        },
      })

      if (!createResponse.ok()) {
        throw new Error(`Failed to create project: ${createResponse.status()} ${createResponse.statusText()}`)
      }

      const createdProject = (await createResponse.json()) as { id: string }
      projects = [createdProject]
    }

    await page.goto(`/project/${projects[0].id}`)
  }

  await page.waitForURL(/\/project\//, { timeout: 10000 })
}

test.describe('Chat input compact layout', () => {
  test.skip(!!process.env.CI, 'Skill creation requires real API keys — skip in CI')
  test('selected skills stay compact and do not squeeze the input area', async ({ page }) => {
    await page.addInitScript(
      ([storageKey, storageValue]) => localStorage.setItem(storageKey, storageValue),
      [CHAT_INPUT_PANEL_HEIGHT_KEY, String(CHAT_INPUT_PANEL_MIN_HEIGHT_PX)],
    )

    const skillName = `紧凑技能 ${Date.now()}`
    const trigger = `/compact-${Date.now().toString().slice(-6)}`
    const mockSkillId = `mock-skill-${Date.now()}`

    // Mock skills API to avoid 402 in CI
    await page.route('**/api/v1/skills**', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            skills: [
              {
                id: mockSkillId,
                name: skillName,
                description: '用于验证聊天输入区紧凑布局的测试技能',
                triggers: [trigger],
                instructions: '当用户调用这个技能时，保留输入框优先级并继续写作。',
                is_active: true,
                source: 'user',
              },
            ],
            total: 1,
          }),
        })
      } else if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ id: mockSkillId, name: skillName, triggers: [trigger] }),
        })
      } else {
        await route.continue()
      }
    })

    await loginAndNavigateToProject(page)
    await page.reload()

    const chatInput = page.getByTestId('chat-input')
    const sendButton = page.getByTestId('send-button')
    await expect(chatInput).toBeVisible({ timeout: 10000 })
    await expect(sendButton).toBeVisible()

    await page.getByRole('tab', { name: '技能', exact: true }).click()

    const skillRow = page
      .getByText(skillName, { exact: true })
      .locator('xpath=ancestor::div[contains(@class,"group")][1]')

    await expect(skillRow).toBeVisible({ timeout: 10000 })
    await skillRow.hover()
    await skillRow.getByTitle('使用此技能').click()

    const selectedSkillRail = page.getByTestId('chat-selected-skill-row')
    await expect(selectedSkillRail).toContainText(skillName)
    await expect(chatInput).toBeVisible()
    await expect(sendButton).toBeVisible()

    const [inputBox, sendButtonBox, skillRailBox] = await Promise.all([
      chatInput.boundingBox(),
      sendButton.boundingBox(),
      selectedSkillRail.boundingBox(),
    ])

    expect(inputBox).not.toBeNull()
    expect(sendButtonBox).not.toBeNull()
    expect(skillRailBox).not.toBeNull()

    if (!inputBox || !sendButtonBox || !skillRailBox) {
      throw new Error('Missing bounding boxes for compact layout assertions')
    }

    expect(inputBox.height).toBeGreaterThanOrEqual(36)
    expect(skillRailBox.y + skillRailBox.height).toBeLessThanOrEqual(inputBox.y + 1)
    expect(sendButtonBox.y).toBeGreaterThanOrEqual(inputBox.y - 1)

    const [railOverflowX, accessoriesOverflowY] = await Promise.all([
      selectedSkillRail.evaluate((node) => getComputedStyle(node).overflowX),
      page.getByTestId('chat-input-accessories').evaluate((node) => getComputedStyle(node).overflowY),
    ])

    expect(railOverflowX).toBe('auto')
    expect(accessoriesOverflowY).toBe('auto')

    await chatInput.fill('测试紧凑布局下的输入区仍然可用')
    await expect(chatInput).toHaveValue('测试紧凑布局下的输入区仍然可用')
  })
})
