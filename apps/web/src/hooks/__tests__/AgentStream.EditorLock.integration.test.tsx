/**
 * P0 Integration: SSE mid-stream disconnect (EOF, transport error, or project switch)
 * after `file_edit_start` must ALWAYS release `aiEditingFileId` / `isAiEditing`
 * and streaming state so editor autosave is never permanently locked.
 *
 * Uses the real streamAgentRequest (agentApi), real useAgentStream, real
 * useChatStreaming, real ProjectProvider, and real Editor + SimpleEditor.
 */
import * as React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const server = vi.hoisted(() => ({
  getFile: vi.fn(),
  updateFile: vi.fn(),
  getTree: vi.fn(),
}));
const auth = vi.hoisted(() => ({ value: { user: { id: 'user-1' } } }));

vi.mock('../../lib/api', () => ({
  projectApi: {
    getAll: vi.fn(async () => [
      { id: 'project-1', name: 'Novel 1', project_type: 'novel', updated_at: '2026-11-26T00:00:00Z' },
      { id: 'project-2', name: 'Novel 2', project_type: 'novel', updated_at: '2026-11-26T00:00:00Z' },
    ]),
    get: vi.fn(),
  },
  fileApi: {
    get: server.getFile,
    update: server.updateFile,
    getTree: server.getTree,
    create: vi.fn(),
  },
  fileVersionApi: { getVersions: vi.fn(async () => ({ total: 0, versions: [] })) },
}));
vi.mock('../../lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn(), captureException: vi.fn() }));
vi.mock('../../lib/upgradeAnalytics', () => ({ trackUpgradeExpose: vi.fn(), trackUpgradeClick: vi.fn() }));
vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: vi.fn() },
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => auth.value }));
vi.mock('../../contexts/MaterialLibraryContext', () => ({ useMaterialLibraryContext: () => ({ preview: null }) }));
vi.mock('../../contexts/MaterialAttachmentContext', () => ({ useMaterialAttachment: () => ({ addMaterial: vi.fn() }) }));
vi.mock('../../contexts/TextQuoteContext', () => ({ useTextQuote: () => ({ addQuote: vi.fn() }) }));
vi.mock('../../contexts/MobileLayoutContext', () => ({ useMobileLayout: () => ({ isMobile: false }) }));

import { ProjectProvider, useProject } from '../../contexts/ProjectContext';
import { useAgentStream } from '../useAgentStream';
import { useChatStreaming } from '../useChatStreaming';
import { Editor } from '../../components/Editor';

const FILE_ID = 'ch-lock-1';
const INITIAL_FILE = {
  id: FILE_ID,
  project_id: 'project-1',
  file_type: 'draft' as const,
  title: '第1章',
  content: '开篇正文。',
  updated_at: '2026-11-26T08:00:00.000001Z',
};

interface ControlledStream {
  enqueue: (sseFrame: string) => void;
  close: () => void;
  error: (err: Error) => void;
  stream: ReadableStream<Uint8Array>;
}

function createControlledSSEStream(): ControlledStream {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(ctrl) {
      controller = ctrl;
    },
  });
  return {
    stream,
    enqueue: (frame: string) => controller.enqueue(encoder.encode(frame)),
    close: () => controller.close(),
    error: (err: Error) => controller.error(err),
  };
}

interface HarnessHandle {
  startStream: ReturnType<typeof useAgentStream>['startStream'];
  cancel: ReturnType<typeof useAgentStream>['cancel'];
  isStreaming: boolean;
  aiEditingFileId: string | null;
  streamingFileId: string | null;
  setCurrentProjectId: (id: string | null) => void;
}

const handle: { current: HarnessHandle | null } = { current: null };
const latestProjectView: {
  selectedItem: ReturnType<typeof useProject>['selectedItem'];
  streamingFileId: string | null;
} = {
  selectedItem: null,
  streamingFileId: null,
};

function IntegratedWorkspace() {
  const project = useProject();
  const { getStreamCallbacks } = useChatStreaming();

  const { currentProjectId, setCurrentProjectId, selectedItem, setSelectedItem } = project;
  const initializedRef = React.useRef(false);

  React.useLayoutEffect(() => {
    latestProjectView.selectedItem = project.selectedItem;
    latestProjectView.streamingFileId = project.streamingFileId;
  });

  React.useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    setCurrentProjectId('project-1');
  }, [setCurrentProjectId]);

  React.useEffect(() => {
    if (currentProjectId === 'project-1' && !selectedItem) {
      setSelectedItem({ id: FILE_ID, type: 'draft', title: INITIAL_FILE.title });
    }
  }, [currentProjectId, selectedItem, setSelectedItem]);

  const callbacks = React.useMemo(
    () =>
      getStreamCallbacks({
        triggerFileTreeRefresh: project.triggerFileTreeRefresh,
        triggerEditorRefresh: project.triggerEditorRefresh,
        setAiEditingFileId: project.setAiEditingFileId,
        setSelectedItem: project.setSelectedItem,
        getCurrentSelectedItem: () => latestProjectView.selectedItem,
        appendFileContent: project.appendFileContent,
        finishFileStreaming: project.finishFileStreaming,
        startFileStreaming: project.startFileStreaming,
        getCurrentStreamingFileId: () => latestProjectView.streamingFileId,
        activeProjectId: project.currentProjectId,
        t: (key: string) => key,
      }),
    [
      getStreamCallbacks,
      project.triggerFileTreeRefresh,
      project.triggerEditorRefresh,
      project.setAiEditingFileId,
      project.setSelectedItem,
      project.appendFileContent,
      project.finishFileStreaming,
      project.startFileStreaming,
      project.currentProjectId,
    ],
  );

  const agent = useAgentStream(project.currentProjectId, {
    onStart: callbacks.onStart,
    onThinking: callbacks.onThinking,
    onThinkingContent: callbacks.onThinkingContent,
    onSegmentStart: callbacks.onSegmentStart,
    onSegmentUpdate: callbacks.onSegmentUpdate,
    onSegmentUpdateToolCalls: callbacks.onSegmentUpdateToolCalls,
    onComplete: callbacks.onComplete,
    onError: callbacks.onError,
    onToolResult: callbacks.onToolResult,
    onFileCreated: callbacks.onFileCreated,
    onFileContent: callbacks.onFileContent,
    onFileContentEnd: callbacks.onFileContentEnd,
    onFileEditStart: callbacks.onFileEditStart,
    onFileEditApplied: callbacks.onFileEditApplied,
    onFileEditEnd: callbacks.onFileEditEnd,
  });

  React.useLayoutEffect(() => {
    handle.current = {
      startStream: agent.startStream,
      cancel: agent.cancel,
      isStreaming: agent.isStreaming,
      aiEditingFileId: project.aiEditingFileId,
      streamingFileId: project.streamingFileId,
      setCurrentProjectId: project.setCurrentProjectId,
    };
  });

  return <Editor />;
}

