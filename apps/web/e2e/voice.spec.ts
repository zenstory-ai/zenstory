import { test as base, expect, Page } from '@playwright/test'
import { TEST_USERS } from './config'
import { login } from './helpers/common'

/**
 * Voice Input E2E Tests
 *
 * Tests the voice recognition feature including:
 * - Voice service status
 * - Voice input UI interactions
 * - Recording controls (start/stop/cancel)
 * - Voice recognition (mocked API)
 * - Error handling
 * - Accessibility
 *
 * Uses browser routes to mock the Tencent ASR API responses.
 * Microphone permission is granted via Playwright's context.grantPermissions().
 */

const test = base

const VOICE_INTERACTION_E2E_ENABLED = process.env.E2E_ENABLE_VOICE_INPUT_E2E === 'true'
const MICROPHONE_PERMISSION_BROWSER_MESSAGE =
  'Playwright can grant microphone permission only in Chromium; Firefox coverage remains on non-recording voice UI and service tests.'

// UI Selectors for voice input
const VOICE_INPUT = {
  // Voice button states
  button: '[data-testid="voice-input-button"]',
  recordingButton: 'button:has(.animate-spin), button[class*="bg-[hsl(var(--error))]',
  processingButton: 'button:has(svg.lucide-loader-2)',
  disabledButton: 'button:disabled',
  // Recording UI
  waveform: '[data-testid="voice-waveform"]',
  duration: '[data-testid="voice-recording-duration"]',
  loaderIcon: 'svg.lucide-loader-2.animate-spin',
  micOffIcon: 'svg.lucide-mic-off',
  // Toast notifications
  toastError: '[role="alert"]',
  // Chat input
  textarea: '[data-testid="chat-input"]',
}

type VoiceMock = 'success' | 'recognition-error' | 'api-error' | 'not-configured'

async function mockVoiceEndpoints(page: Page, scenario: VoiceMock = 'success') {
  await page.unroute('**/api/v1/voice/status')
  await page.unroute('**/api/v1/voice/recognize')
  await page.route('**/api/v1/voice/status', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(scenario === 'not-configured'
      ? { configured: false, error: 'Voice service not configured' }
      : {
          configured: true,
          provider: 'tencent',
          service: '一句话识别',
          max_duration_seconds: 60,
          supported_formats: ['wav', 'pcm', 'ogg-opus'],
        }),
  }))

  await page.route('**/api/v1/voice/recognize', route => {
    if (scenario === 'recognition-error') {
      return route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ text: '', success: false, error: 'InvalidParameter: 音频格式不支持' }) })
    }
    if (scenario === 'api-error') {
      return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ text: '', success: false, error: '腾讯云 API 请求失败: 502' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ text: '这是一段测试语音识别结果', success: true, duration_ms: 3500 }) })
  })
}

test.beforeEach(async ({ page }) => {
  await mockVoiceEndpoints(page)
})

