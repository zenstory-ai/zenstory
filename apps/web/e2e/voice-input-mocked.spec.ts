import { expect, test, type Page } from '@playwright/test';
import { mockResponsiveApp } from './fixtures-responsive';

/**
 * Voice input through the real workbench chat input (MessageInput →
 * VoiceInputButton → useVoiceInput) with every API call mocked. Chromium's fake
 * capture device supplies audio; the recognize endpoint is fulfilled locally, so
 * no backend, login or speech provider is involved.
 */

const PROJECT_ID = 'responsive-project-0';
const RECOGNIZED = '语音回填文本';

type GumMode = { mode: 'allow' | 'deny'; delayMs: number };

type RecognizeCall = {
  format: string;
  sampleRate: number;
  channels: number;
  bitsPerSample: number;
  durationSec: number;
  peak: number;
};

function parseWav(base64: string): RecognizeCall & { valid: boolean } {
  const bytes = Buffer.from(base64, 'base64');
  const tag = (offset: number) => bytes.toString('ascii', offset, offset + 4);
  const valid = tag(0) === 'RIFF' && tag(8) === 'WAVE' && tag(12) === 'fmt ' && tag(36) === 'data';
  const channels = bytes.readUInt16LE(22);
  const sampleRate = bytes.readUInt32LE(24);
  const bitsPerSample = bytes.readUInt16LE(34);
  const dataLength = bytes.readUInt32LE(40);
  let peak = 0;
  for (let offset = 44; offset + 1 < 44 + dataLength && offset + 1 < bytes.length; offset += 2) {
    peak = Math.max(peak, Math.abs(bytes.readInt16LE(offset)));
  }
  return {
    valid,
    format: 'wav',
    sampleRate,
    channels,
    bitsPerSample,
    durationSec: dataLength / (sampleRate * channels * (bitsPerSample / 8)),
    peak,
  };
}

type Harness = {
  calls: RecognizeCall[];
  /** Resolve held recognize responses (only used when `holdRecognize` is set). */
  releaseRecognize: () => void;
  abortedRecognize: () => number;
};

async function prepare(page: Page, options: { gum?: GumMode; holdRecognize?: boolean } = {}): Promise<Harness> {
  await mockResponsiveApp(page, null, 'free', 'zh');
  const calls: RecognizeCall[] = [];
  const held: Array<() => void> = [];
  let aborted = 0;
  page.on('requestfailed', (request) => {
    if (request.url().includes('/api/v1/voice/recognize')) aborted += 1;
  });
  // Registered after the catch-all mock, so it takes precedence for this path.
  await page.route('**/api/v1/voice/recognize', async (route) => {
    const body = route.request().postDataJSON() as { audio_data: string; audio_format: string; sample_rate: number };
    const wav = parseWav(body.audio_data);
    expect(wav.valid).toBe(true);
    expect(body.audio_format).toBe('wav');
    calls.push(wav);
    if (options.holdRecognize) await new Promise<void>((resolve) => held.push(resolve));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ text: RECOGNIZED, success: true, duration_ms: Math.round(wav.durationSec * 1000) }),
    }).catch(() => undefined);
  });
  await page.addInitScript((gum: GumMode) => {
    const state = { ...gum, calls: 0, liveTracks: 0 };
    (window as unknown as { __voiceGum: typeof state }).__voiceGum = state;
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      state.calls += 1;
      await new Promise((resolve) => setTimeout(resolve, state.delayMs));
      if (state.mode === 'deny') throw new DOMException('Permission denied', 'NotAllowedError');
      const stream = await original(constraints);
      for (const track of stream.getTracks()) {
        state.liveTracks += 1;
        track.addEventListener('ended', () => { state.liveTracks -= 1; });
        const stop = track.stop.bind(track);
        let stopped = false;
        track.stop = () => {
          if (!stopped) { stopped = true; state.liveTracks -= 1; }
          stop();
        };
      }
      return stream;
    };
  }, options.gum ?? { mode: 'allow', delayMs: 0 });
  return {
    calls,
    releaseRecognize: () => { for (const resolve of held.splice(0)) resolve(); },
    abortedRecognize: () => aborted,
  };
}

