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
  getTree: vi.fn(),
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
    getTree: server.getTree,
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
import { BEFORE_CHUNK_RELOAD_EVENT, createEditorDraftSnapshot, writeEditorDraftSnapshot, readEditorDraftSnapshot } from '../../lib/editorDraftRecovery';
import { getLastOpenedFile, setLastOpenedFile } from '../../lib/lastOpenedFile';
import { notifyFileRemoved } from '../../lib/fileRemovalEvents';
import { Editor } from '../Editor';

const CH1: ServerFile = { id: 'ch-1', project_id: 'project-1', file_type: 'draft', title: '第1章', content: '第一章正文。', updated_at: '2026-10-09T12:00:00.000001' };
const CH2_ID = 'ch-2';
const V1 = '中午十二点，老周还在摊上。\n\n他看了一眼手表。';
const V2 = '第二天中午十二点，老周还在摊上。\n\n他看了一眼手表。';
const T_CREATED = '2026-10-09T12:38:40.000001';
const T_V1 = '2026-10-09T12:38:46.000001';
const T_V2 = '2026-10-09T12:43:06.000001';

type Callbacks = Required<UseChatStreamingOptions>;
const harness: {
  callbacks: Callbacks | null;
  project: ReturnType<typeof useProject> | null;
  setShowEditor: ((show: boolean) => void) | null;
} = { callbacks: null, project: null, setShowEditor: null };