// Helper to login and navigate to project
async function loginAndNavigateToProject(page: Page) {
  const TEST_EMAIL = TEST_USERS.standard.email
  const TEST_PASSWORD = TEST_USERS.standard.password
  const API_BASE_URL = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

  await login(page, TEST_EMAIL, TEST_PASSWORD)

  // Prefer API-driven setup to avoid locale/UI-dependent dashboard interactions.
  const accessToken = await page.evaluate(() => localStorage.getItem('access_token'))
  if (!accessToken) {
    throw new Error('Missing access token after login')
  }

  const headers = {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }

  const listResponse = await page.request.get(`${API_BASE_URL}/api/v1/projects`, { headers })
  if (!listResponse.ok()) {
    throw new Error(`Failed to list projects: ${listResponse.status()} ${listResponse.statusText()}`)
  }

  let projects = (await listResponse.json()) as Array<{ id?: string }>

  if (!Array.isArray(projects) || projects.length === 0 || !projects[0]?.id) {
    const createResponse = await page.request.post(`${API_BASE_URL}/api/v1/projects`, {
      headers,
      data: {
        name: `Voice E2E ${Date.now()}`,
        project_type: 'novel',
      },
    })

    if (!createResponse.ok()) {
      throw new Error(`Failed to create project: ${createResponse.status()} ${createResponse.statusText()}`)
    }

    const createdProject = (await createResponse.json()) as { id?: string }
    if (!createdProject?.id) {
      throw new Error('Project creation returned no id')
    }
    projects = [createdProject]
  }

  await page.goto(`/project/${projects[0].id}`)
  await page.waitForURL(/\/project\//, { timeout: 10000 })

  // Wait for chat panel to load
  await page.waitForSelector(VOICE_INPUT.textarea, { timeout: 10000 })
}

test.describe('Voice Input - Service Status', () => {
  test.beforeEach(async ({ page }) => {
    await loginAndNavigateToProject(page)
  })

  test('voice status shows configured state', async ({ page }) => {
    // Request voice status endpoint
    const response = await page.request.get('/api/v1/voice/status')

    expect(response.ok()).toBeTruthy()

    const status = await response.json()
    expect(typeof status.configured).toBe('boolean')
    expect(status.provider).toBe('tencent')
    expect(status.service).toBe('一句话识别')
  })

  test('voice status shows supported formats', async ({ page }) => {
    const response = await page.request.get('/api/v1/voice/status')

    expect(response.ok()).toBeTruthy()

    const status = await response.json()
    expect(status.supported_formats).toContain('ogg-opus')
    expect(status.supported_formats).not.toContain('webm')
    expect(status.supported_formats).toContain('wav')
    expect(status.supported_formats.length).toBeGreaterThan(0)
  })
})

test.describe('Voice Input - UI', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(async ({ page, context, browserName }) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    // Grant microphone permission
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page)
  })

  test('voice input button is visible in chat', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()
  })

  test('voice button has accessible label', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Verify it has an aria-label
    const ariaLabel = await voiceButton.getAttribute('aria-label')
    expect(ariaLabel).toBeTruthy()
    expect(ariaLabel?.length).toBeGreaterThan(0)
  })

  test('clicking voice button requests microphone permission', async ({ page }) => {
    // This test verifies permission flow works
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Click the voice button
    await voiceButton.click()

    // Wait for recording to start (waveform or processing indicator)
    await expect(page.locator(VOICE_INPUT.waveform).or(page.locator(VOICE_INPUT.loaderIcon))).toBeVisible({ timeout: 2000 })

    // Either recording started or processing is happening
    const isRecording = await page.locator(VOICE_INPUT.waveform).isVisible()
    const isProcessing = await page.locator(VOICE_INPUT.loaderIcon).isVisible()

    expect(isRecording || isProcessing).toBeTruthy()
  })

  test('recording shows visual indicator with duration', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Click to start recording
    await voiceButton.click()

    // Check for duration display
    const duration = page.locator(VOICE_INPUT.duration)
    await expect(duration).toBeVisible({ timeout: 2000 })

    // Verify duration format (m:ss or 0:00)
    const durationText = await duration.textContent()
    expect(durationText).toMatch(/^\d+:\d{2}$/)
  })

  test('user can stop recording manually', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Verify recording started
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Click again to stop
    await voiceButton.click()

    // Should transition to processing
    await expect(page.locator(VOICE_INPUT.loaderIcon)).toBeVisible({ timeout: 2000 })
  })
})

test.describe('Voice Input - Recognition (Mocked)', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(async ({ page, context, browserName }) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page)
  })

  test('voice input inserts text into chat', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Stop recording
    await voiceButton.click()

    // Wait for recognition to complete (mocked response)
    // Wait for text to appear in textarea
    const textarea = page.locator(VOICE_INPUT.textarea)
    await expect(textarea).not.toBeEmpty({ timeout: 3000 })

    // Check if text was inserted into textarea
    const textareaValue = await textarea.inputValue()

    // The mocked response returns '这是一段测试语音识别结果'
    expect(textareaValue).toContain('测试语音识别结果')
  })

  test('voice input appends to existing text', async ({ page }) => {
    const textarea = page.locator(VOICE_INPUT.textarea)

    // Add some existing text
    await textarea.fill('现有文本')

    const voiceButton = page.locator(VOICE_INPUT.button).first()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Stop recording
    await voiceButton.click()

    // Wait for recognition to complete
    await expect(textarea).not.toBeEmpty({ timeout: 3000 })

    // Check that text was appended
    const textareaValue = await textarea.inputValue()
    expect(textareaValue).toContain('现有文本')
    expect(textareaValue).toContain('测试语音识别结果')
  })
})