async function setGum(page: Page, gum: Partial<GumMode>) {
  await page.evaluate((next) => Object.assign((window as unknown as { __voiceGum: object }).__voiceGum, next), gum);
}

async function gumState(page: Page) {
  return page.evaluate(() => (window as unknown as { __voiceGum: { calls: number; liveTracks: number } }).__voiceGum);
}

async function openChat(page: Page) {
  await page.goto(`/project/${PROJECT_ID}`);
  const width = page.viewportSize()?.width ?? 1440;
  if (width < 768) await page.locator('#chat-tab').click();
  await expect(page.getByTestId('chat-input')).toBeVisible();
  const button = page.getByTestId('voice-input-button');
  await expect(button).toBeVisible();
  return button;
}

const chatInput = (page: Page) => page.getByTestId('chat-input');
const recording = (page: Page) => page.getByTestId('recording-indicator');

/** Real touch sequence through CDP, so hit-testing picks the element under the finger. */
async function touch(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  return {
    down: (x: number, y: number) => cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }),
    up: () => cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }),
  };
}

async function center(page: Page, selector: string) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`no box for ${selector}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function buttonEdge(page: Page) {
  const box = await page.getByTestId('voice-input-button').boundingBox();
  if (!box) throw new Error('no voice button box');
  return { x: box.x + 3, y: box.y + box.height / 2 };
}

// Headless Chromium answers getUserMedia with NotSupportedError unless the fake
// permission UI is enabled alongside the fake capture device.
test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] } });

test.describe('voice input in the real chat input', () => {
  test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium supplies the fake capture device and CDP touch.');
  test.use({ permissions: ['microphone'] });

  test.describe('phone (touch, hold to talk)', () => {
    test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

    for (const spot of ['icon centre', 'button edge'] as const) {
      test(`hold on the ${spot}, speak, release: stops and fills the recognized text`, async ({ page }) => {
        const harness = await prepare(page);
        await openChat(page);
        const point = spot === 'icon centre'
          ? await center(page, '[data-testid="voice-input-button"] svg')
          : await buttonEdge(page);
        const finger = await touch(page);
        await finger.down(point.x, point.y);
        await expect(recording(page)).toBeAttached();
        await page.waitForTimeout(1500);
        await finger.up();
        await expect(recording(page)).not.toBeAttached({ timeout: 2000 });
        await expect(chatInput(page)).toHaveValue(RECOGNIZED);
        expect(harness.calls).toHaveLength(1);
        expect(harness.calls[0]).toMatchObject({ sampleRate: 16000, channels: 1, bitsPerSample: 16 });
        expect(harness.calls[0].durationSec).toBeGreaterThan(1);
        expect(harness.calls[0].peak).toBeGreaterThan(0);
        expect((await gumState(page)).liveTracks).toBe(0);
      });
    }

    test('a short tap explains how to talk and does not open the microphone', async ({ page }) => {
      const harness = await prepare(page);
      const button = await openChat(page);
      const point = await center(page, '[data-testid="voice-input-button"]');
      const finger = await touch(page);
      await finger.down(point.x, point.y);
      await finger.up();
      await expect(page.getByRole('status').filter({ hasText: '长按说话' })).toBeVisible();
      await page.waitForTimeout(400);
      expect((await gumState(page)).calls).toBe(0);
      await expect(button).not.toHaveAttribute('aria-label', /松开/);
      expect(harness.calls).toHaveLength(0);
    });

    test('first permission prompt: released while waiting, then hold again records', async ({ page }) => {
      const harness = await prepare(page, { gum: { mode: 'allow', delayMs: 1500 } });
      const button = await openChat(page);
      const point = await center(page, '[data-testid="voice-input-button"] svg');
      const finger = await touch(page);
      await finger.down(point.x, point.y);
      await expect(button).toHaveAttribute('aria-label', '正在请求麦克风权限…');
      // The user lifts the finger to answer the browser prompt.
      await finger.up();
      await expect(page.getByRole('status').filter({ hasText: '麦克风已就绪，请再次按住说话' })).toBeVisible({ timeout: 4000 });
      // No ghost recording after the grant arrives.
      await page.waitForTimeout(500);
      await expect(recording(page)).not.toBeAttached();
      expect((await gumState(page)).liveTracks).toBe(0);
      expect(harness.calls).toHaveLength(0);

      await setGum(page, { delayMs: 0 });
      await finger.down(point.x, point.y);
      await expect(recording(page)).toBeAttached();
      await page.waitForTimeout(1000);
      await finger.up();
      await expect(chatInput(page)).toHaveValue(RECOGNIZED);
      expect(harness.calls).toHaveLength(1);
    });

    test('a permission prompt that cancels the touch still ends in the ready hint', async ({ page }) => {
      const harness = await prepare(page, { gum: { mode: 'allow', delayMs: 1200 } });
      await openChat(page);
      const point = await buttonEdge(page);
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: point.x, y: point.y }] });
      await expect(page.getByTestId('voice-input-button')).toHaveAttribute('aria-label', '正在请求麦克风权限…');
      // Native permission UI typically takes the touch away (touchcancel, not touchend).
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] });
      await expect(page.getByRole('status').filter({ hasText: '麦克风已就绪，请再次按住说话' })).toBeVisible({ timeout: 4000 });
      await expect(page.getByRole('status').filter({ hasText: '已取消录音' })).toHaveCount(0);
      await expect(recording(page)).not.toBeAttached();
      expect((await gumState(page)).liveTracks).toBe(0);
      expect(harness.calls).toHaveLength(0);
    });

    test('a new hold while an abandoned permission request is pending still records', async ({ page }) => {
      const harness = await prepare(page, { gum: { mode: 'allow', delayMs: 1200 } });
      await openChat(page);
      const point = await buttonEdge(page);
      const finger = await touch(page);
      await finger.down(point.x, point.y);
      await page.waitForTimeout(400);
      await finger.up();
      await setGum(page, { delayMs: 0 });
      await page.waitForTimeout(100);
      await finger.down(point.x, point.y);
      await expect(recording(page)).toBeAttached();
      await page.waitForTimeout(1500); // the first, abandoned request resolves meanwhile
      await expect(recording(page)).toBeAttached();
      await finger.up();
      await expect(chatInput(page)).toHaveValue(RECOGNIZED);
      expect(harness.calls).toHaveLength(1);
      expect((await gumState(page)).liveTracks).toBe(0);
    });

    test('a system-cancelled touch cancels the recording and sends nothing', async ({ page }) => {
      const harness = await prepare(page);
      await openChat(page);
      const point = await center(page, '[data-testid="voice-input-button"] svg');
      const cdp = await page.context().newCDPSession(page);
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: point.x, y: point.y }] });
      await expect(recording(page)).toBeAttached();
      await page.waitForTimeout(600);
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] });
      await expect(page.getByRole('status').filter({ hasText: '已取消录音' })).toBeVisible();
      await expect(recording(page)).not.toBeAttached();
      await page.waitForTimeout(500);
      expect(harness.calls).toHaveLength(0);
      expect((await gumState(page)).liveTracks).toBe(0);
      await expect(chatInput(page)).toHaveValue('');
    });

    test('permission denied after release still tells the user why', async ({ page }) => {
      await prepare(page, { gum: { mode: 'deny', delayMs: 800 } });
      await openChat(page);
      const point = await buttonEdge(page);
      const finger = await touch(page);
      await finger.down(point.x, point.y);
      await page.waitForTimeout(400);
      await finger.up();
      await expect(page.getByRole('alert').filter({ hasText: '请在浏览器设置中允许使用麦克风' })).toBeVisible({ timeout: 3000 });
      await expect(recording(page)).not.toBeAttached();
    });

    test('release after the 55 s auto-stop keeps the recognition and fills the text', async ({ page }) => {
      await page.clock.install();
      const harness = await prepare(page, { holdRecognize: true });
      await openChat(page);
      const point = await buttonEdge(page);
      const finger = await touch(page);
      await finger.down(point.x, point.y);
      await page.clock.runFor(300);
      await expect(recording(page)).toBeAttached();
      await page.waitForTimeout(800);
      await page.clock.runFor(56_000);
      await expect(page.getByTestId('voice-processing-indicator')).toBeVisible();
      await expect.poll(() => harness.calls.length).toBe(1);
      await finger.up();
      await page.waitForTimeout(200);
      harness.releaseRecognize();
      await expect(chatInput(page)).toHaveValue(RECOGNIZED);
      expect(harness.abortedRecognize()).toBe(0);
    });
  });

  test.describe('narrow desktop window (mouse and keyboard)', () => {
    test.use({ viewport: { width: 600, height: 900 }, hasTouch: false, isMobile: false });

    test('mouse click starts and a second click stops', async ({ page }) => {
      const harness = await prepare(page);
      const button = await openChat(page);
      await button.click();
      await expect(recording(page)).toBeAttached();
      await page.waitForTimeout(1200);
      await button.click();
      await expect(chatInput(page)).toHaveValue(RECOGNIZED);
      expect(harness.calls).toHaveLength(1);
    });

    test('keyboard Enter starts and stops', async ({ page }) => {
      const harness = await prepare(page);
      const button = await openChat(page);
      await button.focus();
      await page.keyboard.press('Enter');
      await expect(recording(page)).toBeAttached();
      await page.waitForTimeout(1200);
      await page.keyboard.press('Enter');
      await expect(chatInput(page)).toHaveValue(RECOGNIZED);
      expect(harness.calls).toHaveLength(1);
    });
  });

  test.describe('wide desktop window', () => {
    test.use({ viewport: { width: 1440, height: 900 } });

    test('click to record, click to stop, text is appended to the draft', async ({ page }) => {
      const harness = await prepare(page);
      const button = await openChat(page);
      await chatInput(page).fill('开头');
      await button.click();
      await expect(recording(page)).toBeAttached();
      await page.waitForTimeout(1500);
      await button.click();
      await expect(chatInput(page)).toHaveValue(`开头 ${RECOGNIZED}`);
      expect(harness.calls[0]).toMatchObject({ sampleRate: 16000, channels: 1, bitsPerSample: 16 });
      expect(harness.calls[0].durationSec).toBeGreaterThan(1);
      expect((await gumState(page)).liveTracks).toBe(0);
    });

    test('waiting for permission is visible; clicking again cancels without a ghost recording', async ({ page }) => {
      const harness = await prepare(page, { gum: { mode: 'allow', delayMs: 1200 } });
      const button = await openChat(page);
      await button.click();
      await expect(button).toHaveAttribute('aria-label', '正在请求麦克风权限…');
      await button.click();
      await expect(page.getByRole('status').filter({ hasText: '已取消录音' })).toBeVisible();
      await page.waitForTimeout(1600);
      await expect(recording(page)).not.toBeAttached();
      expect((await gumState(page)).liveTracks).toBe(0);
      expect(harness.calls).toHaveLength(0);
    });

    test('permission granted after a delay starts recording normally', async ({ page }) => {
      const harness = await prepare(page, { gum: { mode: 'allow', delayMs: 800 } });
      const button = await openChat(page);
      await button.click();
      await expect(recording(page)).toBeAttached({ timeout: 3000 });
      await page.waitForTimeout(1000);
      await button.click();
      await expect(chatInput(page)).toHaveValue(RECOGNIZED);
      expect(harness.calls).toHaveLength(1);
    });

    test('permission denied shows the browser-settings hint', async ({ page }) => {
      await prepare(page, { gum: { mode: 'deny', delayMs: 300 } });
      const button = await openChat(page);
      await button.click();
      await expect(page.getByRole('alert').filter({ hasText: '请在浏览器设置中允许使用麦克风' })).toBeVisible();
      await expect(recording(page)).not.toBeAttached();
    });
  });
});
