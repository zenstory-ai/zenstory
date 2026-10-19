import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FirstWorkGuidance, FirstWorkOpenTracker } from '../FirstWorkGuidance';
import { SimpleEditor } from '../SimpleEditor';
import { MessageInput } from '../MessageInput';
import { MobileLayoutProvider, useMobileLayout } from '../../contexts/MobileLayoutContext';
import { subscriptionQueryKeys } from '../../lib/subscriptionApi';
import { firstWorkQueryKey, type FirstWorkState } from '../../lib/firstWorkApi';
import zhEditor from '../../../public/locales/zh/editor.json';
import enEditor from '../../../public/locales/en/editor.json';
import zhChat from '../../../public/locales/zh/chat.json';
import enChat from '../../../public/locales/en/chat.json';
import zhCommon from '../../../public/locales/zh/common.json';
import enCommon from '../../../public/locales/en/common.json';

const mocks = vi.hoisted(() => ({ get: vi.fn(), open: vi.fn(), send: vi.fn(), save: vi.fn() }));
vi.mock('../../lib/firstWorkApi', async original => ({ ...await original<typeof import('../../lib/firstWorkApi')>(), firstWorkApi: mocks }));
vi.mock('../../contexts/TextQuoteContext', () => ({ useTextQuote: () => ({ addQuote: vi.fn(), quotes: [], removeQuote: vi.fn() }) }));
vi.mock('../../contexts/MaterialAttachmentContext', () => ({ useMaterialAttachment: () => ({ attachedMaterials: [], removeMaterial: vi.fn() }) }));
vi.mock('../../contexts/SkillTriggerContext', () => ({ MAX_SELECTED_SKILLS: 3, useSkillTrigger: () => ({ selectedSkills: [], selectSkill: vi.fn(), removeSkill: vi.fn(), clearSkills: vi.fn() }) }));
vi.mock('../../lib/api', () => ({ skillsApi: { list: vi.fn(async () => ({ skills: [] })) } }));
vi.mock('../../lib/writingStatsApi', () => ({ writingStatsApi: { recordStats: vi.fn() } }));
vi.mock('../../hooks/useGestures', () => ({ usePinchZoom: () => ({ zoom: 1, bind: () => ({}), resetZoom: vi.fn() }) }));
vi.mock('../FileVersionHistory', () => ({ FileVersionHistory: () => null }));
vi.mock('../VoiceInputButton', () => ({ VoiceInputButton: () => null }));
vi.mock('../SelectionToolbar', () => ({ SelectionToolbar: () => null }));

const initial: FirstWorkState = { status: 'created', project_id: 'first-project', file_id: 'first-file', available: true,
  static_guidance: true, opened_at: null, first_manual_write_at: null, first_ai_requested_at: null };
let client: QueryClient;
function Workbench({ initialContent = '', fileId = 'first-file' }) {
  const [content, setContent] = useState(initialContent);
  const [draft, setDraft] = useState('Existing chat draft');
  const { activePanel, setActivePanel, chatFocusRequest } = useMobileLayout();
  return <>
    <FirstWorkOpenTracker userId="author" projectId="first-project" />
    <div data-testid="active-panel">{activePanel}</div>
    <button onClick={() => setActivePanel('editor')}>Back to manuscript</button>
    <section hidden={activePanel !== 'editor'}>
      <SimpleEditor userId="author" projectId="first-project" fileId={fileId} title="First chapter" content={content}
        onTitleChange={vi.fn()} onContentChange={setContent} onSave={mocks.save}
        emptyHint={focusEditor => <FirstWorkGuidance userId="author" projectId="first-project" fileId={fileId} focusEditor={focusEditor} />} />
    </section>
    <section hidden={activePanel !== 'chat'}><MessageInput onSend={mocks.send} externalDraft={draft} onDraftChange={setDraft}
      focusRequest={activePanel === 'chat' ? chatFocusRequest : 0} suggestionDisplayState="ready" /></section>
  </>;
}
async function mount(language = 'zh', props = {}, isMobile = true) {
  const i18n = createInstance();
  await i18n.init({ lng: language, fallbackLng: false, defaultNS: 'editor', resources: {
    zh: { editor: zhEditor, chat: zhChat, common: zhCommon }, en: { editor: enEditor, chat: enChat, common: enCommon },
  } });
  return render(<I18nextProvider i18n={i18n}><QueryClientProvider client={client}><MobileLayoutProvider isMobile={isMobile}><Workbench {...props} /></MobileLayoutProvider></QueryClientProvider></I18nextProvider>);
}
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(subscriptionQueryKeys.quota(), { ai_conversations: { used: 3, limit: 3, reset_at: null } });
  mocks.get.mockResolvedValue(initial); mocks.open.mockResolvedValue({ ...initial, opened_at: '2026-10-14T12:00:00Z' });
  mocks.save.mockResolvedValue({ outcome: 'saved', updatedAt: 'saved-version' });
});

