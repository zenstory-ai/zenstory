import { test as base, expect, Page, type TestInfo } from '@playwright/test'
import {
  cleanupOwnedProject,
  createOwnedProject,
  type OwnedProject,
} from './fixtures/project-test-data'

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
  processingIndicator: '[data-testid="voice-processing-indicator"]',
  micOffIcon: 'svg.lucide-mic-off',
  // Toast notifications
  toastError: '[role="alert"]',
  // Chat input
  textarea: '[data-testid="chat-input"]',
}

type VoiceMock = 'success' | 'recognition-error' | 'api-error' | 'not-configured'

type VoiceMockState = { recognizeRequests: number }

async function mockVoiceEndpoints(page: Page, scenario: VoiceMock = 'success'): Promise<VoiceMockState> {
  const state = { recognizeRequests: 0 }
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

  await page.route('**/api/v1/voice/recognize', async route => {
    state.recognizeRequests += 1
    await new Promise(resolve => setTimeout(resolve, 200))
    if (scenario === 'not-configured') {
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error_code: 'ERR_VOICE_CREDENTIALS_NOT_CONFIGURED', error_message: '腾讯云 API 凭证未配置' }) })
    }
    if (scenario === 'recognition-error') {
      return route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'ERR_VOICE_AUDIO_DECODE_FAILED',
          error_code: 'ERR_VOICE_AUDIO_DECODE_FAILED',
          error_detail: 'Audio data must be strict Base64',
        }),
      })
    }
    if (scenario === 'api-error') {
      return route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: 'ERR_VOICE_API_REQUEST_FAILED',
          error_code: 'ERR_VOICE_API_REQUEST_FAILED',
          error_detail: 'Voice recognition provider unavailable',
        }),
      })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ text: '这是一段测试语音识别结果', success: true, duration_ms: 3500 }) })
  })
  return state
}

async function mockMicrophone(page: Page, mode: 'available' | 'denied' = 'available') {
  await page.addInitScript((microphoneMode) => {
    const state = { calls: 0, constraints: null as MediaStreamConstraints | null }
    Object.defineProperty(window, '__voiceE2E', { configurable: true, value: state })
    const mediaDevices = navigator.mediaDevices ?? {}
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: mediaDevices })
    Object.defineProperty(mediaDevices, 'getUserMedia', {
      configurable: true,
      value: async (constraints: MediaStreamConstraints) => {
        state.calls += 1
        state.constraints = constraints
        if (microphoneMode === 'denied') {
          throw new DOMException('Permission denied by E2E fixture', 'NotAllowedError')
        }
        const context = new AudioContext()
        const oscillator = context.createOscillator()
        const destination = context.createMediaStreamDestination()
        oscillator.connect(destination)
        oscillator.start()
        const track = destination.stream.getAudioTracks()[0]
        track.addEventListener('ended', () => {
          oscillator.stop()
          void context.close()
        }, { once: true })
        return destination.stream
      },
    })
  }, mode)
}

async function waitForRecordedAudio(page: Page) {
  await expect(page.locator(VOICE_INPUT.duration)).toHaveText('0:01')
}

let voiceProject: OwnedProject | undefined

test.beforeEach(async ({ page }) => {
  await mockVoiceEndpoints(page)
})

test.afterEach(async ({ page }) => {
  await cleanupOwnedProject(page, voiceProject)
  voiceProject = undefined
})