/** Wires the real stream callbacks to the real ProjectContext, like ChatPanel does. */
function Harness({ initialSelection = true }: { initialSelection?: boolean }) {
  const selectedInitially = React.useRef(false);
  const initializedProject = React.useRef(false);
  const project = useProject();
  // The project state outlives the editor, as in the app (leaving the project page).
  const [showEditor, setShowEditor] = React.useState(true);
  React.useLayoutEffect(() => {
    harness.setShowEditor = setShowEditor;
  }, []);
  const { getStreamCallbacks } = useChatStreaming();
  const selectedRef = React.useRef(project.selectedItem);
  const streamingRef = React.useRef(project.streamingFileId);
  const { setCurrentProjectId, setSelectedItem, currentProjectId, selectedItem } = project;
  React.useEffect(() => {
    if (initializedProject.current) return;
    initializedProject.current = true;
    if (!currentProjectId) setCurrentProjectId('project-1');
  }, [currentProjectId, setCurrentProjectId]);
  React.useEffect(() => {
    if (initialSelection && !selectedInitially.current && currentProjectId === 'project-1' && !selectedItem) {
      selectedInitially.current = true;
      setSelectedItem({ id: CH1.id, type: 'draft', title: CH1.title });
    }
  }, [currentProjectId, selectedItem, setSelectedItem, initialSelection]);
  React.useLayoutEffect(() => {
    selectedRef.current = project.selectedItem;
    streamingRef.current = project.streamingFileId;
    harness.project = project;
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
  return showEditor ? <Editor /> : null;
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
async function runRoundUntilV1({ failReloadAfterStream = false } = {}) {
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
  if (failReloadAfterStream) server.get.mockRejectedValueOnce(new Error('network'));
  act(() => callbacks().onFileContentEnd(CH2_ID));
  await advance(300); await settle();
  if (!failReloadAfterStream) expect(textarea().value).toBe(V1);
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
  server.getTree.mockReset();
  server.getTree.mockImplementation(async () => ({ tree: [...server.files.values()].map(file => ({...file, children: []})) }));
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
  harness.project = null;
  harness.setShowEditor = null;
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

    // Comparison view: the author's text is the baseline, the AI's change the proposal.
    expect(screen.queryByPlaceholderText('editor:placeholder.contentPlaceholder')).toBeNull();
    expect(harness.project!.diffReviewState).toMatchObject({
      originalContent: `${V1}作者补的一句。`,
      modifiedContent: `${V2}作者补的一句。`,
    });
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
    // Saved as the author's edit, so the server's author-edit guard keeps protecting it.
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ base_updated_at: T_V2, change_type: 'edit', change_source: 'user' });
    expect(server.files.get(CH2_ID)!.content).toBe(`${V2}作者补的一句。`);
    expect(server.files.get(CH2_ID)!.content.startsWith('第二天')).toBe(true);
    expect(textarea().value).toBe(server.files.get(CH2_ID)!.content);
    // The editor is clean afterwards: nothing else is sent.
    await advance(5000); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
  });

  it("where the author and the AI rewrote the same words, finishing keeps the author's side", async () => {
    await runRoundUntilV1();
    // The author rewrites the first sentence that v2 also changes.
    const authorText = V1.replace('中午十二点，老周还在摊上。', '傍晚，老周收摊了。');
    fireEvent.change(textarea(), { target: { value: authorText } });
    await secondWrite('parallel_execute');
    expect(screen.getAllByText(/傍晚，老周收摊了/).length).toBeGreaterThan(0);
    // The listed change is the AI's rewrite, starting rejected (the author's words).
    expect(harness.project!.diffReviewState).toMatchObject({ originalContent: authorText, modifiedContent: V2 });
    expect(harness.project!.diffReviewState!.pendingEdits.map((edit) => edit.status)).toEqual(['rejected']);

    finishReview();
    await advance(100); await settle();
    expect(server.files.get(CH2_ID)!.content).toBe(authorText);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ change_type: 'edit', change_source: 'user', base_updated_at: T_V2 });
  });

  it("'reject all' in the comparison gives back exactly the author's text, never less", async () => {
    await runRoundUntilV1();
    const authorText = `${V1.replace('中午十二点，老周还在摊上。', '傍晚，老周收摊了。')}作者补的一句。`;
    fireEvent.change(textarea(), { target: { value: authorText } });
    await secondWrite('parallel_execute');

    fireEvent.click(screen.getByTitle('editor:rejectAll'));
    await settle();
    finishReview();
    await advance(100); await settle();
    expect(server.files.get(CH2_ID)!.content).toBe(authorText);
    expect(textarea().value).toBe(authorText);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ change_type: 'edit', change_source: 'user' });
  });

  it("accepting the AI's rewrite takes the AI's words there and keeps the author's other edits", async () => {
    await runRoundUntilV1();
    const authorText = `${V1.replace('中午十二点，老周还在摊上。', '傍晚，老周收摊了。')}作者补的一句。`;
    fireEvent.change(textarea(), { target: { value: authorText } });
    await secondWrite('parallel_execute');

    fireEvent.click(screen.getByTitle('editor:acceptAll'));
    await settle();
    finishReview();
    await advance(100); await settle();
    expect(server.files.get(CH2_ID)!.content).toBe(`${V2}作者补的一句。`);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ change_type: 'edit', change_source: 'user' });
  });

  it('an AI <file> stream into the open chapter does not discard text the author had not saved yet', async () => {
    await runRoundUntilV1();
    // The author types (not saved yet: under the 3 s autosave), then the AI
    // rewrites the same chapter through a <file> stream.
    const authorText = `${V1}作者补的一句。`;
    fireEvent.change(textarea(), { target: { value: authorText } });
    act(() => {
      callbacks().onToolResult('create_file', 'success', { id: CH2_ID });
      callbacks().onFileCreated(CH2_ID, 'draft', '第2章');
    });
    await settle();
    act(() => callbacks().onFileContent(CH2_ID, V2));
    await advance(50);
    server.files.set(CH2_ID, { ...server.files.get(CH2_ID)!, content: V2, updated_at: T_V2 });
    act(() => callbacks().onFileContentEnd(CH2_ID));
    await advance(300); await settle();

    // Not replaced by the streamed copy: the comparison opens instead.
    expect(toastError).toHaveBeenCalledWith('editor:aiEditedWhileDirty');
    expect(harness.project!.diffReviewState).toMatchObject({ originalContent: authorText, modifiedContent: `${V2}作者补的一句。` });
    await advance(5000); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(0);
    finishReview();
    await advance(100); await settle();
    expect(server.files.get(CH2_ID)!.content).toBe(`${V2}作者补的一句。`);
  });

  it('keeps the unsaved text when the AI only renamed the chapter, and saves it on the new token', async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${V1}作者补的一句。` } });
    server.files.set(CH2_ID, { ...server.files.get(CH2_ID)!, title: '第2章 新名', updated_at: T_V2 });
    act(() => { callbacks().onToolResult('parallel_execute', 'success', { total_tasks: 1, completed: 1, tasks: [] }); });
    await advance(300); await settle();

    // Nothing from the AI to review: no comparison, the author's text stays.
    expect(harness.project!.diffReviewState).toBeNull();
    expect(textarea().value).toBe(`${V1}作者补的一句。`);
    expect(titleInput().value).toBe('第2章 新名');
    await advance(3100); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ content: `${V1}作者补的一句。`, base_updated_at: T_V2 });
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

  it('leaving while the comparison is open keeps the merged draft locally, sends no stale save, and restores it on return', async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${V1}作者补的一句。` } });
    await secondWrite('parallel_execute');
    expect(toastError).toHaveBeenCalledWith('editor:aiEditedWhileDirty');

    // The author leaves the project page; the project state stays mounted.
    act(() => harness.setShowEditor!(false));
    await settle();
    expect(putsFor(CH2_ID)).toHaveLength(0);
    // Kept locally: v2 plus only the author's sentence, on v2's token, so
    // restoring it can never undo the AI's write.
    const snapshot = readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH2_ID });
    expect(snapshot).toMatchObject({ content: `${V2}作者补的一句。`, baseUpdatedAt: T_V2 });
    // The comparison does not linger in the project state to reopen with an older token.
    expect(harness.project!.diffReviewState).toBeNull();

    // Coming back to the chapter offers the author's text again, on top of v2.
    act(() => harness.setShowEditor!(true));
    await settle(); await advance(50); await settle();
    expect(textarea().value).toBe(`${V2}作者补的一句。`);
    expect(screen.getByText('editor:draftRecovery.restored')).toBeTruthy();
    fireEvent.change(textarea(), { target: { value: `${V2}作者补的一句。好` } });
    await advance(3100); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ base_updated_at: T_V2 });
    expect(server.files.get(CH2_ID)!.content).toBe(`${V2}作者补的一句。好`);
  });

  it('closing the page while the comparison is open keeps the merged draft, not the draft on the older copy', async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${V1}作者补的一句。` } });
    await secondWrite('parallel_execute');

    act(() => { window.dispatchEvent(new Event('pagehide')); });
    const snapshot = readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH2_ID });
    expect(snapshot).toMatchObject({ content: `${V2}作者补的一句。`, baseUpdatedAt: T_V2 });
  });

  // The author and the AI rewrote the same sentence; the comparison starts on the author's side there.
  const AUTHOR_REWRITE = V1.replace('中午十二点，老周还在摊上。', '傍晚，老周收摊了。');
  const draftFor = () => readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH2_ID });

  it("leaving before deciding where both rewrote the same words keeps the author's rewrite and asks on return", async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: AUTHOR_REWRITE } });
    await secondWrite('parallel_execute');
    expect(toastError).toHaveBeenCalledWith('editor:aiEditedWhileDirty');

    act(() => harness.setShowEditor!(false));
    await settle();
    expect(putsFor(CH2_ID)).toHaveLength(0);
    // Leaving is not a choice: the author's side is kept, with no token, so
    // reopening asks instead of silently keeping either side.
    const snapshot = draftFor();
    expect(snapshot?.content).toBe(AUTHOR_REWRITE);
    expect(snapshot?.baseUpdatedAt).toBeUndefined();

    act(() => harness.setShowEditor!(true));
    await settle(); await advance(50); await settle();
    expect(textarea().value).toBe(V2);
    expect(screen.getByText('editor:draftRecovery.conflict')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'editor:draftRecovery.restore' }));
    await settle();
    expect(textarea().value).toBe(AUTHOR_REWRITE);
    fireEvent.change(textarea(), { target: { value: `${AUTHOR_REWRITE}好` } });
    await advance(3100); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ content: `${AUTHOR_REWRITE}好`, base_updated_at: T_V2 });
  });

  it("closing the page before deciding where both rewrote the same words keeps the author's rewrite", async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${AUTHOR_REWRITE}作者补的一句。` } });
    await secondWrite('parallel_execute');

    act(() => { window.dispatchEvent(new Event('pagehide')); });
    const snapshot = draftFor();
    expect(snapshot?.content).toBe(`${AUTHOR_REWRITE}作者补的一句。`);
    expect(snapshot?.baseUpdatedAt).toBeUndefined();
  });

  it('a choice the author made in the comparison is what leaving keeps', async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${AUTHOR_REWRITE}作者补的一句。` } });
    await secondWrite('parallel_execute');
    // The author looks at the rewritten sentence and keeps the AI's version.
    fireEvent.click(screen.getAllByTitle('editor:rejectChange')[0]);
    fireEvent.click(screen.getAllByTitle('editor:acceptChange')[0]);
    await settle();

    act(() => harness.setShowEditor!(false));
    await settle();
    expect(draftFor()).toMatchObject({ content: `${V2}作者补的一句。`, baseUpdatedAt: T_V2 });

    act(() => harness.setShowEditor!(true));
    await settle(); await advance(50); await settle();
    expect(textarea().value).toBe(`${V2}作者补的一句。`);
    expect(screen.getByText('editor:draftRecovery.restored')).toBeTruthy();
  });

  it("pressing finish where both rewrote the same words keeps the author's side and leaves no draft behind", async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${AUTHOR_REWRITE}作者补的一句。` } });
    await secondWrite('parallel_execute');
    finishReview();
    await advance(100); await settle();
    expect(server.files.get(CH2_ID)!.content).toBe(`${AUTHOR_REWRITE}作者补的一句。`);
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ base_updated_at: T_V2 });
    expect(textarea().value).toBe(`${AUTHOR_REWRITE}作者补的一句。`);

    act(() => harness.setShowEditor!(false));
    await settle();
    expect(draftFor()).toBeNull();
    expect(putsFor(CH2_ID)).toHaveLength(1);
  });

  it("does not count the AI's streamed chapter as the author's words", async () => {
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: `${V1}好` } });
    await advance(3100); await settle();
    expect(putsFor(CH2_ID)).toHaveLength(1);
    // One typed character: no "author edit" version for the AI's text either.
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ content: `${V1}好`, base_updated_at: T_V1, skip_version: true });
    expect(recordStats).toHaveBeenCalledTimes(1);
    expect(recordStats.mock.calls[0]).toEqual(['project-1', expect.objectContaining({ words_added: 1, words_deleted: 0 })]);
  });

  it("keeps the editor and offers to load again when the reload after the AI's streamed chapter fails", async () => {
    await runRoundUntilV1({ failReloadAfterStream: true });
    // Not the full-page "failed to load" screen: the editor stays, with a notice.
    expect(screen.queryByText('editor:placeholder.loadFailed')).toBeNull();
    expect(textarea()).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('editor:serverSyncFailed.message');

    fireEvent.click(screen.getByRole('button', { name: 'editor:serverSyncFailed.retry' }));
    await settle();
    expect(textarea().value).toBe(V1);
    expect(screen.queryByText('editor:serverSyncFailed.message')).toBeNull();

    // The loaded copy is the clean baseline: the next edit saves on v1's token.
    fireEvent.change(textarea(), { target: { value: `${V1}好` } });
    await advance(3100); await settle();
    expect(putsFor(CH2_ID)[0][1]).toMatchObject({ content: `${V1}好`, base_updated_at: T_V1 });
  });

  it('says so when the latest copy cannot be loaded, and loads it on request', async () => {
    await runRoundUntilV1();
    server.get.mockRejectedValueOnce(new Error('network'));
    server.get.mockRejectedValueOnce(new Error('network'));
    server.get.mockRejectedValueOnce(new Error('network'));
    await secondWrite('parallel_execute');
    await advance(1100); await settle();
    await advance(3100); await settle();
    // Still on v1, but the author is told and can load again.
    expect(textarea().value).toBe(V1);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('editor:serverSyncFailed.message');

    fireEvent.click(screen.getByRole('button', { name: 'editor:serverSyncFailed.retry' }));
    await settle();
    expect(textarea().value).toBe(V2);
    expect(screen.queryByText('editor:serverSyncFailed.message')).toBeNull();
  });
});

