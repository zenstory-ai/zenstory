/**
 * Tests for error handling and translation
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock i18next
vi.mock('i18next', () => ({
  default: {
    t: vi.fn((key: string, options?: { defaultValue?: string }) => {
      // Simulate translation behavior
      const translations: Record<string, string> = {
        'errors:ERR_AUTH_INVALID_CREDENTIALS': 'Invalid username or password',
        'errors:ERR_AUTH_TOKEN_INVALID': 'Your session has expired. Please log in again.',
        'errors:ERR_PROJECT_NOT_FOUND': 'Project not found',
        'errors:ERR_FILE_TYPE_INVALID': 'Invalid file type. Only .txt files are allowed.',
        'errors:ERR_EXPORT_NO_DRAFTS': 'No drafts available to export',
        'errors:ERR_VALIDATION_ERROR': 'Validation error occurred',
        'errors:ERR_INTERNAL_SERVER_ERROR': 'An internal server error occurred',
        'errors:ERR_QUOTA_EXCEEDED': 'Daily quota exceeded',
      }
      // Return translation if found, otherwise return defaultValue or empty string
      // This matches the real i18next behavior with defaultValue: ''
      const translation = translations[key]
      if (translation) return translation
      return options?.defaultValue !== undefined ? options.defaultValue : ''
    }),
  },
}))

// Import after mocking
import {
  handleApiError,
  resolveApiErrorMessage,
  toUserErrorMessage,
  translateError,
} from '../errorHandler'

describe('errorHandler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('translateError', () => {
    it('translates known error codes', () => {
      const result = translateError('ERR_AUTH_INVALID_CREDENTIALS')
      expect(result).toBe('Invalid username or password')
    })

    it('translates ERR_AUTH_TOKEN_INVALID', () => {
      const result = translateError('ERR_AUTH_TOKEN_INVALID')
      expect(result).toBe('Your session has expired. Please log in again.')
    })

    it('translates ERR_PROJECT_NOT_FOUND', () => {
      const result = translateError('ERR_PROJECT_NOT_FOUND')
      expect(result).toBe('Project not found')
    })

    it('translates ERR_FILE_TYPE_INVALID', () => {
      const result = translateError('ERR_FILE_TYPE_INVALID')
      expect(result).toBe('Invalid file type. Only .txt files are allowed.')
    })

    it('translates ERR_EXPORT_NO_DRAFTS', () => {
      const result = translateError('ERR_EXPORT_NO_DRAFTS')
      expect(result).toBe('No drafts available to export')
    })

    it('translates ERR_VALIDATION_ERROR', () => {
      const result = translateError('ERR_VALIDATION_ERROR')
      expect(result).toBe('Validation error occurred')
    })

    it('shows the generic message instead of an unknown error code (falsy translation)', () => {
      // Untranslated ERR_ codes never reach the UI; the generic message is shown instead
      const result = translateError('ERR_UNKNOWN_ERROR')
      expect(result).toBe('An internal server error occurred')
    })

    it('falls back quota sub-codes to generic quota message', () => {
      const result = translateError('ERR_QUOTA_PROJECTS_EXCEEDED')
      expect(result).toBe('Daily quota exceeded')
    })

    it('returns error code for non-ERR_ strings (falsy translation)', () => {
      const result = translateError('Some random message')
      expect(result).toBe('Some random message')
    })

    it('handles empty string', () => {
      const result = translateError('')
      expect(result).toBe('An internal server error occurred')
    })

    it('handles unknown error codes with underscores', () => {
      const result = translateError('ERR_AUTH_REGISTRATION_FAILED')
      // Unknown codes show the generic message
      expect(result).toBe('An internal server error occurred')
    })
  })

  describe('handleApiError', () => {
    it('handles Error object with ERR_ code', () => {
      const error = new Error('ERR_PROJECT_NOT_FOUND')
      const result = handleApiError(error)
      expect(result).toBe('Project not found')
    })

    it('handles Error object with non-ERR_ message', () => {
      const error = new Error('Network error')
      const result = handleApiError(error)
      expect(result).toBe('Network error')
    })

    it('handles string error with ERR_ code', () => {
      const result = handleApiError('ERR_FILE_TYPE_INVALID')
      expect(result).toBe('Invalid file type. Only .txt files are allowed.')
    })

    it('handles string error without ERR_ prefix', () => {
      const result = handleApiError('Something went wrong')
      expect(result).toBe('Something went wrong')
    })

    it('handles null error', () => {
      const result = handleApiError(null)
      expect(result).toBe('An internal server error occurred')
    })

    it('handles undefined error', () => {
      const result = handleApiError(undefined)
      expect(result).toBe('An internal server error occurred')
    })

    it('handles number error', () => {
      const result = handleApiError(404)
      expect(result).toBe('An internal server error occurred')
    })

    it('handles object error', () => {
      const result = handleApiError({ message: 'test' })
      expect(result).toBe('test')
    })

    it('handles empty string', () => {
      const result = handleApiError('')
      expect(result).toBe('An internal server error occurred')
    })

    it('preserves whitespace in non-ERR messages', () => {
      const error = new Error('  Message with spaces  ')
      const result = handleApiError(error)
      expect(result).toBe('  Message with spaces  ')
    })

    it('handles Error with numeric message', () => {
      // @ts-expect-error - testing edge case
      const error = new Error(500)
      const result = handleApiError(error)
      expect(result).toBe('500')
    })

    it('handles Error-like object', () => {
      const errorLike = { message: 'ERR_EXPORT_NO_DRAFTS' }
      const result = handleApiError(errorLike)
      expect(result).toBe('No drafts available to export')
    })

    it('handles symbol error', () => {
      const result = handleApiError(Symbol('error'))
      expect(result).toBe('An internal server error occurred')
    })

    it('handles array error', () => {
      const result = handleApiError(['error1', 'error2'])
      expect(result).toBe('An internal server error occurred')
    })

    it('handles boolean error', () => {
      const result = handleApiError(true)
      expect(result).toBe('An internal server error occurred')
    })

    it('handles function error', () => {
      const result = handleApiError(() => 'error')
      expect(result).toBe('An internal server error occurred')
    })
  })

  describe('error handler integration', () => {
    it('translates error codes from API responses', () => {
      const apiError = new Error('ERR_AUTH_TOKEN_INVALID')
      const userMessage = handleApiError(apiError)
      expect(userMessage).toBe('Your session has expired. Please log in again.')
    })

    it('preserves non-error-code messages from API', () => {
      const apiError = new Error('Connection timeout')
      const userMessage = handleApiError(apiError)
      expect(userMessage).toBe('Connection timeout')
    })

    it('handles mixed error scenarios', () => {
      // Test multiple error types in sequence
      const errors = [
        { input: 'ERR_PROJECT_NOT_FOUND', expected: 'Project not found' },
        { input: 'Network failed', expected: 'Network failed' },
        { input: null, expected: 'An internal server error occurred' },
        { input: 'ERR_FILE_TYPE_INVALID', expected: 'Invalid file type. Only .txt files are allowed.' },
      ]

      errors.forEach(({ input, expected }) => {
        const result = handleApiError(input)
        expect(result).toBe(expected)
      })
    })
  })

  describe('edge cases', () => {
    it('handles very long error messages', () => {
      const longMessage = 'ERR_' + 'A'.repeat(1000)
      const result = translateError(longMessage)
      // Unknown error codes show the generic message
      expect(result).toBe('An internal server error occurred')
    })

    it('handles special characters in error messages', () => {
      const result = translateError('ERR_SPECIAL_CHARS_!@#$%')
      expect(result).toBe('An internal server error occurred')
    })

    it('handles unicode characters in error messages', () => {
      const result = translateError('错误信息')
      expect(result).toBe('错误信息')
    })

    it('handles error code with numbers', () => {
      const result = translateError('ERR_ERROR_123')
      expect(result).toBe('An internal server error occurred')
    })

    it('handles ERR_ prefix in middle of message', () => {
      const result = translateError('This ERR_ERROR happened')
      expect(result).toBe('This ERR_ERROR happened')
    })

    it('handles multiple ERR_ prefixes', () => {
      const result = translateError('ERR_FIRST_ERR_SECOND')
      expect(result).toBe('An internal server error occurred')
    })

    it('handles Error object with empty message', () => {
      const error = new Error('')
      const result = handleApiError(error)
      expect(result).toBe('An internal server error occurred')
    })

    it('handles whitespace-only string', () => {
      const result = handleApiError('   ')
      expect(result).toBe('An internal server error occurred')
    })
  })

  describe('resolveApiErrorMessage', () => {
    it('prefers error_code over other fields', () => {
      const result = resolveApiErrorMessage(
        {
          error_code: 'ERR_PROJECT_NOT_FOUND',
          detail: 'ignored',
          error_detail: { message: 'ignored too' },
        },
        'fallback'
      )
      expect(result).toBe('ERR_PROJECT_NOT_FOUND')
    })

    it('extracts nested message from error_detail object', () => {
      const result = resolveApiErrorMessage(
        {
          error_detail: {
            message: 'Human readable message',
          },
        },
        'fallback'
      )
      expect(result).toBe('Human readable message')
    })

    it('returns fallback when payload has no usable message', () => {
      const result = resolveApiErrorMessage({ foo: 'bar' }, 'fallback')
      expect(result).toBe('fallback')
    })
  })

  describe('toUserErrorMessage', () => {
    it('translates ERR_ codes', () => {
      expect(toUserErrorMessage('ERR_PROJECT_NOT_FOUND')).toBe('Project not found')
    })

    it('keeps plain messages untouched', () => {
      expect(toUserErrorMessage('Network error')).toBe('Network error')
    })

    it('falls back to internal error for blank message', () => {
      expect(toUserErrorMessage('   ')).toBe('An internal server error occurred')
    })
  })
})
