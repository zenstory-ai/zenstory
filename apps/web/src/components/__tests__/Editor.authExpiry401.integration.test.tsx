/**
 * P0 Integration: 401 token refresh failure mid-edit preserves unsaved draft
 * in user/project/file-scoped recovery storage and offers safe recovery after re-login.
 *
 * Uses the real apiClient (fetch stubbed at HTTP boundary), real AuthProvider,
 * real ProjectProvider, real Editor + SimpleEditor, and real editorDraftRecovery.
 */
import * as React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock('../../lib/analytics', () => ({
  trackEvent: vi.fn(),
  captureException: vi.fn(),
  identifyUser: vi.fn(),
  resetAnalytics: vi.fn(),
}));
vi.mock('../../lib/upgradeAnalytics', () => ({ trackUpgradeExpose: vi.fn(), trackUpgradeClick: vi.fn() }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, opts?: string | Record<string, unknown>) => (typeof opts === 'string' ? opts : key) }),
}));
vi.mock('../../contexts/MaterialLibraryContext', () => ({ useMaterialLibraryContext: () => ({ preview: null }) }));
vi.mock('../../contexts/MaterialAttachmentContext', () => ({ useMaterialAttachment: () => ({ addMaterial: vi.fn() }) }));
vi.mock('../../contexts/TextQuoteContext', () => ({ useTextQuote: () => ({ addQuote: vi.fn() }) }));
vi.mock('../../contexts/MobileLayoutContext', () => ({ useMobileLayout: () => ({ isMobile: false }) }));

import { AuthProvider, useAuth } from '../../contexts/AuthContext';
import { ProjectProvider, useProject } from '../../contexts/ProjectContext';
import { clearAuthStorage } from '../../lib/apiClient';
import { getEditorDraftRecoveryKey, readEditorDraftSnapshot } from '../../lib/editorDraftRecovery';
import { Editor } from '../Editor';

const PROJECT_ID = 'project-401';
const FILE_ID = 'chapter-401';
const T1 = '2026-11-26T08:00:00.000001Z';
const T2 = '2026-11-26T09:30:00.000001Z';

