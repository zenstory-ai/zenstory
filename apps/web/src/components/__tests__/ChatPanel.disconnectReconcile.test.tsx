import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRef, useSyncExternalStore } from 'react';
import { ChatPanelHeader } from '../chat/ChatPanelHeader';
import { isUnexpectedStreamDisconnect, SSE_DISCONNECT_RECONCILE_DELAY_MS } from '../../lib/agentApi';

// ChatPanel is wrapped in React.memo with no props; mirror ProjectContext updates
// via a subscribable store so project switches always trigger a re-render.
const projectStore = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  const store = {
    currentProjectId: 'proj-1',
    setProjectId(id: string) {
      store.currentProjectId = id;
      listeners.forEach((listener) => listener());
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
  return store;
});

const mocks = vi.hoisted(() => ({
  getRecentMessages: vi.fn(),
  createNewSession: vi.fn(),
  startStream: vi.fn(),
  stop: vi.fn(),
  reset: vi.fn(),
  clearError: vi.fn(),
  triggerFileTreeRefresh: vi.fn(),
  streamOptions: null as Record<string, (...args: unknown[]) => unknown> | null,
  streamState: {
    isStreaming: false,
    isThinking: false,
    error: null as string | null,
    errorCode: null as string | null,
    retryable: false,
  },
  language: 'en',
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => {} },
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'chat:panel.title': mocks.language.startsWith('zh') ? 'AI 创作助手' : 'AI Assistant',
        'chat:defaultTitle': mocks.language.startsWith('zh') ? '新对话' : 'New Chat',
        'chat:panel.aiMemory': 'AI Memory',
        'chat:panel.newSession': 'New chat',
        'chat:panel.alreadyNewSession': 'Already in a new chat',
        'chat:panel.streamErrorTitle': 'Generation was interrupted',
        'chat:stream.connectionInterrupted': 'Connection lost. Please try again.',
        'common:retry': 'Retry',
      };
      return map[key] ?? key;
    },
    i18n: {
      get language() {
        return mocks.language;
      },
    },
  }),
}));

vi.mock('../subscription/QuotaBadge', () => ({
  QuotaBadge: () => <div data-testid="quota-badge" />,
}));

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => {
    const currentProjectId = useSyncExternalStore(
      (listener) => projectStore.subscribe(listener),
      () => projectStore.currentProjectId,
    );
    return {
      currentProjectId,
      currentProject: { id: currentProjectId, project_type: 'novel' },
      selectedItem: null,
      fileTreeVersion: 1,
      triggerFileTreeRefresh: mocks.triggerFileTreeRefresh,
      triggerEditorRefresh: vi.fn(),
      setAiEditingFileId: vi.fn(),
      setIsChatStreaming: vi.fn(),
      pendingAgentUiAction: null,
      consumeAgentUiAction: vi.fn(),
      setSelectedItem: vi.fn(),
      appendFileContent: vi.fn(),
      finishFileStreaming: vi.fn(),
      startFileStreaming: vi.fn(),
      streamingFileId: null,
      refreshProject: vi.fn(),
    };
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1' } }),
}));

vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({
    isMobile: false,
    activePanel: 'chat',
    chatFocusRequest: 0,
    switchToEditor: vi.fn(),
    focusChatInput: vi.fn(),
  }),
}));

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: () => ({
    attachedFileIds: [],
    attachedLibraryMaterials: [],
    clearMaterials: vi.fn(),
  }),
}));

vi.mock('../../contexts/TextQuoteContext', () => ({
  useTextQuote: () => ({ quotes: [], clearQuotes: vi.fn() }),
}));

vi.mock('../../hooks/useFirstWork', () => ({
  useFirstWork: () => ({ data: null, isSuccess: true }),
  needsStaticFirstWorkGuidance: () => false,
}));

vi.mock('../../hooks/useLeaveWhileGenerating', () => ({
  useLeaveWhileGenerating: () => ({ leavePending: false, roundEnded: false, confirmLeave: vi.fn(), cancelLeave: vi.fn() }),
}));

vi.mock('../../hooks/useQuotaRefreshAfterLeave', () => ({
  useQuotaRefreshAfterLeave: vi.fn(),
}));

vi.mock('../../hooks/useDraftPersistence', () => ({
  appendDraftForProject: vi.fn(() => true),
  useDraftPersistence: () => ({ draft: '', saveDraft: vi.fn(), clearDraft: vi.fn() }),
}));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    projectApi: {
      ...actual.projectApi,
      getProgress: vi.fn().mockResolvedValue([]),
    },
  };
});

vi.mock('../../lib/chatApi', () => ({
  getRecentMessages: (...args: unknown[]) => mocks.getRecentMessages(...args),
  createNewSession: (...args: unknown[]) => mocks.createNewSession(...args),
  submitMessageFeedback: vi.fn(),
}));

