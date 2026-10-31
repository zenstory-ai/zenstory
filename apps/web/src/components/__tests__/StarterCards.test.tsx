import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StarterCards } from '../StarterCards'
import zhChat from '../../../public/locales/zh/chat.json'
import enChat from '../../../public/locales/en/chat.json'

async function show(language: 'zh' | 'en', props: Partial<React.ComponentProps<typeof StarterCards>> = {}) {
  const i18n = createInstance()
  await i18n.init({ lng: language, fallbackLng: language, interpolation: { escapeValue: false }, resources: {
    zh: { chat: zhChat }, en: { chat: enChat },
  } })
  const handlers = { onSend: vi.fn(), onEdit: vi.fn(), onSendManuscript: vi.fn() }
  render(
    <I18nextProvider i18n={i18n}>
      <StarterCards projectType="novel" remainingToday={2} {...handlers} {...props} />
    </I18nextProvider>,
  )
  return handlers
}

afterEach(cleanup)

describe('StarterCards for a work that already has the author’s prose', () => {
  it.each(['zh', 'en'] as const)('offers the manuscript requests instead of the example premise (%s)', async (language) => {
    const handlers = await show(language, { hasManuscript: true })

    expect(screen.getByTestId('starter-cards')).toHaveAttribute('data-variant', 'manuscript')
    expect(screen.queryByTestId('starter-example')).not.toBeInTheDocument()
    expect(screen.queryByTestId('starter-send')).not.toBeInTheDocument()
    expect(screen.getByTestId('starter-quota')).toBeInTheDocument()

    const review = screen.getByTestId('starter-manuscript-review')
    const shown = review.textContent ?? ''
    // The read-through request says in plain words that the text stays as it is.
    expect(shown).toMatch(language === 'zh' ? /不改原文/ : /Don't change the text/)
    fireEvent.click(review)
    expect(handlers.onSendManuscript).toHaveBeenCalledWith(shown, 'review')

    fireEvent.click(screen.getByTestId('starter-manuscript-characters'))
    fireEvent.click(screen.getByTestId('starter-manuscript-continue'))
    expect(handlers.onSendManuscript.mock.calls.map(([, kind]) => kind)).toEqual(['review', 'characters', 'continue'])
    expect(handlers.onSend).not.toHaveBeenCalled()
  })

  it('words the continuation by the kind of work', async () => {
    await show('zh', { hasManuscript: true, projectType: 'screenplay' })
    expect(screen.getByTestId('starter-manuscript-continue').textContent).toContain('下一集')
    cleanup()
    await show('zh', { hasManuscript: true, projectType: 'novel' })
    expect(screen.getByTestId('starter-manuscript-continue').textContent).toContain('下一章')
  })

  it('keeps the example premise when the work has no prose yet', async () => {
    const handlers = await show('zh', { hasManuscript: false })
    expect(screen.getByTestId('starter-cards')).toHaveAttribute('data-variant', 'example')
    expect(screen.queryByTestId('starter-manuscript')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('starter-send'))
    expect(handlers.onSend).toHaveBeenCalledWith(zhChat.starter.example.novel)
  })
})