describe('deleted open file terminal state', () => {
  const mountOpenFile = async () => {
    const view = render(<ProjectProvider><Harness /></ProjectProvider>);
    await settle(); await advance(50); await settle();
    expect(textarea()).toHaveValue(CH1.content);
    return view;
  };

  it('validates a legacy boolean delete result against the tree and never refreshes the removed file', async () => {
    await mountOpenFile();
    setLastOpenedFile('user-1', 'project-1', CH1.id);
    const getsBefore = server.get.mock.calls.length;
    server.files.delete(CH1.id);
    act(() => callbacks().onToolResult('delete_file', 'success', true as unknown as Record<string, unknown>));
    await settle();
    await act(async () => { await callbacks().onComplete([], null); });
    await advance(5000); await settle();
    expect(harness.project?.selectedItem).toBeNull();
    expect(server.get).toHaveBeenCalledTimes(getsBefore);
    expect(getLastOpenedFile('user-1', 'project-1')).toBeNull();
    expect(screen.queryByText('editor:serverSyncFailed.message')).not.toBeInTheDocument();
  });

  it('refreshes the still-open file when a boolean delete removed a different file', async () => {
    await mountOpenFile();
    server.files.set(CH1.id, { ...CH1, content: 'Updated surviving file' });
    act(() => callbacks().onToolResult('delete_file', 'success', true as unknown as Record<string, unknown>));
    await settle();
    await act(async () => { await callbacks().onComplete([], null); });
    await advance(5000); await settle();
    expect(harness.project?.selectedItem?.id).toBe(CH1.id);
    expect(textarea()).toHaveValue('Updated surviving file');
  });

  it('uses a terminal file read after deletion-tree verification fails', async () => {
    await mountOpenFile();
    server.getTree.mockRejectedValueOnce(new Error('tree offline'));
    server.get.mockRejectedValueOnce(new ApiError(404, 'gone', 'ERR_FILE_NOT_FOUND'));
    act(() => callbacks().onToolResult('delete_file', 'success', true as unknown as Record<string, unknown>));
    await settle();
    await act(async () => { await callbacks().onComplete([], null); });
    await advance(5000); await settle();
    expect(harness.project?.selectedItem).toBeNull();
    expect(server.get).toHaveBeenCalledTimes(2);
  });

  it('preserves dirty text when saving first discovers the file was deleted', async () => {
    await mountOpenFile();
    fireEvent.change(textarea(), { target: { value: 'Save encountered deletion' } });
    server.update.mockRejectedValueOnce(new ApiError(404, 'gone', 'ERR_FILE_NOT_FOUND'));
    fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true });
    await settle(); await advance(5000);
    expect(harness.project?.selectedItem).toBeNull();
    expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue('Save encountered deletion');
    expect(server.update).toHaveBeenCalledTimes(1);
    expect(recordStats).not.toHaveBeenCalled();
  });

  it('treats refresh 404 as terminal after one request', async () => {
    await mountOpenFile();
    const getsBefore = server.get.mock.calls.length;
    server.get.mockRejectedValue(new ApiError(404, 'gone', 'ERR_FILE_NOT_FOUND'));
    act(() => harness.project?.triggerEditorRefresh(CH1.id));
    await advance(5000); await settle();
    expect(server.get).toHaveBeenCalledTimes(getsBefore + 1);
    expect(harness.project?.selectedItem).toBeNull();
    expect(screen.queryByText('editor:serverSyncFailed.message')).not.toBeInTheDocument();
  });

  it('retains separate recovery entries when two open files are deleted in sequence', async () => {
    await mountOpenFile();
    fireEvent.change(textarea(), { target: { value: 'First removed local body' } });
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
    await settle();
    server.files.set(CH2_ID, { ...CH1, id: CH2_ID, title: 'Second chapter', content: 'Second saved body' });
    act(() => harness.project?.setSelectedItem({ id: CH2_ID, type: 'draft', title: 'Second chapter' }));
    await settle();
    fireEvent.change(textarea(), { target: { value: 'Second removed local body' } });
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH2_ID }));
    await settle();
    expect(screen.getAllByLabelText('editor:deletedFile.draft').map(node => (node as HTMLTextAreaElement).value)).toEqual(['First removed local body', 'Second removed local body']);
    expect(readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH1.id })?.content).toBe('First removed local body');
    expect(readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH2_ID })?.content).toBe('Second removed local body');
    expect(server.update).not.toHaveBeenCalled();
  });

  it('keeps a deleted dirty draft readable after remount without saving into the deleted file', async () => {
    const view = await mountOpenFile();
    const draft = '作者刚输入、还没有保存的正文';
    fireEvent.change(textarea(), { target: { value: draft } });
    server.files.delete(CH1.id);
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
    await settle(); await advance(5000);
    expect(harness.project?.selectedItem).toBeNull();
    expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue(draft);
    expect(readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH1.id })?.content).toBe(draft);
    expect(server.update).not.toHaveBeenCalled();
    view.unmount(); await settle();
    render(<ProjectProvider><Harness initialSelection={false} /></ProjectProvider>);
    await settle();
    expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue(draft);
    expect(server.update).not.toHaveBeenCalled();
  });

  it.each(['project-2', null])('keeps a memory-only deleted draft through premature context %s and back', async prematureProject => {
    await mountOpenFile();
    fireEvent.change(textarea(), { target: { value: 'Only in memory in project A' } });
    const originalWrite = vi.mocked(localStorage.setItem).getMockImplementation()!;
    const write = vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => {
      if (key.startsWith('zenstory:editor-draft-recovery:')) throw new DOMException('full', 'QuotaExceededError');
      originalWrite(key, value);
    });
    try {
      act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id })); await settle();
      expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue('Only in memory in project A');
      act(() => harness.project?.setCurrentProjectId(prematureProject)); await settle();
      expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue('Only in memory in project A');
      const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(true);
      const reload = new Event(BEFORE_CHUNK_RELOAD_EVENT, { cancelable: true }); window.dispatchEvent(reload);
      expect(reload.defaultPrevented).toBe(true);
      act(() => harness.project?.setCurrentProjectId('project-1')); await settle();
      expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue('Only in memory in project A');
      expect(screen.getByText('editor:deletedFile.storageFailed')).toBeInTheDocument();
      expect(localStorage.getItem('zenstory:editor-draft-recovery:v1:user-1:project-1:ch-1')).toBeNull();
      const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:memory-draft');
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
      try {
        fireEvent.click(screen.getByRole('button', { name: 'editor:deletedFile.download' }));
        expect(await (createUrl.mock.calls[0][0] as Blob).text()).toBe(CH1.title + '\n\nOnly in memory in project A');
      } finally { createUrl.mockRestore(); click.mockRestore(); }
    } finally { write.mockRestore(); }
  });

  it('prefers the newest memory-only deleted draft over its stale stored copy when returning to the project', async () => {
    await mountOpenFile();
    const scope = { userId: 'user-1', projectId: 'project-1', fileId: CH1.id };
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      ...scope, title: CH1.title, content: 'Stale stored copy', baseUpdatedAt: 'older-token', reason: 'file-deleted',
    }));
    fireEvent.change(textarea(), { target: { value: 'Newest copy retained only in memory' } });
    const originalWrite = vi.mocked(localStorage.setItem).getMockImplementation()!;
    const write = vi.spyOn(localStorage, 'setItem').mockImplementation((key, value) => {
      if (key.startsWith('zenstory:editor-draft-recovery:')) throw new DOMException('full', 'QuotaExceededError');
      originalWrite(key, value);
    });
    try {
      act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
      await settle();
    } finally { write.mockRestore(); }
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      userId: 'user-1', projectId: 'project-1', fileId: CH2_ID,
      title: 'Another deleted file', content: 'A separate durable copy', baseUpdatedAt: 'second-token', reason: 'file-deleted',
    }));
    act(() => harness.project?.setCurrentProjectId('project-2')); await settle();
    expect(screen.getAllByLabelText('editor:deletedFile.draft').map(node => (node as HTMLTextAreaElement).value))
      .toEqual(['Newest copy retained only in memory']);
    act(() => harness.project?.setCurrentProjectId('project-1')); await settle();
    expect(screen.getAllByLabelText('editor:deletedFile.draft').map(node => (node as HTMLTextAreaElement).value))
      .toEqual(['Newest copy retained only in memory', 'A separate durable copy']);
    expect(screen.getAllByText('editor:deletedFile.storageFailed')).toHaveLength(1);
    expect(screen.getAllByText('editor:deletedFile.saved')).toHaveLength(1);
    expect(readEditorDraftSnapshot(localStorage, scope)?.content).toBe('Stale stored copy');
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    expect(server.update).not.toHaveBeenCalled();
  });

  it('retains the visible draft and reports storage failure without claiming it was saved', async () => {
    await mountOpenFile();
    fireEvent.change(textarea(), { target: { value: '内存中的未保存正文' } });
    const write = vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError'); });
    try {
      server.files.delete(CH1.id);
      act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
      await settle(); await advance(5000);
      expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue('内存中的未保存正文');
      expect(screen.getByText('editor:deletedFile.storageFailed')).toBeInTheDocument();
      expect(screen.queryByText('editor:deletedFile.saved')).not.toBeInTheDocument();
      const unload = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(unload);
      expect(unload.defaultPrevented).toBe(true);
      const chunkReload = new Event(BEFORE_CHUNK_RELOAD_EVENT, { cancelable: true });
      window.dispatchEvent(chunkReload);
      expect(chunkReload.defaultPrevented).toBe(true);

      expect(server.update).not.toHaveBeenCalled();
    } finally { write.mockRestore(); }
  });
  it('ignores deletion events for another project or another file while preserving the active draft', async () => {
    await mountOpenFile();
    fireEvent.change(textarea(), { target: { value: 'Keep this project draft' } });
    act(() => {
      notifyFileRemoved('project-2', CH1.id);
      notifyFileRemoved('project-1', CH2_ID);
    });
    await settle();
    expect(harness.project?.selectedItem?.id).toBe(CH1.id);
    expect(textarea()).toHaveValue('Keep this project draft');
    expect(screen.queryByLabelText('editor:deletedFile.draft')).not.toBeInTheDocument();
    expect(server.get).toHaveBeenCalledTimes(1);
    expect(server.update).not.toHaveBeenCalled();
  });

  it.each(['success', '404'] as const)('ignores an obsolete GET %s after deletion and selection of another file', async outcome => {
    await mountOpenFile();
    let resolveOld!: (file: ServerFile) => void;
    let rejectOld!: (error: Error) => void;
    server.get.mockImplementationOnce(() => new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }));
    act(() => harness.project?.triggerEditorRefresh(CH1.id));
    await advance(100);
    const second = { ...CH1, id: CH2_ID, title: '第2章', content: '另一个文件的正文' };
    server.files.set(CH2_ID, second);
    act(() => {
      callbacks().onToolResult('delete_file', 'success', { id: CH1.id });
      harness.project?.setSelectedItem({ id: CH2_ID, type: 'draft', title: second.title });
    });
    await settle();
    await act(async () => {
      if (outcome === 'success') resolveOld({ ...CH1, content: '删除前的过期响应' });
      else rejectOld(new ApiError(404, 'gone', 'ERR_FILE_NOT_FOUND'));
    });
    await settle();
    expect(harness.project?.selectedItem?.id).toBe(CH2_ID);
    expect(textarea()).toHaveValue(second.content);
  });

  it('downloads the latest editing copy rather than an older page-exit snapshot', async () => {
    await mountOpenFile();
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      userId: 'user-1', projectId: 'project-1', fileId: CH1.id,
      title: CH1.title, content: 'Earlier page exit copy', baseUpdatedAt: 'older-token', reason: 'page-exit',
    }));
    fireEvent.change(textarea(), { target: { value: 'Latest unsaved copy' } });
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
    await settle();
    const snapshot = readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH1.id });
    expect(snapshot?.content).toBe('Latest unsaved copy');
    expect(snapshot?.baseUpdatedAt).toBe(CH1.updated_at);
    const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:deleted-draft');
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    fireEvent.click(screen.getByRole('button', { name: 'editor:deletedFile.download' }));
    expect(await (createUrl.mock.calls[0][0] as Blob).text()).toBe(CH1.title + '\n\nLatest unsaved copy');
    await advance(1);
    expect(revoke).toHaveBeenCalledWith('blob:deleted-draft');
    click.mockRestore(); createUrl.mockRestore(); revoke.mockRestore();
  });

  it('keeps the original token of an unanswered recovery when no new edits exist', async () => {
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      userId: 'user-1', projectId: 'project-1', fileId: CH1.id,
      title: 'Unanswered recovery', content: 'Previous conflict draft', baseUpdatedAt: 'conflict-token', reason: 'page-exit',
    }));
    await mountOpenFile();
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
    await settle();
    const snapshot = readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH1.id });
    expect(snapshot?.content).toBe('Previous conflict draft');
    expect(snapshot?.baseUpdatedAt).toBe('conflict-token');
    expect(screen.getAllByLabelText('editor:deletedFile.draft')).toHaveLength(1);
  });

  it('retains both an unanswered recovery conflict and newer edits after deletion and remount', async () => {
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      userId: 'user-1', projectId: 'project-1', fileId: CH1.id,
      title: CH1.title, content: 'Previous conflict draft', baseUpdatedAt: 'conflict-token', reason: 'page-exit',
    }));
    await mountOpenFile();
    expect(screen.getByText('editor:draftRecovery.conflict')).toBeTruthy();
    fireEvent.change(textarea(), { target: { value: 'Fresh server-based edit' } });
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
    await settle();
    expect(screen.getAllByLabelText('editor:deletedFile.draft').map(node => (node as HTMLTextAreaElement).value)).toEqual(['Fresh server-based edit', 'Previous conflict draft']);
    const snapshot = readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH1.id });
    expect(snapshot?.baseUpdatedAt).toBe(CH1.updated_at);
    expect(snapshot?.recoveryDraft?.baseUpdatedAt).toBe('conflict-token');
    act(() => harness.setShowEditor?.(false)); await settle();
    act(() => harness.setShowEditor?.(true)); await settle();
    expect(screen.getAllByLabelText('editor:deletedFile.draft').map(node => (node as HTMLTextAreaElement).value)).toEqual(['Fresh server-based edit', 'Previous conflict draft']);
  });

  it.each(['success', '404', '500'] as const)('keeps the latest dirty recovery copy when an earlier PUT resolves with %s after deletion', async outcome => {
    await mountOpenFile();
    let resolveSave!: (file: ServerFile) => void;
    let rejectSave!: (error: Error) => void;
    server.update.mockImplementationOnce(() => new Promise((resolve, reject) => { resolveSave = resolve; rejectSave = reject; }));
    fireEvent.change(textarea(), { target: { value: '先提交的正文' } });
    fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true });
    await settle();
    expect(server.update).toHaveBeenCalledTimes(1);
    fireEvent.change(textarea(), { target: { value: '删除前最后输入的正文' } });
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
    await settle();
    await act(async () => {
      if (outcome === 'success') resolveSave({ ...CH1, content: '先提交的正文' });
      else rejectSave(new ApiError(Number(outcome), 'gone', outcome === '404' ? 'ERR_FILE_NOT_FOUND' : 'ERR_INTERNAL'));
    });
    await settle();
    expect(harness.project?.selectedItem).toBeNull();
    expect(readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH1.id })?.content).toBe('删除前最后输入的正文');
    expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue('删除前最后输入的正文');
    expect(recordStats).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('recognizes a deletion completed inside parallel_execute', async () => {
    await mountOpenFile();
    server.files.delete(CH1.id);
    act(() => callbacks().onToolResult('parallel_execute', 'success', {
      tasks: [{ type: 'delete_file', status: 'completed', result: { status: 'success', data: true } }],
    }));
    await settle(); await advance(5000);
    expect(harness.project?.selectedItem).toBeNull();
    expect(server.get).toHaveBeenCalledTimes(1);
  });

  it('offers a previously saved local draft when the file is already deleted on opening', async () => {
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      userId: 'user-1', projectId: 'project-1', fileId: CH1.id,
      title: '本地标题', content: '离开前保留的正文', baseUpdatedAt: CH1.updated_at, reason: 'page-exit',
    }));
    server.get.mockRejectedValue(new ApiError(404, 'gone', 'ERR_FILE_NOT_FOUND'));
    render(<ProjectProvider><Harness /></ProjectProvider>);
    await settle(); await advance(5000);
    expect(screen.getByLabelText('editor:deletedFile.draft')).toHaveValue('离开前保留的正文');
    expect(harness.project?.selectedItem).toBeNull();
    expect(server.get).toHaveBeenCalledTimes(1);
  });

  it.each(['success', '404'] as const)('retains a saved recovery when deletion arrives before the initial GET resolves with %s', async outcome => {
    const scope = { userId: 'user-1', projectId: 'project-1', fileId: CH1.id };
    const earlierScope = { ...scope, fileId: CH2_ID };
    const earlierSnapshot = createEditorDraftSnapshot({
      ...earlierScope, title: 'Already deleted file', content: 'Earlier removed local body',
      baseUpdatedAt: 'earlier-removed-token', reason: 'file-deleted',
    });
    writeEditorDraftSnapshot(localStorage, earlierSnapshot);
    const snapshot = createEditorDraftSnapshot({
      ...scope, title: 'Pending draft', content: 'Saved before leaving the page',
      baseUpdatedAt: 'original-token', reason: 'page-exit',
      recoveryDraft: { title: 'Unanswered conflict', content: 'Earlier conflict copy', baseUpdatedAt: 'conflict-token', capturedAt: T_V1 },
    });
    writeEditorDraftSnapshot(localStorage, snapshot);
    let resolveRead!: (file: ServerFile) => void;
    let rejectRead!: (error: Error) => void;
    server.get.mockImplementationOnce(() => new Promise((resolve, reject) => { resolveRead = resolve; rejectRead = reject; }));
    render(<ProjectProvider><Harness /></ProjectProvider>);
    await settle();
    expect(server.get).toHaveBeenCalledWith(CH1.id);
    expect(screen.queryByPlaceholderText('editor:placeholder.contentPlaceholder')).not.toBeInTheDocument();
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH1.id }));
    await settle();
    expect(harness.project?.selectedItem).toBeNull();
    expect(readEditorDraftSnapshot(localStorage, scope)).toEqual({ ...snapshot, reason: 'file-deleted' });
    expect(screen.getAllByLabelText('editor:deletedFile.draft').map(node => (node as HTMLTextAreaElement).value))
      .toEqual(['Earlier removed local body', 'Saved before leaving the page', 'Earlier conflict copy']);
    await act(async () => {
      if (outcome === 'success') resolveRead({ ...CH1, content: 'Obsolete response' });
      else rejectRead(new ApiError(404, 'gone', 'ERR_FILE_NOT_FOUND'));
    });
    await advance(5000); await settle();
    expect(harness.project?.selectedItem).toBeNull();
    expect(screen.queryByPlaceholderText('editor:placeholder.contentPlaceholder')).not.toBeInTheDocument();
    expect(readEditorDraftSnapshot(localStorage, scope)).toEqual({ ...snapshot, reason: 'file-deleted' });
    expect(screen.getAllByLabelText('editor:deletedFile.draft')).toHaveLength(3);
    expect(readEditorDraftSnapshot(localStorage, earlierScope)).toEqual(earlierSnapshot);
    expect(server.get).toHaveBeenCalledTimes(1);
    expect(server.update).not.toHaveBeenCalled();
  });

  it('retains an undecided comparison without inventing a newer base token when it is deleted', async () => {
    const AUTHOR_REWRITE = V1.replace('中午十二点，老周还在摊上。', '傍晚，老周收摊了。');
    await runRoundUntilV1();
    fireEvent.change(textarea(), { target: { value: AUTHOR_REWRITE } });
    await secondWrite('parallel_execute');
    act(() => callbacks().onToolResult('delete_file', 'success', { id: CH2_ID }));
    await settle();
    const snapshot = readEditorDraftSnapshot(localStorage, { userId: 'user-1', projectId: 'project-1', fileId: CH2_ID });
    expect(snapshot?.content).toBe(AUTHOR_REWRITE);
    expect(snapshot?.baseUpdatedAt).toBeUndefined();
    expect(snapshot?.reason).toBe('file-deleted');
    expect(putsFor(CH2_ID)).toHaveLength(0);
  });

});

