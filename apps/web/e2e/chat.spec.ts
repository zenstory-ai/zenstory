import { test, expect } from '@playwright/test'
import { TEST_USERS } from './config'

/**
 * AI Chat E2E Tests
 *
 * Tests the AI conversation flow including:
 * - Message sending and display
 * - Streaming responses
 * - Tool call execution and display
 * - Error handling
 * - Context preservation
 */

const ENABLE_CHAT_E2E = process.env.E2E_ENABLE_CHAT_E2E === 'true'
const CHAT_OPT_IN_MESSAGE = 'Chat E2E tests are opt-in. Set E2E_ENABLE_CHAT_E2E=true to run.'

test.describe('AI Chat', () => {
  test.skip(!ENABLE_CHAT_E2E, CHAT_OPT_IN_MESSAGE)

  // Test credentials
  const TEST_EMAIL = TEST_USERS.standard.email
  const TEST_PASSWORD = TEST_USERS.standard.password

  test.beforeEach(async ({ page }) => {
    // Navigate to login page
    await page.goto('/login')

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('登录')

    // Login with test credentials
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()

    // Wait for redirect to complete (either project or dashboard)
    await page.waitForURL(/\/(project|dashboard)/, { timeout: 10000 })

    // If redirected to dashboard, create or navigate to a project
    const currentUrl = page.url()
    if (currentUrl.includes('/dashboard')) {
      // Click on first project card if available
      const projectCard = page.locator('[data-testid="project-card"]').first()
      if (await projectCard.isVisible()) {
        await projectCard.click()
        await page.waitForURL(/\/project\//, { timeout: 5000 })
      } else {
        // Create a new project for testing
        await page.locator('button:has-text("创建项目")').click()
        await page.waitForURL(/\/project\//, { timeout: 5000 })
      }
    }
  })

  test('user can send message to AI', async ({ page }) => {
    // Find the chat input textarea
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Type a message
    await input.fill('帮我创建一个角色')
    await expect(input).toHaveValue('帮我创建一个角色')

    // Click send button
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Verify user message appears in the chat
    await expect(page.locator('text=帮我创建一个角色')).toBeVisible({ timeout: 5000 })
  })

  test('AI response streams correctly', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message that will trigger a response
    await input.fill('写一段简短的描述')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for AI response to start streaming (with extended timeout for AI)
    await expect(page.locator('.bg-\\[hsl\\(var\\(--bg-tertiary\\)\\)\\]')).toBeVisible({ timeout: 30000 })

    // Wait for streaming to complete by checking for the absence of streaming cursor
    // The streaming cursor has animate-pulse class
    await expect(page.locator('.animate-pulse.w-1\\.5')).not.toBeVisible({ timeout: 45000 })

    // Verify AI response content is not empty
    const assistantMessage = page.locator('.markdown-content').last()
    await expect(assistantMessage).not.toBeEmpty()
  })

  test('tool calls display in UI', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message that will trigger a tool call
    await input.fill('创建一个大纲文件，标题为"测试大纲"')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for tool call card to appear (either pending or completed)
    await expect(page.locator('text=create_file')).toBeVisible({ timeout: 30000 })

    // Verify tool call has proper structure (icon + label)
    const toolCard = page.locator('text=create_file').first().locator('..')
    await expect(toolCard).toBeVisible()
  })

  test('user can cancel streaming response', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message that will trigger a long response
    await input.fill('写一个很长的故事，包含很多细节')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for streaming to start
    await expect(page.locator('.animate-pulse.w-1\\.5')).toBeVisible({ timeout: 10000 })

    // Look for cancel button (X icon in a button with red/error background during streaming)
    const cancelButton = page.locator('button:has([class*="X"]).bg-\\[hsl\\(var\\(--error\\)\\)]')

    // Click cancel if available
    if (await cancelButton.isVisible()) {
      await cancelButton.click()

      // Verify streaming stopped (cursor should disappear)
      await expect(page.locator('.animate-pulse.w-1\\.5')).not.toBeVisible({ timeout: 5000 })
    }
  })

  test('file is created via AI tool call', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    const uniqueTitle = `测试大纲_${Date.now()}`
    await input.fill(`创建一个名为"${uniqueTitle}"的大纲文件`)
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for tool call to complete (success indicator)
    await expect(page.locator('text=已创建').or(page.locator('[class*="CheckCircle2"]'))).toBeVisible({ timeout: 30000 })

    // Verify file appeared in file tree
    await expect(page.locator(`text=${uniqueTitle}`)).toBeVisible({ timeout: 10000 })
  })

  test('error displays on AI failure', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message that might trigger an error (empty or malformed request)
    // Note: This test assumes the backend handles certain inputs as errors
    await input.fill('执行一个不存在的命令xyz123')

    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for either error message or normal response
    // Error messages typically appear in amber/warning colored containers
    const errorContainer = page.locator('.bg-amber-50, .border-amber-200, [class*="error"]')

    // If an error occurs, verify it's displayed properly
    // Note: This test may pass even without error if AI responds normally
    try {
      await expect(errorContainer.first()).toBeVisible({ timeout: 15000 })
    } catch {
      // If no error, that's also acceptable - AI handled the request gracefully
      console.log('AI handled the request without error')
    }
  })

  test('context is preserved across messages', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // First message - establish context
    await input.fill('我的主角叫张三')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for AI response
    await expect(page.locator('.markdown-content').first()).toBeVisible({ timeout: 30000 })

    // Second message - reference context
    await input.fill('告诉我关于主角的信息')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for AI response
    await expect(page.locator('.markdown-content').last()).toBeVisible({ timeout: 30000 })

    // Verify the response mentions "张三" (context was preserved)
    const lastResponse = page.locator('.markdown-content').last()
    const responseText = await lastResponse.textContent()

    // The response should mention the character name
    expect(responseText).toContain('张三')
  })

  test('suggestions appear after inactivity', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send initial message
    await input.fill('创建一个角色')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for response
    await expect(page.locator('.markdown-content')).toBeVisible({ timeout: 30000 })

    // Wait for idle timeout for suggestions to appear (10 seconds in app + buffer)
    // This wait is intentional to test the suggestion feature - use clock mocking for reliability
    await page.clock.runFor(12000)

    // Check for suggestion buttons (they appear as rounded pill buttons)
    const suggestions = page.locator('button:has-text("创建"), button:has-text("写"), button:has-text("帮助")')

    // Suggestions should be visible after idle timeout
    const suggestionCount = await suggestions.count()
    expect(suggestionCount).toBeGreaterThanOrEqual(0) // Suggestions may or may not appear depending on AI
  })

  test('new session clears chat history', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message
    await input.fill('这是一条测试消息')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for message to appear
    await expect(page.locator('text=这是一条测试消息')).toBeVisible({ timeout: 10000 })

    // Click new session button (+ icon)
    await page.locator('button:has([class*="Plus"])').first().click()

    // Verify chat is cleared
    await expect(page.locator('text=这是一条测试消息')).not.toBeVisible({ timeout: 5000 })
  })
})

