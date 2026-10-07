import { afterEach, describe, expect, it, vi } from 'vitest'
import { encodeMonoWav, normalizeRecording } from '../audioRecording'

afterEach(() => vi.unstubAllGlobals())

describe('PCM16 WAV encoding', () => {
  it.each([8000, 16000] as const)('writes a mono %i Hz RIFF header and clipped signed PCM', async (rate) => {
    const blob = encodeMonoWav(new Float32Array([-2, -1, -0.5, 0, 0.5, 1, 2]), rate)
    expect(blob.type).toBe('audio/wav')
    const bytes = await blob.arrayBuffer()
    const view = new DataView(bytes)
    const label = (offset: number, size: number) => new TextDecoder().decode(bytes.slice(offset, offset + size))
    expect(label(0, 4)).toBe('RIFF')
    expect(label(8, 4)).toBe('WAVE')
    expect(label(12, 4)).toBe('fmt ')
    expect(label(36, 4)).toBe('data')
    expect(view.getUint32(4, true)).toBe(50)
    expect(view.getUint32(16, true)).toBe(16)
    expect(view.getUint16(20, true)).toBe(1)
    expect(view.getUint16(22, true)).toBe(1)
    expect(view.getUint32(24, true)).toBe(rate)
    expect(view.getUint32(28, true)).toBe(rate * 2)
    expect(view.getUint16(32, true)).toBe(2)
    expect(view.getUint16(34, true)).toBe(16)
    expect(view.getUint32(40, true)).toBe(14)
    expect(Array.from({ length: 7 }, (_, i) => view.getInt16(44 + i * 2, true)))
      .toEqual([-32768, -32768, -16384, 0, 16384, 32767, 32767])
  })
})

describe('browser recording normalization', () => {
  function setup() {
    const decoded = { duration: 0.01, length: 80, numberOfChannels: 2, sampleRate: 8000 }
    const decode = vi.fn().mockResolvedValue(decoded)
    const source = { buffer: null, connect: vi.fn(), disconnect: vi.fn(), start: vi.fn() }
    const destination = {}
    const render = vi.fn().mockResolvedValue({ getChannelData: () => new Float32Array([0, 0.5]) })
    const contexts = vi.fn().mockImplementationOnce(function (_channels: number, _length: number, rate: number) {
      decoded.sampleRate = rate
      decoded.length = rate * decoded.duration
      return { decodeAudioData: decode }
    })
      .mockImplementationOnce(function () {
        return { createBufferSource: () => source, destination, startRendering: render }
      })
    vi.stubGlobal('OfflineAudioContext', contexts)
    return { decoded, decode, source, render, contexts, destination }
  }

  it('decodes the actual container and renders mono at the requested rate', async () => {
    const { decoded, source, contexts, destination } = setup()
    const normalized = await normalizeRecording(new Blob(['native-container']), 8000)
    expect(contexts).toHaveBeenNthCalledWith(1, 1, 1, 8000)
    expect(contexts).toHaveBeenNthCalledWith(2, 1, 80, 8000)
    expect(source.buffer).toBe(decoded)
    expect(source.connect).toHaveBeenCalledWith(destination)
    expect(source.start).toHaveBeenCalledWith(0)
    expect(source.disconnect).toHaveBeenCalledOnce()
    expect(normalized.type).toBe('audio/wav')
    expect(new DataView(await normalized.arrayBuffer()).getUint32(24, true)).toBe(8000)
  })

  it('does not render audio when cancellation arrives during decoding', async () => {
    const { decode, render, contexts } = setup()
    const controller = new AbortController()
    decode.mockImplementationOnce(async () => {
      controller.abort()
      return { duration: 0.01 }
    })
    await expect(normalizeRecording(new Blob(['native']), 16000, controller.signal))
      .rejects.toMatchObject({ name: 'AbortError' })
    expect(contexts).toHaveBeenCalledTimes(1)
    expect(render).not.toHaveBeenCalled()
  })

  it('supports older AbortSignals without throwIfAborted', async () => {
    setup()
    const signal = { aborted: false } as AbortSignal
    await expect(normalizeRecording(new Blob(['native']), 16000, signal))
      .resolves.toMatchObject({ type: 'audio/wav' })
  })

  it('rejects an already cancelled older AbortSignal before decoding', async () => {
    const { decode } = setup()
    const signal = { aborted: true } as AbortSignal
    await expect(normalizeRecording(new Blob(['native']), 16000, signal))
      .rejects.toMatchObject({ name: 'AbortError' })
    expect(decode).not.toHaveBeenCalled()
  })

  it.each(['decode', 'render'] as const)('propagates %s errors without retaining a connected source', async (stage) => {
    const { decode, render, source } = setup()
    const error = new Error(`${stage} failed`)
    if (stage === 'decode') decode.mockRejectedValueOnce(error)
    else render.mockRejectedValueOnce(error)
    await expect(normalizeRecording(new Blob(['native']), 16000)).rejects.toBe(error)
    if (stage === 'render') expect(source.disconnect).toHaveBeenCalledOnce()
    else expect(source.connect).not.toHaveBeenCalled()
  })
})
