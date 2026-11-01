import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import enChat from '../../../public/locales/en/chat.json'
import zhChat from '../../../public/locales/zh/chat.json'
import { StreamActivityLine } from '../StreamActivityLine'

async function mount(language: string, words: number) {
  const i18n = createInstance()
  await i18n.init({ lng: language, fallbackLng: false, defaultNS: 'chat', resources: { en: { chat: enChat }, zh: { chat: zhChat } } })
  render(
    <I18nextProvider i18n={i18n}>
      <StreamActivityLine startedAt={Date.now()} bodyWrite={() => ({ title: 'Chapter 1', words })} />
    </I18nextProvider>,
  )
}

describe('StreamActivityLine word count wording', () => {
  it('uses the singular for one English word', async () => {
    await mount('en', 1)
    expect(screen.getByText(/· 1 word so far/)).toBeInTheDocument()
  })

  it('uses the plural with a formatted count in English', async () => {
    await mount('en', 1234)
    expect(screen.getByText(/· 1,234 words so far/)).toBeInTheDocument()
  })

  it('keeps the single Chinese form', async () => {
    await mount('zh', 1)
    expect(screen.getByText(/已写 1 字/)).toBeInTheDocument()
  })
})
