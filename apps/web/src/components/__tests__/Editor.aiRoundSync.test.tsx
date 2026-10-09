/**
 * Integration: one AI round writes the open chapter twice (create_file streams
 * v1, then a later write in the same round lands v2). Uses the real
 * ProjectProvider, the real stream callbacks from useChatStreaming, the real
 * Editor and SimpleEditor; only the network and peripheral contexts are mocked.
 */
import * as React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ServerFile = { id: string; project_id: string; file_type: string; title: string; content: string; updated_at: string };
const server = vi.hoisted(() => ({
  files: new Map<string, ServerFile>(),
  get: vi.fn(),
  update: vi.fn(),
}));
const toastError = vi.hoisted(() => vi.fn());
const recordStats = vi.hoisted(() => vi.fn(async () => ({})));
const auth = vi.hoisted(() => ({ value: { user: { id: 'user-1' } } }));

vi.mock('../../lib/api', () => ({
  projectApi: {
    getAll: vi.fn(async () => [{ id: 'project-1', name: 'Novel', project_type: 'novel', updated_at: '2026-10-09T00:00:00' }]),
    get: vi.fn(),
  },
  fileApi: {
    get: server.get,
    update: server.update,
    getTree: vi.fn(async () => ({ tree: [] })),
    create: vi.fn(),
  },
  fileVersionApi: { getVersions: vi.fn(async () => ({ total: 0, versions: [] })) },
}));
vi.mock('../../lib/toast', () => ({ toast: { error: toastError, success: vi.fn(), info: vi.fn() } }));
vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn(), captureException: vi.fn() }));
vi.mock('../../lib/upgradeAnalytics', () => ({ trackUpgradeExpose: vi.fn(), trackUpgradeClick: vi.fn() }));
vi.mock('../../lib/writingStatsApi', () => ({ writingStatsApi: { recordStats } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => auth.value }));
vi.mock('../../contexts/MaterialLibraryContext', () => ({ useMaterialLibraryContext: () => ({ preview: null }) }));
vi.mock('../../contexts/MaterialAttachmentContext', () => ({ useMaterialAttachment: () => ({ addMaterial: vi.fn() }) }));
vi.mock('../../contexts/TextQuoteContext', () => ({ useTextQuote: () => ({ addQuote: vi.fn() }) }));
vi.mock('../../contexts/MobileLayoutContext', () => ({ useMobileLayout: () => ({ isMobile: false }) }));

import { ProjectProvider, useProject } from '../../contexts/ProjectContext';
import { useChatStreaming } from '../../hooks/useChatStreaming';
import type { UseChatStreamingOptions } from '../../hooks/useChatStreaming';
import { ApiError } from '../../lib/apiClient';
import { readEditorDraftSnapshot } from '../../lib/editorDraftRecovery';
import { Editor } from '../Editor';

const CH1: ServerFile = { id: 'ch-1', project_id: 'project-1', file_type: 'draft', title: '第1章', content: '第一章正文。', updated_at: '2026-10-09T12:00:00.000001' };
const CH2_ID = 'ch-2';
const V1 = '中午十二点，老周还在摊上。\n\n他看了一眼手表。';
const V2 = '第二天中午十二点，老周还在摊上。\n\n他看了一眼手表。';
const T_CREATED = '2026-10-09T12:38:40.000001';
const T_V1 = '2026-10-09T12:38:46.000001';
const T_V2 = '2026-10-09T12:43:06.000001';

type Callbacks = Required<UseChatStreamingOptions>;
const harness: { callbacks: Callbacks | null } = { callbacks: null };