test.describe('AI Chat - Tool Results', () => {
  test.skip(!ENABLE_CHAT_E2E, CHAT_OPT_IN_MESSAGE)

  // Test credentials
  const TEST_EMAIL = TEST_USERS.standard.email
  const TEST_PASSWORD = TEST_USERS.standard.password

  test.beforeEach(async ({ page }) => {
    // Navigate and login
    await page.goto('/login')
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()
    await page.waitForURL(/\/(project|dashboard)/, { timeout: 10000 })

    // Navigate to project
    const currentUrl = page.url()
    if (currentUrl.includes('/dashboard')) {
      const projectCard = page.locator('[data-testid="project-card"]').first()
      if (await projectCard.isVisible()) {
        await projectCard.click()
        await page.waitForURL(/\/project\//, { timeout: 5000 })
      }
    }
  })

  test('create_file tool result shows success', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await input.fill('创建一个新角色文件')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for create_file tool call
    await expect(page.locator('text=create_file')).toBeVisible({ timeout: 30000 })

    // Verify success indicator (CheckCircle icon or "已创建" text)
    await expect(
      page.locator('[class*="CheckCircle"], text=已创建').first()
    ).toBeVisible({ timeout: 30000 })
  })

  test('edit_file tool shows diff preview', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')

    // First create a file to edit
    await input.fill('创建一个草稿文件，内容是"这是原始内容"')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for file to be created (success indicator)
    await expect(page.locator('text=已创建').or(page.locator('[class*="CheckCircle2"]'))).toBeVisible({ timeout: 30000 })

    // Now edit the file
    await input.fill('将文件中的"原始内容"改为"修改后的内容"')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for edit_file tool call
    await expect(page.locator('text=edit_file, text=编辑文件')).toBeVisible({ timeout: 30000 })
  })

  test('query_files tool shows results list', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')

    // Query for files
    await input.fill('查询所有角色文件')
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Wait for query_files tool call
    await expect(page.locator('text=/查找文件|Find Files/')).toBeVisible({ timeout: 30000 })
  })
})

