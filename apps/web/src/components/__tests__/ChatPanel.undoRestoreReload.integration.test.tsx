/**
 * P0 Integration: Undo & delete-restore tool card state stays synchronized with
 * persisted server state across full ChatPanel unmount/remount (reload).
 *
 * Renders the REAL ChatPanel + REAL MessageList + REAL ToolResultCard (no mocking
 * of MessageList or ToolResultCard) with stateful persistence on getRecentMessages,
 * fileVersionApi.rollback, and fileApi.restore.
 */
import * as React from 'react';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  getRecentMessages: vi.fn(),
  rollback: vi.fn(),
  restore: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  triggerFileTreeRefresh: vi.fn(),
  triggerEditorRefresh: vi.fn(),
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1', email: 'author@example.com' } }),
}));

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: vi.fn() },
  useTranslation: () => ({
    t: (key: string, opts?: string | Record<string, unknown>) =>
      typeof opts === 'string' ? opts : key,
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: 'project-1',
    currentProject: { id: 'project-1', name: 'Test Novel', project_type: 'novel' },
    selectedItem: null,
    fileTreeVersion: 0,
    refreshProjects: vi.fn(async () => {}),
    refreshProject: vi.fn(async () => {}),
    triggerFileTreeRefresh: mocks.triggerFileTreeRefresh,
    triggerEditorRefresh: mocks.triggerEditorRefresh,
    setSelectedItem: vi.fn(),
    setIsChatStreaming: vi.fn(),
    setAiEditingFileId: vi.fn(),
    pendingAgentUiAction: null,
    consumeAgentUiAction: vi.fn(),
    appendFileContent: vi.fn(),
    finishFileStreaming: vi.fn(),
    startFileStreaming: vi.fn(),
    streamingFileId: null,
  }),
}));

vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({ isMobile: false, switchToEditor: vi.fn(), focusChatInput: vi.fn() }),
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

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getQuota: vi.fn(async () => ({ ai_conversations: { used: 0, limit: 10, reset_at: null } })),
    getStatus: vi.fn(async () => ({ tier: 'pro' })),
  },
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'user-1'],
    quota: () => ['subscription-quota', 'user-1'],
    quotaLite: () => ['quota', 'user-1'],
  },
}));

vi.mock('../../lib/firstWorkApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/firstWorkApi')>()),
  firstWorkApi: {
    get: vi.fn(async () => ({
      status: 'ineligible',
      project_id: null,
      file_id: null,
      available: false,
      static_guidance: false,
      opened_at: null,
      first_manual_write_at: null,
      first_ai_requested_at: null,
    })),
  },
}));

vi.mock('../../lib/activationEvents', () => ({ reportActivationEvent: vi.fn() }));
vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn(), captureException: vi.fn() }));
vi.mock('../../lib/upgradeAnalytics', () => ({ trackUpgradeExpose: vi.fn(), trackUpgradeClick: vi.fn() }));
vi.mock('../../lib/toast', () => ({
  toast: { error: mocks.toastError, success: mocks.toastSuccess, info: vi.fn() },
}));

vi.mock('../../hooks/useDraftPersistence', () => ({
  useDraftPersistence: () => ({ draft: '', saveDraft: vi.fn(), clearDraft: vi.fn() }),
}));

vi.mock('../../lib/chatApi', () => ({
  getRecentMessages: mocks.getRecentMessages,
  createNewSession: vi.fn(async () => ({ id: 'session-1' })),
  submitMessageFeedback: vi.fn(),
}));

vi.mock('../../lib/agentApi', () => ({
  fetchSuggestions: vi.fn(async () => []),
  streamAgentRequest: vi.fn(() => new AbortController()),
  sendSteeringRequest: vi.fn(),
  stopAgentRun: vi.fn(),
}));

vi.mock('../../lib/api', () => ({
  fileVersionApi: { rollback: mocks.rollback },
  projectApi: { getProgress: vi.fn(async () => []) },
  fileApi: { get: vi.fn(), restore: mocks.restore },
}));

vi.mock('../MessageInput', () => ({
  MessageInput: () => <div data-testid="message-input-stub" />,
}));

import { ApiError } from '../../lib/apiClient';
import { ChatPanel } from '../ChatPanel';

