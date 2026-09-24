import '@testing-library/jest-dom'
import { cleanup } from '@testing-library/react'
import { afterEach, vi, beforeEach } from 'vitest'

// Polyfill TextDecoder for vmThreads environment
if (typeof TextDecoder === 'undefined') {
  global.TextDecoder = class TextDecoder {
    readonly encoding: string = 'utf-8'
    readonly fatal: boolean = false
    readonly ignoreBOM: boolean = false

    decode(input?: BufferSource | null, _options?: TextDecodeOptions): string {
      if (input instanceof Uint8Array) {
        return Array.from(input)
          .map(b => String.fromCharCode(b))
          .join('')
      }
      return ''
    }
  } as typeof globalThis.TextDecoder
}

// Polyfill TextEncoder for vmThreads environment
if (typeof TextEncoder === 'undefined') {
  global.TextEncoder = class TextEncoder {
    readonly encoding: string = 'utf-8'

    encode(input?: string): Uint8Array {
      const str = input ?? ''
      return new Uint8Array(Array.from(str).map(c => c.charCodeAt(0)))
    }

    encodeInto(src: string, dest: Uint8Array): TextEncoderEncodeIntoResult {
      const encoded = this.encode(src)
      const len = Math.min(encoded.length, dest.length)
      for (let i = 0; i < len; i++) {
        dest[i] = encoded[i] as number
      }
      return { read: src.length, written: len }
    }
  } as typeof globalThis.TextEncoder
}

afterEach(() => cleanup())

// Mock IntersectionObserver
global.IntersectionObserver = vi.fn().mockImplementation(function () {
  return {
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  }
})

// Create a proper localStorage mock that stores values
const createLocalStorage = () => {
  let store: Record<string, string> = {}
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key]
    }),
    clear: vi.fn(() => {
      store = {}
    }),
    get length() {
      return Object.keys(store).length
    },
    key: vi.fn((index: number) => Object.keys(store)[index] ?? null),
  }
}

const localStorageMock = createLocalStorage()
Object.defineProperty(global, 'localStorage', {
  value: localStorageMock,
  writable: true,
})

// Reset localStorage between tests
beforeEach(() => {
  const newStorage = createLocalStorage()
  Object.defineProperty(global, 'localStorage', {
    value: newStorage,
    writable: true,
  })
})

// Mock matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})