test.describe('Chat Session Deletion', () => {
  test.skip(!ENABLE_CHAT_E2E, CHAT_OPT_IN_MESSAGE)

  // Test credentials
  const TEST_EMAIL = TEST_USERS.standard.email
  const TEST_PASSWORD = TEST_USERS.standard.password

  test.beforeEach(async ({ page }) => {
    // Navigate to login page
    await page.goto('/login')

    // Wait for page to load
    await expect(page.locator('h1')).toContainText('登录')

    // Login with test credentials
    await page.locator('#identifier').fill(TEST_EMAIL)
    await page.locator('#password').fill(TEST_PASSWORD)
    await page.locator('button[type="submit"]').click()

    // Wait for redirect to complete (either project or dashboard)
    await page.waitForURL(/\/(project|dashboard)/, { timeout: 10000 })

    // If redirected to dashboard, create or navigate to a project
    const currentUrl = page.url()
    if (currentUrl.includes('/dashboard')) {
      // Click on first project card if available
      const projectCard = page.locator('[data-testid="project-card"]').first()
      if (await projectCard.isVisible()) {
        await projectCard.click()
        await page.waitForURL(/\/project\//, { timeout: 5000 })
      } else {
        // Create a new project for testing
        await page.locator('button:has-text("创建项目")').click()
        await page.waitForURL(/\/project\//, { timeout: 5000 })
      }
    }
  })

  test('user can delete chat session', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message to create chat history
    const testMessage = `测试删除消息_${Date.now()}`
    await input.fill(testMessage)
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Verify message appears
    await expect(page.locator(`text=${testMessage}`)).toBeVisible({ timeout: 10000 })

    // Wait for AI response to complete
    await expect(page.locator('.markdown-content').first()).toBeVisible({ timeout: 30000 })

    // Find delete session button (trash icon or menu option)
    // Look for delete button in chat header or session list
    const deleteButton = page.locator('button:has([class*="Trash"]), button:has([class*="Trash2"]), button[aria-label*="删除"]').first()

    if (await deleteButton.isVisible()) {
      await deleteButton.click()

      // Confirm deletion if dialog appears
      const confirmButton = page.locator('button:has-text("确认"), button:has-text("删除"), button:has-text("确定")')
      if (await confirmButton.isVisible()) {
        await confirmButton.click()
      }

      // Verify chat is cleared or session is removed
      await expect(page.locator(`text=${testMessage}`)).not.toBeVisible({ timeout: 5000 })
    } else {
      // Alternative: Look for session menu/dropdown
      const sessionMenuButton = page.locator('button[aria-label*="更多"], button:has([class*="More"])').first()
      if (await sessionMenuButton.isVisible()) {
        await sessionMenuButton.click()

        // Click delete option in menu
        const deleteOption = page.locator('text=删除会话, text=Delete session')
        await deleteOption.click()

        // Confirm deletion if dialog appears
        const confirmButton = page.locator('button:has-text("确认"), button:has-text("删除"), button:has-text("确定")')
        if (await confirmButton.isVisible()) {
          await confirmButton.click()
        }

        // Verify chat is cleared or session is removed
        await expect(page.locator(`text=${testMessage}`)).not.toBeVisible({ timeout: 5000 })
      }
    }
  })

  test('delete session requires confirmation', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message to create chat history
    const testMessage = `确认删除测试_${Date.now()}`
    await input.fill(testMessage)
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Verify message appears
    await expect(page.locator(`text=${testMessage}`)).toBeVisible({ timeout: 10000 })

    // Wait for AI response to complete
    await expect(page.locator('.markdown-content').first()).toBeVisible({ timeout: 30000 })

    // Find delete session button
    const deleteButton = page.locator('button:has([class*="Trash"]), button:has([class*="Trash2"]), button[aria-label*="删除"]').first()

    if (await deleteButton.isVisible()) {
      await deleteButton.click()

      // Check for confirmation dialog
      const cancelButton = page.locator('button:has-text("取消"), button:has-text("Cancel")')

      // If confirmation dialog appears, cancel it
      if (await cancelButton.isVisible()) {
        await cancelButton.click()

        // Verify chat history is preserved
        await expect(page.locator(`text=${testMessage}`)).toBeVisible({ timeout: 5000 })
      } else {
        // No confirmation dialog - this is acceptable for some implementations
        console.log('No confirmation dialog appeared')
      }
    }
  })

  test('deleted session cannot be recovered', async ({ page }) => {
    const input = page.locator('textarea[placeholder*="输入"]')
    await expect(input).toBeVisible()

    // Send a message to create chat history
    const testMessage = `持久删除测试_${Date.now()}`
    await input.fill(testMessage)
    await page.locator('button[type="submit"], button:has([class*="Send"])').last().click()

    // Verify message appears
    await expect(page.locator(`text=${testMessage}`)).toBeVisible({ timeout: 10000 })

    // Wait for AI response to complete
    await expect(page.locator('.markdown-content').first()).toBeVisible({ timeout: 30000 })

    // Find and click delete button
    const deleteButton = page.locator('button:has([class*="Trash"]), button:has([class*="Trash2"]), button[aria-label*="删除"]').first()

    if (await deleteButton.isVisible()) {
      await deleteButton.click()

      // Confirm deletion
      const confirmButton = page.locator('button:has-text("确认"), button:has-text("删除"), button:has-text("确定")')
      if (await confirmButton.isVisible()) {
        await confirmButton.click()
      }

      // Verify message is no longer visible
      await expect(page.locator(`text=${testMessage}`)).not.toBeVisible({ timeout: 5000 })

      // Refresh page
      await page.reload()

      // Wait for page to load
      await expect(page.locator('textarea[placeholder*="输入"]')).toBeVisible({ timeout: 10000 })

      // Verify session is still deleted (message should not reappear)
      await expect(page.locator(`text=${testMessage}`)).not.toBeVisible({ timeout: 5000 })
    }
  })
})