/** Wires the real stream callbacks to the real ProjectContext, like ChatPanel does. */
function Harness() {
  const project = useProject();
  const { getStreamCallbacks } = useChatStreaming();
  const selectedRef = React.useRef(project.selectedItem);
  const streamingRef = React.useRef(project.streamingFileId);
  const { setCurrentProjectId, setSelectedItem, currentProjectId, selectedItem } = project;
  React.useEffect(() => {
    if (!currentProjectId) setCurrentProjectId('project-1');
  }, [currentProjectId, setCurrentProjectId]);
  React.useEffect(() => {
    if (currentProjectId === 'project-1' && !selectedItem) {
      setSelectedItem({ id: CH1.id, type: 'draft', title: CH1.title });
    }
  }, [currentProjectId, selectedItem, setSelectedItem]);
  React.useLayoutEffect(() => {
    selectedRef.current = project.selectedItem;
    streamingRef.current = project.streamingFileId;
    harness.callbacks = getStreamCallbacks({
      triggerFileTreeRefresh: project.triggerFileTreeRefresh,
      triggerEditorRefresh: project.triggerEditorRefresh,
      setAiEditingFileId: project.setAiEditingFileId,
      setSelectedItem: project.setSelectedItem,
      getCurrentSelectedItem: () => selectedRef.current,
      appendFileContent: project.appendFileContent,
      finishFileStreaming: project.finishFileStreaming,
      startFileStreaming: project.startFileStreaming,
      streamingFileId: project.streamingFileId,
      getCurrentStreamingFileId: () => streamingRef.current,
      enterDiffReview: project.enterDiffReview,
      activeProjectId: project.currentProjectId,
      createSnapshot: async () => ({}),
      t: (key: string) => key,
    });
  });
  return <Editor />;
}

const settle = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const textarea = () => screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
const titleInput = () => screen.getByPlaceholderText('editor:placeholder.titlePlaceholder') as HTMLInputElement;
const finishReview = () => fireEvent.click(screen.getByTitle(/editor:(finishReview|applyChanges)/));
const callbacks = () => {
  if (!harness.callbacks) throw new Error('harness not mounted');
  return harness.callbacks;
};
const putsFor = (id: string) => server.update.mock.calls.filter(([fileId]) => fileId === id);

type SecondWrite = 'edit_file' | 'parallel_execute';

/** Opens chapter 1, then runs the AI round up to (not including) the second write. */
async function runRoundUntilV1() {
  render(<ProjectProvider><Harness /></ProjectProvider>);
  await settle(); await advance(50); await settle();
  expect(textarea().value).toBe(CH1.content);

  // create_file: an empty chapter, then <file> content streams in.
  server.files.set(CH2_ID, { ...CH1, id: CH2_ID, title: '第2章', content: '', updated_at: T_CREATED });
  act(() => {
    callbacks().onStart();
    callbacks().onToolResult('create_file', 'success', { id: CH2_ID, content: '' });
    callbacks().onFileCreated(CH2_ID, 'draft', '第2章');
  });
  await settle();
  act(() => callbacks().onFileContent(CH2_ID, V1.slice(0, 6)));
  await advance(50);
  act(() => callbacks().onFileContent(CH2_ID, V1.slice(6)));
  await advance(50);
  // Backend saves v1 before file_content_end.
  server.files.set(CH2_ID, { ...server.files.get(CH2_ID)!, content: V1, updated_at: T_V1 });
  act(() => callbacks().onFileContentEnd(CH2_ID));
  await advance(300); await settle();
  expect(textarea().value).toBe(V1);
}

