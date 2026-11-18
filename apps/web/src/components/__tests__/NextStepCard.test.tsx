import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NextStepCard } from '../NextStepCard'
import zhChat from '../../../public/locales/zh/chat.json'
import enChat from '../../../public/locales/en/chat.json'

async function renderCard(language: 'zh' | 'en', projectType?: string) {
  const i18n = createInstance()
  await i18n.init({
    lng: language,
    fallbackLng: language,
    interpolation: { escapeValue: false },
    resources: { zh: { chat: zhChat }, en: { chat: enChat } },
  })
  const onStart = vi.fn()
  const onDismiss = vi.fn()
  render(
    <I18nextProvider i18n={i18n}>
      <NextStepCard projectType={projectType} onStart={onStart} onDismiss={onDismiss} />
    </I18nextProvider>,
  )
  return { onStart, onDismiss }
}

afterEach(cleanup)

describe('NextStepCard', () => {
  it.each([
    ['zh', 'novel', zhChat.nextStep.novel.label, zhChat.nextStep.novel.message, zhChat.nextStep.dismiss],
    ['zh', 'short', zhChat.nextStep.short.label, zhChat.nextStep.short.message, zhChat.nextStep.dismiss],
    ['en', 'screenplay', enChat.nextStep.screenplay.label, enChat.nextStep.screenplay.message, enChat.nextStep.dismiss],
  ] as const)('pairs one primary CTA with a calm neutral dismiss option (%s %s)', async (lang, projectType, startLabel, message, dismissLabel) => {
    const { onStart, onDismiss } = await renderCard(lang, projectType)

    const card = screen.getByTestId('next-step-card')
    expect(card).toHaveClass('rounded-xl')

    const startButton = screen.getByTestId('next-step-start')
    const dismissButton = screen.getByRole('button', { name: dismissLabel })
    expect(startButton).toHaveTextContent(startLabel)
    expect(startButton.className).toContain('bg-[hsl(var(--primary))]')
    expect(dismissButton.className).not.toContain('bg-[hsl(var(--primary))]')
    expect(dismissButton.className).toContain('border-[hsl(var(--border-color))]')

    fireEvent.click(startButton)
    expect(onStart).toHaveBeenCalledWith(message)
    fireEvent.click(dismissButton)
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