const flushMicrotasks = async () => {
  await act(async () => {
    for (let i = 0; i < 15; i++) {
      await Promise.resolve();
    }
  });
};

describe('SSE mid-stream disconnect releases isAiEditing / autosave lock', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem('access_token', 'test-token');
    localStorage.setItem('refresh_token', 'test-refresh');
    handle.current = null;

    server.getFile.mockResolvedValue({ ...INITIAL_FILE });
    server.getTree.mockResolvedValue({ tree: [] });
    server.updateFile.mockImplementation(async (_id: string, body: { title?: string; content?: string }) => ({
      ...INITIAL_FILE,
      title: body.title ?? INITIAL_FILE.title,
      content: body.content ?? INITIAL_FILE.content,
      updated_at: '2026-11-26T08:05:00.000001Z',
    }));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function mountEditorAndStartEditStream(controlled: ControlledStream) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/api/v1/agent/stream')) {
          return {
            ok: true,
            status: 200,
            body: controlled.stream,
            headers: { get: () => null },
          };
        }
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );

    render(
      <ProjectProvider>
        <IntegratedWorkspace />
      </ProjectProvider>,
    );
    await flushMicrotasks();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    await flushMicrotasks();

    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
    expect(textarea.value).toBe(INITIAL_FILE.content);

    // Start real SSE stream and emit file_edit_start (WITHOUT file_edit_end or done)
    await act(async () => {
      handle.current!.startStream('改写第一章');
    });
    await flushMicrotasks();

    await act(async () => {
      controlled.enqueue(
        `event: file_edit_start\ndata: ${JSON.stringify({
          file_id: FILE_ID,
          title: INITIAL_FILE.title,
          total_edits: 2,
          file_type: 'draft',
        })}\n\n`,
      );
    });
    await flushMicrotasks();

    expect(handle.current?.isStreaming).toBe(true);
    expect(handle.current?.aiEditingFileId).toBe(FILE_ID);

    // Confirm autosave is genuinely suspended while isAiEditing is active
    fireEvent.change(textarea, { target: { value: '作者在 AI 编辑期间输入的内容' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    await flushMicrotasks();
    expect(server.updateFile).not.toHaveBeenCalled();

    return textarea;
  }

  it.each([
    {
      label: 'unexpected EOF without done/file_edit_end',
      disconnect: (stream: ControlledStream) => stream.close(),
    },
    {
      label: 'mid-stream network transport error',
      disconnect: (stream: ControlledStream) => stream.error(new TypeError('network reset')),
    },
  ])('releases isAiEditing and unlocks autosave on $label', async ({ disconnect }) => {
    const controlled = createControlledSSEStream();
    const textarea = await mountEditorAndStartEditStream(controlled);

    // Drop the SSE connection mid-edit without ever sending file_edit_end or done
    await act(async () => {
      disconnect(controlled);
    });
    await flushMicrotasks();

    // Stream lock must be released
    expect(handle.current?.isStreaming).toBe(false);
    expect(handle.current?.aiEditingFileId).toBeNull();

    // Editor autosave must now succeed cleanly (lock is NOT permanently stuck)
    fireEvent.change(textarea, { target: { value: '断连恢复后作者继续写下的新正文。' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3500);
    });
    await flushMicrotasks();

    expect(server.updateFile).toHaveBeenCalledTimes(1);
    expect(server.updateFile).toHaveBeenCalledWith(
      FILE_ID,
      expect.objectContaining({ content: '断连恢复后作者继续写下的新正文。' }),
    );
  });

  it('releases isAiEditing and clears streaming lock when switching projects mid-stream', async () => {
    const controlled = createControlledSSEStream();
    await mountEditorAndStartEditStream(controlled);

    // Switch project while project-1's stream is still open mid-edit
    await act(async () => {
      handle.current!.setCurrentProjectId('project-2');
    });
    await flushMicrotasks();

    expect(handle.current?.isStreaming).toBe(false);
    expect(handle.current?.aiEditingFileId).toBeNull();
    expect(handle.current?.streamingFileId).toBeNull();

    // Late SSE frame from old stream must not re-lock the new project
    await act(async () => {
      controlled.enqueue(
        `event: file_edit_start\ndata: ${JSON.stringify({
          file_id: 'stale-file',
          title: 'Stale',
          total_edits: 1,
          file_type: 'draft',
        })}\n\n`,
      );
      controlled.close();
    });
    await flushMicrotasks();

    expect(handle.current?.aiEditingFileId).toBeNull();
    expect(handle.current?.streamingFileId).toBeNull();
  });
});
