import { readFileSync } from 'node:fs'
import { test, expect } from '@playwright/test'

function wavHeader(base64: string) {
  const bytes = Buffer.from(base64, 'base64')
  let peak = 0
  for (let offset = 44; offset + 1 < bytes.length; offset += 2) peak = Math.max(peak, Math.abs(bytes.readInt16LE(offset)))
  return {
    riff: bytes.toString('ascii', 0, 4),
    wave: bytes.toString('ascii', 8, 12),
    pcm: bytes.readUInt16LE(20),
    channels: bytes.readUInt16LE(22),
    rate: bytes.readUInt32LE(24),
    bits: bytes.readUInt16LE(34),
    dataBytes: bytes.readUInt32LE(40),
    peak,
  }
}

for (const [file, mime] of [['tone.webm', 'audio/webm;codecs=opus'], ['tone.m4a', 'audio/mp4']] as const) {
  for (const sampleRate of [8000, 16000] as const) {
    test(`${file} becomes real mono ${sampleRate} Hz PCM16 WAV`, async ({ page, browserName }) => {
      test.skip(browserName === 'webkit' && file === 'tone.webm', 'WebKit fixture scope is native MP4/AAC, not Chrome capture.')
      await page.goto('/e2e/fixtures/voice-audio.html')
      const base64 = readFileSync(new URL(`./fixtures/audio/${file}`, import.meta.url)).toString('base64')
      const result = await page.evaluate(async ({ base64, mime, sampleRate }) => {
        // @ts-expect-error This absolute browser import is served and transformed by Vite.
        const { normalizeRecording } = await import('/src/lib/audioRecording.ts')
        const native = new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))], { type: mime })
        const wav = await normalizeRecording(native, sampleRate)
        const bytes = new Uint8Array(await wav.arrayBuffer())
        return btoa(Array.from(bytes, (b) => String.fromCharCode(b)).join(''))
      }, { base64, mime, sampleRate })
      expect(wavHeader(result)).toMatchObject({ riff: 'RIFF', wave: 'WAVE', pcm: 1, channels: 1, rate: sampleRate, bits: 16 })
      const header = wavHeader(result)
      // Known 250 ms tone: reject one-silent-sample/header-only false positives.
      expect(header.dataBytes).toBeGreaterThanOrEqual(sampleRate * 0.2 * 2)
      expect(header.dataBytes).toBeLessThan(sampleRate * 0.4 * 2)
      expect(header.peak).toBeGreaterThan(100)
    })
  }
}

test('production button captures native microphone audio and uploads actual WAV', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'Fake microphone permission flow is available only in Chromium.')
  let payload: { audio_data: string; audio_format: string; sample_rate: number } | undefined
  await page.route('**/api/v1/voice/recognize', async (route) => {
    payload = route.request().postDataJSON()
    await route.fulfill({ json: { success: true, text: 'local transcription', error: null, duration_ms: 800 } })
  })
  await page.goto('/e2e/fixtures/voice-audio.html')
  const button = page.getByTestId('voice-input-button')
  await button.click()
  await expect(page.getByTestId('recording-indicator')).toBeAttached()
  // Allow native MediaRecorder to produce multiple chunks, including stop's final chunk.
  await page.waitForTimeout(800)
  await button.click()
  await expect(page.getByTestId('voice-result')).toHaveText('local transcription')
  expect(payload?.audio_format).toBe('wav')
  expect(payload?.sample_rate).toBe(16000)
  expect(wavHeader(payload!.audio_data)).toMatchObject({ riff: 'RIFF', wave: 'WAVE', pcm: 1, channels: 1, rate: 16000, bits: 16 })
  expect(payload!.audio_data.length).toBeLessThanOrEqual(3_000_000)
})