interface MockServerState {
  activeUser: { id: string; email: string; username: string };
  authExpired: boolean;
  file: {
    id: string;
    project_id: string;
    file_type: 'draft';
    title: string;
    content: string;
    updated_at: string;
  };
  putCalls: Array<{ title?: string; content?: string; base_updated_at?: string }>;
  refreshCalls: number;
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function SelectedFileWorkspace() {
  const { currentProjectId, setCurrentProjectId, selectedItem, setSelectedItem } = useProject();

  React.useEffect(() => {
    if (!currentProjectId) {
      setCurrentProjectId(PROJECT_ID);
    }
  }, [currentProjectId, setCurrentProjectId]);

  React.useEffect(() => {
    if (currentProjectId === PROJECT_ID && !selectedItem) {
      setSelectedItem({ id: FILE_ID, type: 'draft', title: '第1章' });
    }
  }, [currentProjectId, selectedItem, setSelectedItem]);

  return <Editor />;
}

function ProtectedAppShell() {
  const { user, loading } = useAuth();
  if (loading) return <div data-testid="auth-loading">loading</div>;
  if (!user) return <div data-testid="login-screen">login</div>;
  return (
    <ProjectProvider>
      <SelectedFileWorkspace />
    </ProjectProvider>
  );
}

describe('Editor 401 refresh failure mid-edit draft preservation', () => {
  let state: MockServerState;
  let tokenSeq = 0;

  function installFetchMock() {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      const path = url.pathname;
      const method = (init?.method ?? 'GET').toUpperCase();

      if (path === '/api/auth/me') {
        if (state.authExpired) return jsonResponse({ detail: 'Unauthorized' }, 401);
        return jsonResponse(state.activeUser);
      }
      if (path === '/api/auth/refresh') {
        state.refreshCalls += 1;
        return jsonResponse({ detail: 'Refresh token expired' }, 401);
      }
      if (path === '/api/auth/logout') {
        return jsonResponse({ ok: true });
      }
      if (path === '/api/v1/projects' && method === 'GET') {
        return jsonResponse([{ id: PROJECT_ID, name: 'Test Novel', project_type: 'novel', updated_at: T1 }]);
      }
      if (path === `/api/v1/projects/${PROJECT_ID}/file-tree` && method === 'GET') {
        return jsonResponse({
          tree: [{
            id: 'folder-drafts',
            title: '正文',
            file_type: 'folder',
            parent_id: null,
            children: [{ ...state.file, parent_id: 'folder-drafts', children: [] }],
          }],
        });
      }
      if (path === `/api/v1/files/${FILE_ID}` && method === 'GET') {
        if (state.authExpired) return jsonResponse({ detail: 'Unauthorized' }, 401);
        return jsonResponse(state.file);
      }
      if (path === `/api/v1/files/${FILE_ID}` && method === 'PUT') {
        const body = typeof init?.body === 'string' ? JSON.parse(init.body) : {};
        state.putCalls.push(body);
        if (state.authExpired) return jsonResponse({ detail: 'Token expired' }, 401);
        state.file = {
          ...state.file,
          title: body.title ?? state.file.title,
          content: body.content ?? state.file.content,
          updated_at: T2,
        };
        return jsonResponse(state.file);
      }
      if (path.includes('/versions') && method === 'GET') {
        return jsonResponse({ total: 0, versions: [] });
      }
      return jsonResponse({ detail: `Unhandled ${method} ${path}` }, 404);
    }));
  }

  function seedSession(user: { id: string; email: string; username: string }) {
    tokenSeq += 1;
    localStorage.setItem('access_token', `access-${user.id}-${tokenSeq}`);
    localStorage.setItem('refresh_token', `refresh-${user.id}-${tokenSeq}`);
    localStorage.setItem('user', JSON.stringify(user));
  }

  beforeEach(() => {
    vi.clearAllMocks();
    clearAuthStorage('test-reset');
    localStorage.clear();
    sessionStorage.clear();
    state = {
      activeUser: { id: 'user-1', email: 'author1@example.com', username: 'author1' },
      authExpired: false,
      file: {
        id: FILE_ID,
        project_id: PROJECT_ID,
        file_type: 'draft',
        title: '第1章',
        content: '最初的正文内容。',
        updated_at: T1,
      },
      putCalls: [],
      refreshCalls: 0,
    };
    installFetchMock();
  });

  afterEach(async () => {
    cleanup();
    // Flush any async unmount save promises before wiping localStorage so
    // an unmounted editor from a prior test cannot write into the next test's storage.
    await act(async () => {
      for (let i = 0; i < 10; i++) await Promise.resolve();
    });
    clearAuthStorage('test-cleanup');
    localStorage.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('persists unsaved draft under user/project/file scope when 401 refresh fails mid-edit and recovers it after re-login', async () => {
    seedSession(state.activeUser);
    const { unmount } = render(
      <AuthProvider>
        <ProtectedAppShell />
      </AuthProvider>,
    );

    const textarea = (await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe('最初的正文内容。'));

    const unsavedDraft = '最初的正文内容。\n\n在 401 过期前刚刚写下的关键伏笔。';
    fireEvent.change(textarea, { target: { value: unsavedDraft } });
    expect(textarea.value).toBe(unsavedDraft);

    // Expire both access and refresh tokens on the server, then trigger save (Cmd+S)
    state.authExpired = true;
    fireEvent.keyDown(textarea, { key: 's', ctrlKey: true });

    // apiClient catches 401 -> attempts refresh -> fails -> dispatches auth:logout -> ProtectedAppShell unmounts Editor
    await waitFor(() => expect(screen.getByTestId('login-screen')).toBeInTheDocument(), { timeout: 5000 });
    expect(state.refreshCalls).toBeGreaterThanOrEqual(1);

    // Unsaved draft must be persisted safely in user/project/file-scoped recovery storage
    const scope = { userId: 'user-1', projectId: PROJECT_ID, fileId: FILE_ID };
    await waitFor(() => {
      const snapshot = readEditorDraftSnapshot(localStorage, scope);
      expect(snapshot).not.toBeNull();
      expect(snapshot?.content).toBe(unsavedDraft);
      expect(snapshot?.baseUpdatedAt).toBe(T1);
    });
    expect(localStorage.getItem(getEditorDraftRecoveryKey(scope))).toBeTruthy();

    unmount();

    // Same user re-logs in -> opens file -> unsaved draft is recovered with no lost text
    state.authExpired = false;
    seedSession(state.activeUser);
    render(
      <AuthProvider>
        <ProtectedAppShell />
      </AuthProvider>,
    );

    const recoveredTextarea = (await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')) as HTMLTextAreaElement;
    await waitFor(() => expect(recoveredTextarea.value).toBe(unsavedDraft));
  });

  it('requires explicit recovery choice instead of auto-overwriting when server updated_at advanced during re-login', async () => {
    seedSession(state.activeUser);
    const { unmount } = render(
      <AuthProvider>
        <ProtectedAppShell />
      </AuthProvider>,
    );

    const textarea = (await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe('最初的正文内容。'));

    const unsavedDraft = '作者在掉线前写的版本。';
    fireEvent.change(textarea, { target: { value: unsavedDraft } });

    state.authExpired = true;
    fireEvent.keyDown(textarea, { key: 's', ctrlKey: true });

    await waitFor(() => expect(screen.getByTestId('login-screen')).toBeInTheDocument(), { timeout: 5000 });
    const scope = { userId: 'user-1', projectId: PROJECT_ID, fileId: FILE_ID };
    await waitFor(() => expect(readEditorDraftSnapshot(localStorage, scope)?.content).toBe(unsavedDraft));
    unmount();

    // Server content and updated_at moved forward while the author was logged out
    state.authExpired = false;
    state.putCalls = [];
    state.file = {
      ...state.file,
      content: '另一端已保存的新服务端正文。',
      updated_at: T2,
    };
    seedSession(state.activeUser);

    render(
      <AuthProvider>
        <ProtectedAppShell />
      </AuthProvider>,
    );

    const reopenedTextarea = (await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')) as HTMLTextAreaElement;
    await waitFor(() => expect(reopenedTextarea.value).toBe('另一端已保存的新服务端正文。'));

    // Conflict banner must offer explicit recovery choice and must NOT auto-overwrite server copy
    expect(await screen.findByText('editor:draftRecovery.compare')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'editor:draftRecovery.discard' })).toBeInTheDocument();
    expect(screen.getByText(unsavedDraft)).toBeInTheDocument();
    expect(state.putCalls).toHaveLength(0);
    // The recovery snapshot remains intact until the user makes an explicit choice
    expect(readEditorDraftSnapshot(localStorage, scope)?.content).toBe(unsavedDraft);
  });

  it('isolates the saved recovery draft by userId so a different user logging in cannot see it', async () => {
    seedSession(state.activeUser);
    const { unmount } = render(
      <AuthProvider>
        <ProtectedAppShell />
      </AuthProvider>,
    );

    const textarea = (await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe('最初的正文内容。'));

    const privateDraft = '作者一的私密未保存草稿。';
    fireEvent.change(textarea, { target: { value: privateDraft } });

    state.authExpired = true;
    fireEvent.keyDown(textarea, { key: 's', ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId('login-screen')).toBeInTheDocument(), { timeout: 5000 });
    await waitFor(() =>
      expect(readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: PROJECT_ID, fileId: FILE_ID })?.content).toBe(privateDraft),
    );
    unmount();

    // Different user signs into the same browser
    state.authExpired = false;
    state.activeUser = { id: 'user-2', email: 'author2@example.com', username: 'author2' };
    seedSession(state.activeUser);

    render(
      <AuthProvider>
        <ProtectedAppShell />
      </AuthProvider>,
    );

    const otherUserTextarea = (await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')) as HTMLTextAreaElement;
    await waitFor(() => expect(otherUserTextarea.value).toBe('最初的正文内容。'));
    await act(async () => {
      await Promise.resolve();
    });
    expect(otherUserTextarea.value).not.toContain(privateDraft);
  });
});