/** Later in the same round the agent writes the open chapter again (v2). */
async function secondWrite(kind: SecondWrite) {
  server.files.set(CH2_ID, { ...server.files.get(CH2_ID)!, content: V2, updated_at: T_V2 });
  act(() => {
    if (kind === 'edit_file') {
      callbacks().onToolResult('edit_file', 'success', { id: CH2_ID });
      callbacks().onFileEditStart(CH2_ID, '第2章', 1, 'draft');
      callbacks().onFileEditApplied(CH2_ID, 0, 'replace');
      callbacks().onFileEditEnd(CH2_ID, 1, V2.length, undefined, undefined, 'draft', '第2章', {
        failedCount: 0, partialSuccess: false, allFailed: false, warnings: [],
      });
    } else {
      // Edits made inside parallel_execute sub-tasks emit no file_edit_* events.
      callbacks().onToolResult('parallel_execute', 'success', { total_tasks: 2, completed: 2, tasks: [] });
    }
  });
  await advance(300); await settle();
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  toastError.mockReset();
  recordStats.mockClear();
  server.files.clear();
  server.files.set(CH1.id, { ...CH1 });
  server.get.mockReset();
  server.update.mockReset();
  server.get.mockImplementation(async (id: string) => {
    const file = server.files.get(id);
    if (!file) throw new Error(`missing ${id}`);
    return { ...file };
  });
  // Mirrors the server's optimistic concurrency check on base_updated_at.
  server.update.mockImplementation(async (id: string, data: { content?: string; title?: string; base_updated_at?: string }) => {
    const file = server.files.get(id)!;
    if (data.base_updated_at && data.base_updated_at !== file.updated_at) {
      throw new ApiError(409, 'ERR_CONFLICT', {
        reason: 'stale_write', current_content: file.content, current_updated_at: file.updated_at,
      });
    }
    const next = {
      ...file,
      ...(typeof data.content === 'string' ? { content: data.content } : {}),
      ...(typeof data.title === 'string' ? { title: data.title } : {}),
      updated_at: '2026-10-09T12:50:00.000001',
    };
    server.files.set(id, next);
    return { ...next };
  });
  harness.callbacks = null;
});
afterEach(async () => {
  cleanup();
  await settle();
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe('AI writes the open chapter twice in one round', () => {
  for (const kind of ['edit_file', 'parallel_execute'] as const) {
    it(`editor follows the server copy after a second ${kind} write`, async () => {
      await runRoundUntilV1();
      await secondWrite(kind);
      expect(textarea().value).toBe(V2);
    });

    it(`typing after a second ${kind} write saves on top of v2 with the v2 token`, async () => {
      await runRoundUntilV1();
      await secondWrite(kind);
      fireEvent.change(textarea(), { target: { value: `${textarea().value}好` } });
      await advance(3100); await settle();
      expect(putsFor(CH2_ID)).toHaveLength(1);
      expect(putsFor(CH2_ID)[0][1]).toMatchObject({ content: `${V2}好`, base_updated_at: T_V2 });
      expect(server.files.get(CH2_ID)!.content).toBe(`${V2}好`);
      expect(toastError).not.toHaveBeenCalled();
    });
  }

  it('round completion re-reads the open file even when no write event named it', async () => {
    await runRoundUntilV1();
    // A write the stream never announced lands before the round ends.
    server.files.set(CH2_ID, { ...server.files.get(CH2_ID)!, content: V2, updated_at: T_V2 });
    await act(async () => { await callbacks().onComplete([], null, { confirmedFileMutation: true }); });
    await advance(300); await settle();
    expect(textarea().value).toBe(V2);
  });

  it('keeps unsaved author text and opens a comparison instead of replacing it', async () => {
    await runRoundUntilV1();
    // The author edits v1 while the round is still running.
    const authorText = `${V1}作者补的一句。`;
    fireEvent.change(textarea(), { target: { value: authorText } });
    await secondWrite('parallel_execute');

    // Comparison view: the AI's v2 is the baseline, the author's text the proposal.
    expect(screen.queryByPlaceholderText('editor:placeholder.contentPlaceholder')).toBeNull();
    expect(toastError).toHaveBeenCalledWith('editor:aiEditedWhileDirty');
    expect(screen.getAllByText(/作者补的一句/).length).toBeGreaterThan(0);
    // Autosave is held while the author decides; v2 is not overwritten.
    await advance(5000); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(0);
    expect(server.files.get(CH2_ID)!.content).toBe(V2);

    // Finishing without choosing keeps the AI's v2 and adds only the author's sentence.
    finishReview();
    await advance(100); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ base_updated_at: T_V2 });
    expect(server.files.get(CH2_ID)!.content).toBe(`${V2}作者补的一句。`);
    expect(server.files.get(CH2_ID)!.content.startsWith('第二天')).toBe(true);
    expect(textarea().value).toBe(server.files.get(CH2_ID)!.content);
    // The editor is clean afterwards: nothing else is sent.
    await advance(5000); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
  });

  it('where the author and the AI rewrote the same words, finishing keeps the AI side', async () => {
    await runRoundUntilV1();
    // The author rewrites the first sentence that v2 also changes.
    fireEvent.change(textarea(), { target: { value: V1.replace('中午十二点，老周还在摊上。', '傍晚，老周收摊了。') } });
    await secondWrite('parallel_execute');
    expect(screen.getAllByText(/傍晚，老周收摊了/).length).toBeGreaterThan(0);

    finishReview();
    await advance(100); await settle();
    expect(server.files.get(CH2_ID)!.content).toBe(V2);
  });

  it('a typed-then-deleted character does not leave a stale draft that later overwrites v2', async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${V1}x` } });
    fireEvent.change(textarea(), { target: { value: V1 } });
    await secondWrite('parallel_execute');
    expect(textarea().value).toBe(V2);

    await advance(5000); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(0);
    expect(toastError).not.toHaveBeenCalled();
    expect(server.files.get(CH2_ID)!.content).toBe(V2);

    // The next real edit is saved on top of v2 with the v2 token.
    fireEvent.change(textarea(), { target: { value: `${V2}好` } });
    await advance(3100); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ content: `${V2}好`, base_updated_at: T_V2 });
  });

  it('an unsaved title rename keeps the title, follows v2 for the text and saves both', async () => {
    await runRoundUntilV1();
    fireEvent.change(titleInput(), { target: { value: '第2章 新名' } });
    await secondWrite('parallel_execute');

    // No comparison: the body has no unsaved edits.
    expect(textarea().value).toBe(V2);
    expect(titleInput().value).toBe('第2章 新名');
    expect(toastError).not.toHaveBeenCalled();

    await advance(3100); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ title: '第2章 新名', content: V2, base_updated_at: T_V2 });
    expect(server.files.get(CH2_ID)).toMatchObject({ title: '第2章 新名', content: V2 });
  });

  it('saves an unsaved title rename together with the comparison result', async () => {
    await runRoundUntilV1();
    fireEvent.change(titleInput(), { target: { value: '第2章 新名' } });
    fireEvent.change(textarea(), { target: { value: `${V1}作者补的一句。` } });
    await secondWrite('parallel_execute');
    expect(toastError).toHaveBeenCalledWith('editor:aiEditedWhileDirty');

    finishReview();
    await advance(3100); await settle();
    expect(server.files.get(CH2_ID)).toMatchObject({ title: '第2章 新名', content: `${V2}作者补的一句。` });
  });

  it('follows an AI rename when the author has not renamed the chapter', async () => {
    await runRoundUntilV1();
    server.files.set(CH2_ID, { ...server.files.get(CH2_ID)!, title: '第2章 倒着拨的表', content: V2, updated_at: T_V2 });
    act(() => { callbacks().onToolResult('parallel_execute', 'success', { total_tasks: 1, completed: 1, tasks: [] }); });
    await advance(300); await settle();
    expect(titleInput().value).toBe('第2章 倒着拨的表');
    expect(textarea().value).toBe(V2);

    fireEvent.change(textarea(), { target: { value: `${V2}好` } });
    await advance(3100); await settle();
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ title: '第2章 倒着拨的表', base_updated_at: T_V2 });
  });

  it("counts only the author's words after the editor followed the AI's copy", async () => {
    await runRoundUntilV1();
    await secondWrite('parallel_execute');
    fireEvent.change(textarea(), { target: { value: `${V2}好` } });
    await advance(3100); await settle();
    expect(recordStats).toHaveBeenCalledTimes(1);
    expect(recordStats.mock.calls[0]).toEqual(['project-1', expect.objectContaining({ words_added: 1, words_deleted: 0 })]);
  });

  it('retries the re-read when it fails once', async () => {
    await runRoundUntilV1();
    server.get.mockRejectedValueOnce(new Error('network'));
    await secondWrite('parallel_execute');
    expect(textarea().value).toBe(V1);
    await advance(1100); await settle();
    expect(textarea().value).toBe(V2);
  });

  it('leaving while the comparison is open keeps the author draft locally and sends no stale save', async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${V1}作者补的一句。` } });
    await secondWrite('parallel_execute');
    expect(toastError).toHaveBeenCalledWith('editor:aiEditedWhileDirty');

    cleanup();
    await settle();
    expect(putsFor(CH2_ID)).toHaveLength(0);
    const snapshot = readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH2_ID });
    expect(snapshot?.content).toBe(`${V1}作者补的一句。`);
  });
});