// Helper to login and navigate to project
async function loginAndNavigateToProject(page: Page, testInfo: TestInfo) {
  voiceProject = await createOwnedProject(page, testInfo, 'E2E-voice')
  await page.goto(`/project/${voiceProject.id}`)
  await page.waitForURL(/\/project\//, { timeout: 10000 })

  // Wait for chat panel to load
  await page.waitForSelector(VOICE_INPUT.textarea, { timeout: 10000 })
}

test.describe('Voice Input - Service Status', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    await loginAndNavigateToProject(page, testInfo)
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
  test.beforeEach(async ({ page, context, browserName }, testInfo) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    await mockMicrophone(page)
    // Grant microphone permission
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page, testInfo)
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

  test('clicking voice button calls getUserMedia with audio constraints', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Click the voice button
    await voiceButton.click()

    // Wait for recording to start (waveform or processing indicator)
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })
    const microphoneRequest = await page.evaluate(() => {
      const state = (window as Window & {
        __voiceE2E?: { calls: number; constraints: MediaStreamConstraints | null }
      }).__voiceE2E
      if (!state) throw new Error('Microphone fixture was not installed')
      return state
    })
    expect(microphoneRequest.calls).toBe(1)
    expect(microphoneRequest.constraints).toEqual({
      audio: {
        sampleRate: 16000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    })
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
    await waitForRecordedAudio(page)

    // Click again to stop
    await voiceButton.click()

    // Should transition to processing
    await expect(page.locator(VOICE_INPUT.processingIndicator)).toBeVisible({ timeout: 2000 })
  })
})

test.describe('Voice Input - Recognition (Mocked)', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(async ({ page, context, browserName }, testInfo) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    await mockMicrophone(page)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page, testInfo)
  })

  test('voice input inserts text into chat', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })
    await waitForRecordedAudio(page)

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
    await waitForRecordedAudio(page)

    // Stop recording
    await voiceButton.click()

    // Wait for recognition to complete
    await expect(textarea).toHaveValue(/现有文本.*测试语音识别结果/, { timeout: 3000 })

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
  test('voice shows error when credentials not configured', async ({ page, context }, testInfo) => {
    // Override handler to return not configured status
    const voiceMock = await mockVoiceEndpoints(page, 'not-configured')

    await mockMicrophone(page)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page, testInfo)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    const textarea = page.locator(VOICE_INPUT.textarea)
    await voiceButton.click()
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible()
    await waitForRecordedAudio(page)
    await voiceButton.click()
    await expect(page.locator(VOICE_INPUT.toastError)).toHaveText('语音输入暂不可用，请改用键盘输入')
    await expect(page.locator(VOICE_INPUT.waveform)).toHaveCount(0)
    await expect(textarea).toBeEmpty()
    expect(voiceMock.recognizeRequests).toBe(1)
  })

  test('voice shows error when permission denied', async ({ page, context }, testInfo) => {
    // Clear any previously granted permissions
    await context.clearPermissions()
    await mockMicrophone(page, 'denied')
    await loginAndNavigateToProject(page, testInfo)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Try to start recording
    await voiceButton.click()

    // Should show error toast
    const errorToast = page.locator(VOICE_INPUT.toastError)
    await expect(errorToast).toBeVisible({ timeout: 3000 })

    // Verify error message contains permission-related text
    const toastText = await errorToast.textContent()
    expect(toastText).toBe('请在浏览器设置中允许使用麦克风')
  })

  test('voice handles recognition errors gracefully', async ({ page, context }, testInfo) => {
    // Override handler to return recognition error
    const voiceMock = await mockVoiceEndpoints(page, 'recognition-error')

    await mockMicrophone(page)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page, testInfo)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })
    await waitForRecordedAudio(page)

    // Stop recording
    await voiceButton.click()

    await expect(page.locator(VOICE_INPUT.toastError)).toHaveText('没能识别这段录音，请重新录制')
    expect(voiceMock.recognizeRequests).toBe(1)
    await expect(page.locator(VOICE_INPUT.textarea)).toBeEmpty()
    await expect(page.locator(VOICE_INPUT.waveform)).toHaveCount(0)
    await expect(page.locator(VOICE_INPUT.processingIndicator)).toHaveCount(0)
  })

  test('voice handles API failures gracefully', async ({ page, context }, testInfo) => {
    // Override handler to return API failure
    const voiceMock = await mockVoiceEndpoints(page, 'api-error')

    await mockMicrophone(page)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page, testInfo)

    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })
    await waitForRecordedAudio(page)

    // Stop recording
    await voiceButton.click()

    await expect(page.locator(VOICE_INPUT.toastError)).toHaveText('语音识别失败，请稍后重试或改用键盘输入')
    expect(voiceMock.recognizeRequests).toBe(1)
    await expect(page.locator(VOICE_INPUT.textarea)).toBeEmpty()
    await expect(page.locator(VOICE_INPUT.waveform)).toHaveCount(0)
    await expect(page.locator(VOICE_INPUT.processingIndicator)).toHaveCount(0)
  })
})