describe('AI fulfills a pending empty file', () => {
  for (const writePath of ['direct edit', 'file stream'] as const) {
    it(`${writePath} keeps ten files without saving the streaming display`, async () => {
      const view = render(<ProjectProvider><Harness initialSelection={false} /></ProjectProvider>);
      await settle();
      act(() => callbacks().onStart());
      const generated = Array.from({ length: 10 }, (_, index) => ({
        ...CH1, id: `generated-${index}`, title: `File ${index + 1}`,
        file_type: index < 3 ? 'lore' : 'character', content: `Saved body ${index + 1}.`.repeat(12),
      }));
      const displayedAfterWrite: string[] = [];
      for (const file of generated) {
        server.files.set(file.id, { ...file, content: '', updated_at: T_CREATED });
        act(() => {
          callbacks().onToolResult('create_file', 'success', { id: file.id, content: '' });
          callbacks().onFileCreated(file.id, file.file_type, file.title);
        });
        await settle();
        if (writePath === 'file stream') {
          act(() => callbacks().onFileContent(file.id, file.content));
          await advance(50);
          server.files.set(file.id, { ...file, updated_at: T_V1 });
          act(() => callbacks().onFileContentEnd(file.id));
        } else {
          // The adapter supports edit_file filling an empty file without ever
          // receiving <file>. It emits no file_content_end for that capture.
          server.files.set(file.id, { ...file, updated_at: T_V1 });
          act(() => {
            callbacks().onToolResult('edit_file', 'success', { id: file.id });
            callbacks().onFileEditStart(file.id, file.title, 1, file.file_type);
            callbacks().onFileEditApplied(file.id, 0, 'append');
            callbacks().onFileEditEnd(file.id, 1, file.content.length, undefined, undefined, file.file_type, file.title);
          });
        }
        await advance(300); await settle();
        displayedAfterWrite.push(textarea().value);
        // Exercise the next file's switch flush before the debounce expires.
        await advance(2200);
      }
      // The last file exercises the debounce without another file switch.
      await advance(3500); await settle();
      await act(async () => { await callbacks().onComplete([], null, { confirmedFileMutation: true }); });
      await advance(3500); await settle();
      for (const file of generated) {
        act(() => harness.project!.setSelectedItem({ id: file.id, type: file.file_type as 'lore' | 'character', title: file.title }));
        await settle();
        expect(textarea()).toHaveValue(file.content);
      }
      view.unmount(); await settle();
      expect(server.update).not.toHaveBeenCalled();
      expect(displayedAfterWrite).toEqual(generated.map(file => file.content));
      for (const file of generated) expect(server.files.get(file.id)!.content).toBe(file.content);
    });
  }
});