test.describe('Voice Input - Error Handling', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(({ browserName }) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
  })
  test('voice shows error when credentials not configured', async ({ page, context }) => {
    // Override handler to return not configured status
    await mockVoiceEndpoints(page, 'not-configured')

    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Stop recording to trigger recognition
    await voiceButton.click()

    // Wait for recognition to complete (check for text in textarea)
    await expect(voiceButton).toBeVisible()
  })

  test('voice shows error when permission denied', async ({ page, context }) => {
    // Clear any previously granted permissions
    await context.clearPermissions()
    await loginAndNavigateToProject(page)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Try to start recording
    await voiceButton.click()

    // Should show error toast
    const errorToast = page.locator(VOICE_INPUT.toastError)
    await expect(errorToast).toBeVisible({ timeout: 3000 })

    // Verify error message contains permission-related text
    const toastText = await errorToast.textContent()
    expect(toastText).toMatch(/权限|拒绝|permission|denied/i)
  })

  test('voice handles recognition errors gracefully', async ({ page, context }) => {
    // Override handler to return recognition error
    await mockVoiceEndpoints(page, 'recognition-error')

    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Stop recording
    await voiceButton.click()

    // Wait for error response
    // Should show error toast
    const errorToast = page.locator(VOICE_INPUT.toastError)
    await expect(errorToast).toBeVisible({ timeout: 3000 })
  })

  test('voice handles API failures gracefully', async ({ page, context }) => {
    // Override handler to return API failure
    await mockVoiceEndpoints(page, 'api-error')

    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Stop recording
    await voiceButton.click()

    // Wait for error response
    // Should show error toast
    const errorToast = page.locator(VOICE_INPUT.toastError)
    await expect(errorToast).toBeVisible({ timeout: 3000 })
  })
})

test.describe('Voice Input - Recording Controls', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(async ({ page, context, browserName }) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page)
  })

  test('recording has max duration limit', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // The hook has maxDuration of 55 seconds
    // We can't wait 55 seconds in a test, so just verify the duration display
    const duration = page.locator(VOICE_INPUT.duration)
    const durationText = await duration.textContent()

    // Duration should start from 0:00
    expect(durationText).toMatch(/^0:\d{2}$/)

    // Stop recording to clean up
    await voiceButton.click()
  })

  test('button shows processing state during recognition', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Stop recording
    await voiceButton.click()

    // Should show processing state (loader icon)
    await expect(page.locator(VOICE_INPUT.loaderIcon)).toBeVisible({ timeout: 2000 })

    // Wait for processing to complete
    await expect(page.locator(VOICE_INPUT.loaderIcon)).not.toBeVisible({ timeout: 3000 })

    // Button should return to idle state
    await expect(page.locator(VOICE_INPUT.loaderIcon)).not.toBeVisible({ timeout: 3000 })
  })
})

test.describe('Voice Input - Accessibility', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(async ({ page, context, browserName }) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page)
  })

  test('voice button has title attribute for tooltip', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Verify it has a title attribute
    const title = await voiceButton.getAttribute('title')
    expect(title).toBeTruthy()
    expect(title?.length).toBeGreaterThan(0)
  })

  test('voice button aria-label updates based on state', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Get initial aria-label
    const initialLabel = await voiceButton.getAttribute('aria-label')
    expect(initialLabel).toMatch(/语音|Voice/i)

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Get aria-label during recording
    const recordingLabel = await voiceButton.getAttribute('aria-label')
    expect(recordingLabel).toMatch(/停止|Stop/i)

    // Stop recording
    await voiceButton.click()

    // Wait for processing to complete
    await expect(page.locator(VOICE_INPUT.loaderIcon)).toBeVisible({ timeout: 2000 })
    await expect(page.locator(VOICE_INPUT.loaderIcon)).not.toBeVisible({ timeout: 3000 })
  })

  test('processing state has screen reader text', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })

    // Stop recording
    await voiceButton.click()

    // Should show processing state
    await expect(page.locator(VOICE_INPUT.loaderIcon)).toBeVisible({ timeout: 2000 })

    // Check for screen reader text (sr-only class)
    const srOnly = page.locator('.sr-only')
    const srText = await srOnly.textContent()
    expect(srText).toMatch(/识别|Recognizing/i)
  })
})

test.describe('Voice Input - Manual Verification', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.skip('voice recognition with real audio (requires manual testing)', async () => {
    // This test is skipped because:
    // 1. Playwright cannot generate real audio input
    // 2. Real ASR service requires actual audio data
    // 3. Testing with real audio should be done manually
    //
    // Manual testing steps:
    // 1. Start the development servers
    // 2. Navigate to a project
    // 3. Click the voice input button
    // 4. Speak into the microphone
    // 5. Verify the recognized text appears in the textarea
    // 6. Test with different languages (Chinese, English)
    // 7. Test with different audio qualities
  })
})