vi.mock('../../lib/agentApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/agentApi')>('../../lib/agentApi');
  return {
    ...actual,
    fetchSuggestions: vi.fn().mockResolvedValue([]),
  };
});

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getQuota: vi.fn().mockResolvedValue({
      ai_conversations: { used: 1, limit: 3, reset_at: '2026-11-28T16:00:00Z' },
    }),
  },
  subscriptionQueryKeys: {
    quota: () => ['quota'],
    status: () => ['status', 'user-1'],
  },
}));

vi.mock('../../hooks/useAgentStream', () => ({
  useAgentStream: (_projectId: string, options: Record<string, (...args: unknown[]) => unknown>) => {
    mocks.streamOptions = options;
    return {
      state: { ...mocks.streamState, content: '' },
      startStream: mocks.startStream,
      stop: mocks.stop,
      isStopping: false,
      reset: mocks.reset,
      whenStreamClosed: vi.fn().mockResolvedValue(undefined),
      isStreaming: mocks.streamState.isStreaming,
      isThinking: mocks.streamState.isThinking,
      error: mocks.streamState.error,
      errorCode: mocks.streamState.errorCode,
      clearError: mocks.clearError,
      retryable: mocks.streamState.retryable,
      sessionId: 'sess-1',
      sendSteeringMessage: vi.fn(),
    };
  },
}));

vi.mock('../MessageList', () => ({
  MessageList: ({ messages }: { messages: Array<{ id: string; role: string; content: string }> }) => (
    <div data-testid="mock-message-list">
      {messages.map((m) => (
        <div key={m.id} data-role={m.role}>
          {m.content}
        </div>
      ))}
    </div>
  ),
}));

vi.mock('../MessageInput', () => ({
  MessageInput: ({ onSend, onCancel }: { onSend: (msg: string) => void; onCancel?: () => void }) => (
    <div>
      <button type="button" data-testid="mock-send" onClick={() => onSend('Write chapter 1')}>
        Send
      </button>
      {onCancel && (
        <button type="button" data-testid="mock-cancel" onClick={onCancel}>
          Cancel
        </button>
      )}
    </div>
  ),
}));

vi.mock('../subscription/ChatQuotaCard', () => ({
  ChatQuotaCard: () => null,
}));

vi.mock('../subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: () => null,
}));

import { ChatPanel } from '../ChatPanel';

function renderChatPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel />
    </QueryClientProvider>,
  );
}

describe('ChatPanelHeader legacy default session title localization', () => {
  beforeEach(() => {
    mocks.language = 'en';
  });

  it('displays localized English titles for legacy Chinese default session titles', () => {
    const headerRef = createRef<HTMLDivElement>();
    const first = render(
      <ChatPanelHeader
        headerRef={headerRef}
        isMobile={false}
        isStreaming={false}
        isLoadingHistory={false}
        isCreatingSession={false}
        newChatPending={false}
        isAlreadyEmptyNewChat={false}
        sessionTitle="AI 助手对话"
        onOpenAIMemory={vi.fn()}
        onRequestNewChat={vi.fn()}
      />,
    );
    expect(screen.getByTestId('chat-panel-header-title')).toHaveTextContent('AI Assistant');
    first.unmount();

    const second = render(
      <ChatPanelHeader
        headerRef={headerRef}
        isMobile={false}
        isStreaming={false}
        isLoadingHistory={false}
        isCreatingSession={false}
        newChatPending={false}
        isAlreadyEmptyNewChat={false}
        sessionTitle="新对话"
        onOpenAIMemory={vi.fn()}
        onRequestNewChat={vi.fn()}
      />,
    );
    expect(screen.getByTestId('chat-panel-header-title')).toHaveTextContent('New Chat');
    second.unmount();
  });
});

describe('isUnexpectedStreamDisconnect predicate', () => {
  it('matches only retryable STREAM_CLOSED / STREAM_ERROR transport drops', () => {
    expect(isUnexpectedStreamDisconnect('STREAM_CLOSED', true)).toBe(true);
    expect(isUnexpectedStreamDisconnect('STREAM_ERROR', true)).toBe(true);
    expect(isUnexpectedStreamDisconnect('STREAM_ERROR', false)).toBe(false);
    expect(isUnexpectedStreamDisconnect('ERR_INTERNAL', true)).toBe(false);
    expect(isUnexpectedStreamDisconnect('ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED', false)).toBe(false);
  });
});