it('saves an intentional author deletion of the entire body', async () => {
  await runRoundUntilV1();
  fireEvent.change(textarea(), { target: { value: '' } });
  await advance(3100); await settle();
  expect(putsFor(CH2_ID)).toHaveLength(1);
  expect(putsFor(CH2_ID)[0][1]).toMatchObject({ content: '', base_updated_at: T_V1 });
  expect(server.files.get(CH2_ID)!.content).toBe('');
});

it('keeps the committed body when a direct edit is followed by a stream error', async () => {
  await runRoundUntilV1();
  act(() => callbacks().onFileCreated(CH2_ID, 'draft', '第2章'));
  await settle();
  server.files.set(CH2_ID, { ...server.files.get(CH2_ID)!, content: V2, updated_at: T_V2 });
  act(() => {
    callbacks().onFileEditStart(CH2_ID, '第2章', 1, 'draft');
    callbacks().onFileEditEnd(CH2_ID, 1, V2.length, undefined, undefined, 'draft', '第2章');
    callbacks().onError('Stopped stream');
  });
  await advance(3500); await settle();
  expect(textarea()).toHaveValue(V2);
  expect(putsFor(CH2_ID)).toHaveLength(0);
  expect(server.files.get(CH2_ID)!.content).toBe(V2);
});