describe('first-work static guidance', () => {
  for (const [language, paste, ai] of [['zh', '自己写', '与 AI 创作'], ['en', 'Write on my own', 'Write with AI']]) {
    it(`${language}: opens the chosen side without altering or sending drafts; manual writing works at exhausted quota`, async () => {
      await mount(language);
      fireEvent.click(await screen.findByRole('button', { name: paste }));
      const manuscript = screen.getByPlaceholderText(language === 'zh' ? zhEditor.placeholder.contentPlaceholder : enEditor.placeholder.contentPlaceholder);
      expect(manuscript).toHaveFocus(); expect(manuscript).toHaveValue('');
      fireEvent.click(screen.getByRole('button', { name: ai }));
      await waitFor(() => expect(screen.getByTestId('active-panel')).toHaveTextContent('chat'));
      const chat = screen.getByPlaceholderText(language === 'zh' ? zhChat.input.placeholder : enChat.input.placeholder);
      // Phones show the chat (and its starter cards) without raising the keyboard over them.
      expect(chat).not.toHaveFocus();
      expect(chat).toHaveValue('Existing chat draft'); expect(mocks.send).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Back to manuscript' }));
      const body = screen.getByPlaceholderText(language === 'zh' ? zhEditor.placeholder.contentPlaceholder : enEditor.placeholder.contentPlaceholder);
      fireEvent.change(body, { target: { value: 'Only the author’s paragraph.' } });
      expect(screen.queryByTestId('first-work-guidance')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: language === 'zh' ? zhEditor.save : enEditor.save }));
      await waitFor(() => expect(mocks.save).toHaveBeenCalledTimes(1));
      expect(mocks.save.mock.calls[0][0].content).toBe('Only the author’s paragraph.');
      expect(mocks.send).not.toHaveBeenCalled();
    });
  }

  it('focuses the chat input on desktop without altering or sending the draft', async () => {
    await mount('zh', {}, false);
    fireEvent.click(await screen.findByRole('button', { name: '与 AI 创作' }));
    const chat = screen.getByPlaceholderText(zhChat.input.placeholder);
    await waitFor(() => expect(chat).toHaveFocus());
    expect(chat).toHaveValue('Existing chat draft'); expect(mocks.send).not.toHaveBeenCalled();
  });

  it('records a real open even when manual content already exists, without showing an empty hint', async () => {
    await mount('en', { initialContent: 'A previous manual paragraph.' });
    await waitFor(() => expect(mocks.open).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('first-work-guidance')).not.toBeInTheDocument();
  });

  it('records the project even when another file is selected and removes guidance after accepted AI progress', async () => {
    const view = await mount('en', { fileId: 'another-file' });
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    expect(mocks.open).toHaveBeenCalledTimes(1); expect(screen.queryByTestId('first-work-guidance')).not.toBeInTheDocument();
    view.unmount();
    await mount('en');
    await screen.findByTestId('first-work-guidance');
    mocks.get.mockResolvedValue({ ...initial, static_guidance: false });
    await act(async () => client.setQueryData(firstWorkQueryKey('author'), { ...initial, static_guidance: false, first_ai_requested_at: '2026-10-14T13:00:00Z' }));
    await waitFor(() => expect(screen.queryByTestId('first-work-guidance')).not.toBeInTheDocument());
  });
});