test.describe('Voice Input - Recording Controls', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(async ({ page, context, browserName }, testInfo) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    await mockMicrophone(page)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page, testInfo)
  })

  test('recording has max duration limit', async ({ page }) => {
    const voiceMock = await mockVoiceEndpoints(page)
    await page.clock.install()
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })
    await waitForRecordedAudio(page)

    await page.clock.fastForward(53_000)
    await expect(page.locator(VOICE_INPUT.duration)).toHaveText('0:54')
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible()

    await page.clock.fastForward(1_000)
    await expect(page.locator(VOICE_INPUT.textarea)).toHaveValue(/测试语音识别结果/)
    await expect(page.locator(VOICE_INPUT.waveform)).toHaveCount(0)
    await expect(page.locator(VOICE_INPUT.processingIndicator)).toHaveCount(0)
    expect(voiceMock.recognizeRequests).toBe(1)
  })

  test('button shows processing state during recognition', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })
    await waitForRecordedAudio(page)

    // Stop recording
    await voiceButton.click()

    // Should show processing state (loader icon)
    await expect(page.locator(VOICE_INPUT.processingIndicator)).toBeVisible({ timeout: 2000 })

    // Wait for processing to complete
    await expect(page.locator(VOICE_INPUT.processingIndicator)).not.toBeVisible({ timeout: 3000 })

    // Button should return to idle state
    await expect(page.locator(VOICE_INPUT.processingIndicator)).not.toBeVisible({ timeout: 3000 })
  })
})

test.describe('Voice Input - Accessibility', () => {
  test.skip(
    !VOICE_INTERACTION_E2E_ENABLED,
    'Voice interaction E2E depends on real browser recording flow. Set E2E_ENABLE_VOICE_INPUT_E2E=true to run.'
  )
  test.beforeEach(async ({ page, context, browserName }, testInfo) => {
    test.skip(browserName !== 'chromium', MICROPHONE_PERMISSION_BROWSER_MESSAGE)
    await mockMicrophone(page)
    await context.grantPermissions(['microphone'])
    await loginAndNavigateToProject(page, testInfo)
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
    await waitForRecordedAudio(page)

    // Get aria-label during recording
    const recordingLabel = await voiceButton.getAttribute('aria-label')
    expect(recordingLabel).toMatch(/停止|Stop/i)

    // Stop recording
    await voiceButton.click()

    // Wait for processing to complete
    await expect(page.locator(VOICE_INPUT.processingIndicator)).toBeVisible({ timeout: 2000 })
    await expect(page.locator(VOICE_INPUT.processingIndicator)).not.toBeVisible({ timeout: 3000 })
  })

  test('processing state has screen reader text', async ({ page }) => {
    const voiceButton = page.locator(VOICE_INPUT.button).first()
    await expect(voiceButton).toBeVisible()

    // Start recording
    await voiceButton.click()

    // Wait for recording to start
    await expect(page.locator(VOICE_INPUT.waveform)).toBeVisible({ timeout: 2000 })
    await waitForRecordedAudio(page)

    // Stop recording
    await voiceButton.click()

    // Should show processing state
    const processingIndicator = page.locator(VOICE_INPUT.processingIndicator)
    await expect(processingIndicator).toBeVisible({ timeout: 2000 })

    // Check for screen reader text (sr-only class)
    const srOnly = processingIndicator.locator('.sr-only')
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