interface PersistedToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: string;
  result: { data: Record<string, unknown> };
}

interface PersistedMessage {
  id: string;
  session_id: string;
  role: 'assistant' | 'user';
  content: string;
  created_at: string;
  metadata: null;
  tool_calls: string;
}

function mountChatPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ChatPanel />
      </BrowserRouter>
    </QueryClientProvider>,
  );
}

describe('ChatPanel undo / delete-restore card state sync across reload', () => {
  const hash1 = 'a'.repeat(64);
  const hash2 = 'b'.repeat(64);

  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom lacks scrollIntoView on elements
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    cleanup();
  });

  it('persists edit_file undo state across reload and prevents double-undo', async () => {
    let persistedTools: PersistedToolCall[] = [
      {
        id: 'tool-edit-1',
        name: 'edit_file',
        arguments: { file_id: 'ch-1' },
        status: 'success',
        result: {
          data: {
            id: 'ch-1',
            title: '第一章',
            edits_applied: 1,
            details: [{ op: 'replace', line: 1 }],
            undo: {
              before_version_number: 2,
              expected_after_updated_at: '2026-11-26T08:00:00.000001Z',
              expected_after_content_sha256: hash1,
              undone: false,
            },
          },
        },
      },
    ];

    const buildMessages = (): PersistedMessage[] => [
      {
        id: 'msg-edit-1',
        session_id: 'session-1',
        role: 'assistant',
        content: '已完成第一章润色。',
        created_at: '2026-11-26T08:00:01Z',
        metadata: null,
        tool_calls: JSON.stringify(persistedTools),
      },
    ];

    mocks.getRecentMessages.mockImplementation(async () => buildMessages());
    mocks.rollback.mockImplementation(async (_fileId, _beforeVersion, _updatedAt, opts) => {
      // Simulate server persisting undo.undone = true on the chat message tool_calls JSON
      persistedTools = persistedTools.map((tc) =>
        tc.id === opts?.toolUseId
          ? {
              ...tc,
              result: {
                ...tc.result,
                data: {
                  ...tc.result.data,
                  undo: { ...(tc.result.data.undo as Record<string, unknown>), undone: true },
                },
              },
            }
          : tc,
      );
      return { snapshot_created: true };
    });

    const firstMount = mountChatPanel();

    // Real ToolResultCard renders Undo button
    const undoBtn = await screen.findByRole('button', { name: 'common:undo' });
    fireEvent.click(undoBtn);

    // Real in-app confirmation dialog appears
    await screen.findByRole('dialog');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'chat:actions.undo' }));
    });

    expect(mocks.rollback).toHaveBeenCalledTimes(1);
    expect(mocks.rollback).toHaveBeenCalledWith(
      'ch-1',
      2,
      '2026-11-26T08:00:00.000001Z',
      {
        chatMessageId: 'msg-edit-1',
        toolUseId: 'tool-edit-1',
        expectedAfterContentSha256: hash1,
      },
    );
    expect(mocks.triggerEditorRefresh).toHaveBeenCalledWith('ch-1');

    // Immediately reflects undone state and stops offering undo
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'common:undo' })).not.toBeInTheDocument();
    });
    expect(screen.getByText('chat:tool.edit_undone')).toBeInTheDocument();

    // Simulate full page reload by unmounting and remounting ChatPanel
    firstMount.unmount();
    mountChatPanel();

    // Reloaded card reads server-persisted undone=true and MUST NOT offer a second undo
    expect(await screen.findByText('chat:tool.edit_undone')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'common:undo' })).not.toBeInTheDocument();
    expect(mocks.rollback).toHaveBeenCalledTimes(1);
  });

  it('persists delete_file restore state across reload and prevents double-restore', async () => {
    let persistedTools: PersistedToolCall[] = [
      {
        id: 'tool-del-1',
        name: 'delete_file',
        arguments: { file_id: 'ch-deleted' },
        status: 'success',
        result: {
          data: {
            id: 'ch-deleted',
            title: '废稿章节',
            mutation_applied: true,
            restored: false,
          },
        },
      },
    ];

    const buildMessages = (): PersistedMessage[] => [
      {
        id: 'msg-del-1',
        session_id: 'session-1',
        role: 'assistant',
        content: '已删除废稿章节。',
        created_at: '2026-11-26T08:10:00Z',
        metadata: null,
        tool_calls: JSON.stringify(persistedTools),
      },
    ];

    mocks.getRecentMessages.mockImplementation(async () => buildMessages());
    mocks.restore.mockImplementation(async (fileId, card) => {
      // Simulate server persisting restored=true on the chat message tool_calls JSON
      persistedTools = persistedTools.map((tc) =>
        tc.id === card?.toolUseId
          ? { ...tc, result: { ...tc.result, data: { ...tc.result.data, restored: true } } }
          : tc,
      );
      return { id: fileId, file_ids: [fileId] };
    });

    const firstMount = mountChatPanel();

    // Real ToolResultCard renders Restore button
    const restoreBtn = await screen.findByRole('button', { name: 'chat:tool.restoreDeleted' });
    await act(async () => {
      fireEvent.click(restoreBtn);
    });

    expect(mocks.restore).toHaveBeenCalledTimes(1);
    expect(mocks.restore).toHaveBeenCalledWith('ch-deleted', {
      chatMessageId: 'msg-del-1',
      toolUseId: 'tool-del-1',
    });
    expect(mocks.triggerFileTreeRefresh).toHaveBeenCalled();

    // Immediately reflects restored state and removes Restore button
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'chat:tool.restoreDeleted' })).not.toBeInTheDocument();
    });
    expect(screen.getByText('chat:tool.deletionRestored')).toBeInTheDocument();

    // Simulate full page reload by unmounting and remounting ChatPanel
    firstMount.unmount();
    mountChatPanel();

    // Reloaded card reads server-persisted restored=true and MUST NOT offer a second restore
    expect(await screen.findByText('chat:tool.deletionRestored')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'chat:tool.restoreDeleted' })).not.toBeInTheDocument();
    expect(mocks.restore).toHaveBeenCalledTimes(1);
  });

  it('keeps an earlier card blocked until a later edit is undone or proven stale via 409', async () => {
    const persistedTools: PersistedToolCall[] = [
      {
        id: 'tool-earlier',
        name: 'edit_file',
        arguments: { file_id: 'ch-1' },
        status: 'success',
        result: {
          data: {
            id: 'ch-1',
            title: '第一章',
            edits_applied: 1,
            details: [{ op: 'replace', line: 1 }],
            undo: {
              before_version_number: 1,
              expected_after_updated_at: '2026-11-26T08:00:00.000001Z',
              expected_after_content_sha256: hash1,
              undone: false,
            },
          },
        },
      },
      {
        id: 'tool-later',
        name: 'edit_file',
        arguments: { file_id: 'ch-1' },
        status: 'success',
        result: {
          data: {
            id: 'ch-1',
            title: '第一章',
            edits_applied: 1,
            details: [{ op: 'replace', line: 2 }],
            undo: {
              before_version_number: 2,
              expected_after_updated_at: '2026-11-26T08:05:00.000001Z',
              expected_after_content_sha256: hash2,
              undone: false,
            },
          },
        },
      },
    ];

    mocks.getRecentMessages.mockResolvedValue([
      {
        id: 'msg-two-edits',
        session_id: 'session-1',
        role: 'assistant',
        content: '连续修改了两次。',
        created_at: '2026-11-26T08:05:01Z',
        metadata: null,
        tool_calls: JSON.stringify(persistedTools),
      },
    ]);
    mocks.rollback.mockRejectedValueOnce(
      new ApiError(409, 'ERR_RESOURCE_CONFLICT', { reason: 'stale_write' }),
    );

    mountChatPanel();

    // Two undo buttons render: earlier is disabled (blocked), later is enabled
    const undoButtons = await screen.findAllByRole('button', { name: 'common:undo' });
    expect(undoButtons).toHaveLength(2);
    expect(undoButtons[0]).toBeDisabled();
    expect(undoButtons[1]).toBeEnabled();

    // Clicking the later edit hits 409 stale_write -> toast shown, dialog closes, earlier card becomes unblocked
    fireEvent.click(undoButtons[1]);
    await screen.findByRole('dialog');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'chat:actions.undo' }));
    });

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('versions:undoStaleWrite'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'common:undo' })[0]).toBeEnabled());
  });
});
