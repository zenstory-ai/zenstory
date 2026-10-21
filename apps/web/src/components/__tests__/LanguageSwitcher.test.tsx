import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LanguageSwitcher } from '../LanguageSwitcher'

const changeLanguage = vi.fn()
let currentLanguage = 'zh'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: {
      language: currentLanguage,
      changeLanguage,
    },
    t: (key: string) =>
      (
        {
          'common:language.zh': '中文',
          'common:language.en': 'English',
          'common:language.switchToEnglish': 'Switch to English',
          'common:language.switchToChinese': '切换到中文',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

describe('LanguageSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    currentLanguage = 'zh'
  })

  it('toggles language in icon mode', () => {
    render(<LanguageSwitcher />)

    const button = screen.getByRole('button', { name: /中文/i })
    expect(button).toHaveAttribute('title', 'Switch to English')
    // Someone who cannot read 中文 still hears what the button does.
    expect(button).toHaveAccessibleName('中文 · Switch to English')

    fireEvent.click(button)
    expect(changeLanguage).toHaveBeenCalledWith('en')
  })

  it('renders the full variant and toggles back to chinese', () => {
    currentLanguage = 'en'
    render(<LanguageSwitcher variant="full" />)

    const button = screen.getByRole('button', { name: /English/i })
    fireEvent.click(button)

    expect(changeLanguage).toHaveBeenCalledWith('zh')
  })
})
