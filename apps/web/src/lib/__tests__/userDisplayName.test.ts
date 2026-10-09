import { describe, expect, it } from 'vitest'
import { getUserDisplayName } from '../userDisplayName'

describe('getUserDisplayName', () => {
  it('shows the sign-up username, not the email prefix, when there is no nickname', () => {
    // The audit account: settings showed the email prefix while the greeting showed the username.
    expect(
      getUserDisplayName({
        nickname: null,
        username: 'audit-serial-20261009',
        email: 'audit-serial-20261009-10702@audit.zenstory.ai',
      }),
    ).toBe('audit-serial-20261009')
  })

  it('prefers a nickname and falls back to the email prefix last', () => {
    expect(getUserDisplayName({ nickname: ' 青柠 ', username: 'lime', email: 'lime@example.com' })).toBe('青柠')
    expect(getUserDisplayName({ nickname: '', username: '  ', email: 'writer@example.com' })).toBe('writer')
    expect(getUserDisplayName(null)).toBe('')
  })
})