describe('ChatPanel read-only SSE disconnect auto-reconcile', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.language = 'en';
    projectStore.setProjectId('proj-1');
    mocks.streamState = {
      isStreaming: false,
      isThinking: false,
      error: null,
      errorCode: null,
      retryable: false,
    };
    mocks.getRecentMessages.mockResolvedValue([]);
    mocks.createNewSession.mockResolvedValue({ id: 'sess-new', title: 'New Chat' });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('performs a single delayed read-only GET reconcile on unexpected SSE disconnect and clears the error without ever retrying POST', async () => {
    // Initial mount history load returns empty
    mocks.getRecentMessages.mockResolvedValueOnce([]);
    // Reconcile GET returns the assistant reply that the backend committed after disconnect
    mocks.getRecentMessages.mockResolvedValueOnce([
      {
        id: 'u-1',
        session_id: 'sess-1',
        role: 'user',
        content: 'Write chapter 1',
        created_at: '2026-11-28T10:00:00Z',
      },
      {
        id: 'a-1',
        session_id: 'sess-1',
        role: 'assistant',
        content: 'Chapter 1 has been drafted and saved.',
        created_at: '2026-11-28T10:01:00Z',
      },
    ]);

    renderChatPanel();
    await waitFor(() => expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1));

    // User sends one message (1 POST via startStream)
    act(() => {
      screen.getByTestId('mock-send').click();
    });
    expect(mocks.startStream).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();

    // Unexpected SSE disconnect fires (STREAM_CLOSED, retryable: true)
    act(() => {
      mocks.streamOptions?.onError?.('Connection lost. Please try again.', 'STREAM_CLOSED', true);
    });

    // Not called immediately before the 1.5s grace window
    expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1);

    // Advance by the single 1.5s delay
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_DISCONNECT_RECONCILE_DELAY_MS);
    });

    // Exactly ONE read-only GET reconcile ran (mount + 1 reconcile = 2 calls), ZERO automatic POST retries
    expect(mocks.getRecentMessages).toHaveBeenCalledTimes(2);
    expect(mocks.getRecentMessages).toHaveBeenLastCalledWith('proj-1', 50);
    expect(mocks.startStream).toHaveBeenCalledTimes(1);
    expect(mocks.clearError).toHaveBeenCalledTimes(1);
    expect(mocks.triggerFileTreeRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('mock-message-list')).toHaveTextContent('Chapter 1 has been drafted and saved.');
  });

  it('does NOT run disconnect reconcile on non-disconnect errors (e.g. server ERR_INTERNAL)', async () => {
    mocks.getRecentMessages.mockResolvedValueOnce([]);
    renderChatPanel();
    await waitFor(() => expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1));

    vi.useFakeTimers();
    act(() => {
      mocks.streamOptions?.onError?.('Server error', 'ERR_INTERNAL', true);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_DISCONNECT_RECONCILE_DELAY_MS * 2);
    });

    expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1);
    expect(mocks.clearError).not.toHaveBeenCalled();
  });

  it('cancels the pending disconnect reconcile timer when ChatPanel unmounts during the delay', async () => {
    mocks.getRecentMessages.mockResolvedValueOnce([]);
    const view = renderChatPanel();
    await waitFor(() => expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1));

    vi.useFakeTimers();
    act(() => {
      mocks.streamOptions?.onError?.('Connection lost. Please try again.', 'STREAM_CLOSED', true);
    });

    view.unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_DISCONNECT_RECONCILE_DELAY_MS * 2);
    });

    expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1);
    expect(mocks.clearError).not.toHaveBeenCalled();
  });

  it('cancels pending reconcile for the previous project when the user switches projects during the delay', async () => {
    mocks.getRecentMessages.mockResolvedValue([]);
    renderChatPanel();
    await waitFor(() => expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1));
    expect(mocks.getRecentMessages).toHaveBeenLastCalledWith('proj-1', 50);

    vi.useFakeTimers();
    act(() => {
      mocks.streamOptions?.onError?.('Connection lost. Please try again.', 'STREAM_CLOSED', true);
    });

    // Switch project before the 1.5s reconcile timer fires
    act(() => {
      projectStore.setProjectId('proj-2');
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_DISCONNECT_RECONCILE_DELAY_MS * 2);
    });

    // Only initial mount loads for proj-1 and proj-2 should run; no delayed reconcile for proj-1
    expect(mocks.getRecentMessages.mock.calls.filter(([pid]) => pid === 'proj-1')).toHaveLength(1);
    expect(mocks.getRecentMessages.mock.calls.filter(([pid]) => pid === 'proj-2')).toHaveLength(1);
    expect(mocks.clearError).not.toHaveBeenCalled();
  });

  it('skips disconnect reconcile when the user explicitly stopped generation', async () => {
    mocks.getRecentMessages.mockResolvedValueOnce([]);
    mocks.startStream.mockImplementationOnce(() => {
      mocks.streamState.isStreaming = true;
    });
    mocks.stop.mockImplementationOnce(() => {
      mocks.streamState.isStreaming = false;
    });

    renderChatPanel();
    await waitFor(() => expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1));

    act(() => {
      screen.getByTestId('mock-send').click();
    });
    act(() => {
      screen.getByTestId('mock-cancel').click();
    });
    expect(mocks.stop).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    act(() => {
      mocks.streamOptions?.onError?.('Connection lost. Please try again.', 'STREAM_CLOSED', true);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_DISCONNECT_RECONCILE_DELAY_MS * 2);
    });

    expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1);
    expect(mocks.clearError).not.toHaveBeenCalled();
  });

  it('drops stale in-flight reconcile GET results when the user starts a New Chat before GET resolves', async () => {
    mocks.getRecentMessages.mockResolvedValueOnce([]);
    let resolveReconcile!: (rows: Array<Record<string, unknown>>) => void;
    const reconcilePromise = new Promise<Array<Record<string, unknown>>>((resolve) => {
      resolveReconcile = resolve;
    });
    mocks.getRecentMessages.mockReturnValueOnce(reconcilePromise);

    renderChatPanel();
    await waitFor(() => expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1));

    act(() => {
      screen.getByTestId('mock-send').click();
    });

    vi.useFakeTimers();
    act(() => {
      mocks.streamOptions?.onError?.('Connection lost. Please try again.', 'STREAM_CLOSED', true);
    });

    // Advance timer so the GET reconcile request is now in flight
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_DISCONNECT_RECONCILE_DELAY_MS);
    });
    expect(mocks.getRecentMessages).toHaveBeenCalledTimes(2);

    // User clicks "New chat" while the reconcile GET is still in flight
    await act(async () => {
      screen.getByRole('button', { name: 'New chat' }).click();
    });
    expect(mocks.createNewSession).toHaveBeenCalledTimes(1);

    // Now the stale reconcile GET resolves with old session messages
    await act(async () => {
      resolveReconcile([
        {
          id: 'u-old',
          session_id: 'sess-1',
          role: 'user',
          content: 'Write chapter 1',
          created_at: '2026-11-28T10:00:00Z',
        },
        {
          id: 'a-old',
          session_id: 'sess-1',
          role: 'assistant',
          content: 'Stale chapter from previous session.',
          created_at: '2026-11-28T10:01:00Z',
        },
      ]);
      await Promise.resolve();
    });

    expect(screen.queryByTestId('mock-message-list')).toBeNull();
    expect(document.body).not.toHaveTextContent('Stale chapter from previous session.');
    expect(mocks.clearError).not.toHaveBeenCalled();
  });

  it('drops stale in-flight reconcile GET results when the user sends a new message before GET resolves', async () => {
    mocks.getRecentMessages.mockResolvedValueOnce([]);
    let resolveReconcile!: (rows: Array<Record<string, unknown>>) => void;
    const reconcilePromise = new Promise<Array<Record<string, unknown>>>((resolve) => {
      resolveReconcile = resolve;
    });
    mocks.getRecentMessages.mockReturnValueOnce(reconcilePromise);

    renderChatPanel();
    await waitFor(() => expect(mocks.getRecentMessages).toHaveBeenCalledTimes(1));

    act(() => {
      screen.getByTestId('mock-send').click();
    });
    expect(mocks.startStream).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    act(() => {
      mocks.streamOptions?.onError?.('Connection lost. Please try again.', 'STREAM_CLOSED', true);
    });

    // Advance timer so the GET reconcile request is now in flight
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SSE_DISCONNECT_RECONCILE_DELAY_MS);
    });
    expect(mocks.getRecentMessages).toHaveBeenCalledTimes(2);

    // User manually sends a fresh message while the reconcile GET is still in flight
    act(() => {
      screen.getByTestId('mock-send').click();
    });
    expect(mocks.startStream).toHaveBeenCalledTimes(2);

    // Now the stale reconcile GET resolves
    await act(async () => {
      resolveReconcile([
        {
          id: 'u-stale',
          session_id: 'sess-1',
          role: 'user',
          content: 'Write chapter 1',
          created_at: '2026-11-28T10:00:00Z',
        },
        {
          id: 'a-stale',
          session_id: 'sess-1',
          role: 'assistant',
          content: 'Stale reply that must not clobber the active send.',
          created_at: '2026-11-28T10:01:00Z',
        },
      ]);
      await Promise.resolve();
    });

    expect(screen.getByTestId('mock-message-list')).not.toHaveTextContent('Stale reply that must not clobber the active send.');
    expect(mocks.clearError).not.toHaveBeenCalled();
  });
});
