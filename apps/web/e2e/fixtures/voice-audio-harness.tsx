import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { VoiceInputButton } from '../../src/components/VoiceInputButton'

const i18n = createInstance()
await i18n.init({ lng: 'en', resources: { en: { translation: {} } }, initAsync: false })

export function VoiceHarness() {
  const [text, setText] = useState('')
  return <><VoiceInputButton onResult={setText} /><output data-testid="voice-result">{text}</output></>
}

createRoot(document.getElementById('voice-root')!).render(
  <I18nextProvider i18n={i18n}><VoiceHarness /></I18nextProvider>,
)
