import { StrictMode, createContext, useContext } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Editor } from '../Editor';
import { ApiError } from '../../lib/apiClient';
import type { DiffReviewState, SelectedItem } from '../../types';

const mocks = vi.hoisted(() => ({
  get: vi.fn(), getTree: vi.fn(), create: vi.fn(), update: vi.fn(),
  error: vi.fn(), success: vi.fn(), refresh: vi.fn(), select: vi.fn(),
  enter: vi.fn(), exit: vi.fn(), recordStats: vi.fn(), reject: vi.fn(),
  t: (key: string) => key,
  context: {
    currentProjectId: 'project-a', selectedItem: null as SelectedItem | null,
    streamingFileId: null as string | null, streamingContent: '',
    editorRefreshVersion: 0, lastEditedFileId: null as string | null,
    aiEditingFileId: null as string | null, diffReviewState: null as DiffReviewState | null,
  },
}));
vi.mock('../../lib/api', () => ({
  fileApi: { get: mocks.get, getTree: mocks.getTree, create: mocks.create, update: mocks.update },
  fileVersionApi: { getVersions: vi.fn().mockResolvedValue({ total: 0, versions: [] }) },
}));
vi.mock('../../lib/toast', () => ({ toast: { error: mocks.error, success: mocks.success, info: vi.fn() } }));
vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn(), captureException: vi.fn() }));
vi.mock('../../lib/upgradeAnalytics', () => ({ trackUpgradeExpose: vi.fn(), trackUpgradeClick: vi.fn() }));
vi.mock('../../lib/writingStatsApi', () => ({ writingStatsApi: { recordStats: mocks.recordStats } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: mocks.t }) }));
const ContextRevision = createContext(0);
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => {
  useContext(ContextRevision);
  return ({
  ...mocks.context, setSelectedItem: mocks.select, triggerFileTreeRefresh: mocks.refresh,
  enterDiffReview: mocks.enter, exitDiffReview: mocks.exit,
  // A conflict comparison rejects its conflicts up front (the author's side,
  // the left/original text); every comparison here is one whole-text conflict.
  applyDiffReviewChanges: () => (mocks.reject.mock.calls.length > 0
    ? mocks.context.diffReviewState?.originalContent
    : mocks.context.diffReviewState?.modifiedContent) ?? '',
  acceptEdit: vi.fn(), rejectEdit: mocks.reject, resetEdit: vi.fn(), acceptAllEdits: vi.fn(), rejectAllEdits: vi.fn(),
});
} }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 'user-1' } }) }));
vi.mock('../../contexts/MaterialLibraryContext', () => ({ useMaterialLibraryContext: () => ({ preview: null }) }));
vi.mock('../../contexts/MaterialAttachmentContext', () => ({ useMaterialAttachment: () => ({ addMaterial: vi.fn() }) }));
vi.mock('../../contexts/TextQuoteContext', () => ({ useTextQuote: () => ({ addQuote: vi.fn() }) }));

const fileA = { id: 'file-a', project_id: 'project-a', file_type: 'draft', title: 'Title A', content: 'Original A', updated_at: '2026-10-06T10:00:00.000001' };
const fileB = { ...fileA, id: 'file-b', title: 'Title B', content: 'Original B' };
const token2 = '2026-10-06T10:00:00.000002';
const selected = (file = fileA): SelectedItem => ({ id: file.id, type: file.file_type, title: file.title });
const review = (file = fileA, text = 'Reviewed A'): DiffReviewState => ({
  isReviewing: true, fileId: file.id, originalContent: file.content, modifiedContent: text, pendingEdits: [],
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const textarea = () => screen.getByPlaceholderText('editor:placeholder.contentPlaceholder');
const save = () => fireEvent.click(screen.getByRole('button', { name: 'editor:save' }));
const evidence = (label: string) => console.info('M05 fixture', JSON.stringify({
  label, getIds: mocks.get.mock.calls.map(([id]) => id), put: mocks.update.mock.calls,
  create: mocks.create.mock.calls.length, select: mocks.select.mock.calls, refresh: mocks.refresh.mock.calls.length,
  enter: mocks.enter.mock.calls.length, exit: mocks.exit.mock.calls.length,
  savedLabel: Boolean(screen.queryByText('editor:savedJustNow')), dirtyLabel: Boolean(screen.queryByText('editor:unsaved')),
}));
function mount(strict: boolean) {
  let revision = 0;
  const element = () => {
    const editor = <ContextRevision.Provider value={revision++}><Editor /></ContextRevision.Provider>;
    return strict ? <StrictMode>{editor}</StrictMode> : editor;
  };
  const view = render(element());
  return { ...view, redraw: () => view.rerender(element()) };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  Object.assign(mocks.context, { currentProjectId: 'project-a', selectedItem: selected(), streamingFileId: null,
    streamingContent: '', editorRefreshVersion: 0, lastEditedFileId: null, aiEditingFileId: null, diffReviewState: null });
  mocks.get.mockImplementation(async (id: string) => id === fileB.id ? fileB : fileA);
  mocks.getTree.mockResolvedValue({ tree: [] });
  mocks.create.mockResolvedValue({ ...fileA, id: 'created-a' });
  mocks.update.mockImplementation(async (id: string, data: object) => ({ ...(id === fileB.id ? fileB : fileA), ...data, updated_at: token2 }));
  mocks.select.mockImplementation((item: SelectedItem | null) => { mocks.context.selectedItem = item; });
  mocks.exit.mockImplementation(() => { mocks.context.diffReviewState = null; });
  mocks.enter.mockImplementation((id: string, original: string, modified: string) => {
    mocks.context.diffReviewState = { ...review(), fileId: id, originalContent: original, modifiedContent: modified };
  });
});
afterEach(async () => { cleanup(); await settle(); vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); });

for (const strict of [false, true]) {
  describe(strict ? 'StrictMode' : 'default', () => {
    for (const refresh of ['edit', 'stream'] as const) {
      for (const boundary of ['selection', 'unmount'] as const) {
        it(`C1 cancels ${refresh} timer across ${boundary}`, async () => {
          const view = mount(strict); await settle();
          expect(textarea()).toHaveValue(fileA.content);
          if (refresh === 'stream') {
            mocks.context.streamingFileId = fileA.id; mocks.context.streamingContent = fileA.content;
            view.redraw(); await settle(); mocks.context.streamingFileId = null;
          } else { mocks.context.editorRefreshVersion = 1; mocks.context.lastEditedFileId = fileA.id; }
          view.redraw(); await settle();
          const before = mocks.get.mock.calls.length;
          if (boundary === 'unmount') view.unmount();
          else { mocks.context.selectedItem = selected(fileB); view.redraw(); await settle(); expect(textarea()).toHaveValue(fileB.content); }
          await advance(100);
          evidence(`C1 ${strict} ${refresh} ${boundary}`);
          expect(mocks.get.mock.calls.slice(before).map(([id]) => id)).toEqual(boundary === 'unmount' ? [] : [fileB.id]);
          if (boundary === 'selection') expect(textarea()).toHaveValue(fileB.content);
          expect(mocks.select).not.toHaveBeenCalled();
        });
      }
    }
    it('C1 keeps a direct same-file refresh on mounting with nonzero edit version', async () => {
      mocks.context.lastEditedFileId = fileA.id; mocks.context.editorRefreshVersion = 4;
      mount(strict); await settle(); const before = mocks.get.mock.calls.length;
      const refreshed = { ...fileA, content: 'Refreshed same file', updated_at: token2 };
      mocks.get.mockResolvedValue(refreshed);
      await advance(100);
      expect(mocks.get.mock.calls.length).toBe(before + 1); expect(textarea()).toHaveValue(refreshed.content);
    });
    it('C1 does not restore shared selection when a failed switch flush completes after unmount', async () => {
      const pending = deferred<typeof fileA>(); mocks.update.mockReturnValue(pending.promise);
      const view = mount(strict); await settle();
      fireEvent.change(textarea(), { target: { value: 'Dirty A needing flush' } });
      mocks.context.selectedItem = selected(fileB); view.redraw(); await settle();
      expect(mocks.update).toHaveBeenCalledTimes(1);
      view.unmount(); pending.reject(new Error('Write failed')); await settle();
      evidence(`C1 failed flush ${strict}`);
      expect(mocks.select).not.toHaveBeenCalled(); expect(mocks.get.mock.calls.some(([id]) => id === fileB.id)).toBe(false);
    });
    it('C1 preserves the legitimate final dirty write at actual unmount', async () => {
      const view = mount(strict); await settle();
      fireEvent.change(textarea(), { target: { value: 'Final dirty manuscript' } }); view.unmount(); await settle();
      expect(mocks.update).toHaveBeenCalledTimes(1);
      expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ content: 'Final dirty manuscript', base_updated_at: fileA.updated_at }));
    });

    for (const boundary of ['unmount-remount', 'unmount-project', 'mounted-file', 'mounted-project'] as const) {
      it(`late-save preserves replacement selection after ${boundary} rename`, async () => {
        const pending = deferred<typeof fileA>(); mocks.update.mockReturnValueOnce(pending.promise);
        const view = mount(strict); await settle();
        fireEvent.change(screen.getByPlaceholderText('editor:placeholder.titlePlaceholder'), { target: { value: 'Renamed old A' } });
        fireEvent.change(textarea(), { target: { value: 'Pending renamed A manuscript' } });
        const unmounted = boundary.startsWith('unmount');
        if (unmounted) view.unmount(); else save();
        await settle();
        expect(mocks.update).toHaveBeenCalledTimes(1);
        expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ title: 'Renamed old A', content: 'Pending renamed A manuscript', base_updated_at: fileA.updated_at }));
        if (boundary.endsWith('project')) {
          mocks.context.currentProjectId = 'project-b';
          mocks.get.mockImplementation(async (id: string) => id === fileB.id ? { ...fileB, project_id: 'project-b' } : fileA);
        }
        mocks.context.selectedItem = selected(fileB);
        const replacement = unmounted ? mount(strict) : view;
        if (!unmounted) view.redraw();
        await settle();
        if (unmounted) expect(textarea()).toHaveValue(fileB.content);
        const liveSelection = mocks.context.selectedItem;
        pending.resolve({ ...fileA, title: 'Renamed old A', content: 'Pending renamed A manuscript', updated_at: token2 });
        await settle(); evidence(`late-save ${strict} ${boundary}`);
        expect(mocks.update).toHaveBeenCalledTimes(1);
        expect(mocks.recordStats).toHaveBeenCalledTimes(1); // actual child receives saved result and finishes the legitimate write
        expect(mocks.refresh).toHaveBeenCalledTimes(1); // renamed A still reconciles its tree after remount
        expect(mocks.select).not.toHaveBeenCalled();
        expect(mocks.context.selectedItem).toBe(liveSelection);
        replacement.redraw(); await settle(); expect(textarea()).toHaveValue(fileB.content);
      });
    }
    it('late-save keeps ordinary live A rename selection and returned token', async () => {
      const pending = deferred<typeof fileA>(); mocks.update.mockReturnValueOnce(pending.promise);
      const view = mount(strict); await settle();
      fireEvent.change(screen.getByPlaceholderText('editor:placeholder.titlePlaceholder'), { target: { value: 'Live renamed A' } });
      fireEvent.change(textarea(), { target: { value: 'Live renamed A draft' } }); save(); await settle();
      expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ title: 'Live renamed A', base_updated_at: fileA.updated_at }));
      const committed = { ...fileA, title: 'Live renamed A', content: 'Live renamed A draft', updated_at: token2 };
      mocks.get.mockResolvedValue(committed);
      pending.resolve(committed);
      await settle(); view.redraw(); await settle(); evidence(`late-save ${strict} ordinary live rename`);
      expect(mocks.refresh).toHaveBeenCalledTimes(1);
      expect(mocks.select).toHaveBeenCalledTimes(1);
      expect(mocks.select).toHaveBeenCalledWith({ ...selected(), title: 'Live renamed A' });
      expect(screen.getByPlaceholderText('editor:placeholder.titlePlaceholder')).toHaveValue('Live renamed A');
      fireEvent.change(textarea(), { target: { value: 'Next live renamed draft' } }); save(); await settle();
      expect(mocks.update).toHaveBeenLastCalledWith(fileA.id, expect.objectContaining({ content: 'Next live renamed draft', base_updated_at: token2 }));
      expect(mocks.refresh).toHaveBeenCalledTimes(1); expect(mocks.select).toHaveBeenCalledTimes(1);
    });
    it('late-save keeps unchanged-title final dirty write without selection/tree side effects', async () => {
      const pending = deferred<typeof fileA>(); mocks.update.mockReturnValueOnce(pending.promise);
      const view = mount(strict); await settle();
      fireEvent.change(textarea(), { target: { value: 'Final unchanged-title dirty draft' } });
      view.unmount(); await settle(); mocks.context.selectedItem = selected(fileB);
      const replacement = mount(strict); await settle();
      pending.resolve({ ...fileA, content: 'Final unchanged-title dirty draft', updated_at: token2 });
      await settle(); evidence(`late-save ${strict} unchanged title final write`);
      expect(mocks.update).toHaveBeenCalledTimes(1);
      expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ title: fileA.title, content: 'Final unchanged-title dirty draft', base_updated_at: fileA.updated_at }));
      expect(mocks.recordStats).toHaveBeenCalledTimes(1);
      expect(mocks.refresh).not.toHaveBeenCalled(); expect(mocks.select).not.toHaveBeenCalled();
      replacement.redraw(); await settle(); expect(textarea()).toHaveValue(fileB.content);
    });
    it('late-save renamed pending queue advances from exact own T1 result', async () => {
      const pending = deferred<typeof fileA>();
      let committed = fileA;
      mocks.get.mockImplementation(async () => committed);
      mocks.update.mockImplementation(async (_id: string, data: object) => { committed = { ...committed, ...data, updated_at: '2026-10-06T10:00:00.000003' }; return committed; }).mockReturnValueOnce(pending.promise);
      mount(strict); await settle();
      fireEvent.change(screen.getByPlaceholderText('editor:placeholder.titlePlaceholder'), { target: { value: 'Queued renamed A' } });
      fireEvent.change(textarea(), { target: { value: 'First renamed draft' } });
      fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true }); await settle();
      fireEvent.change(textarea(), { target: { value: 'Second renamed draft' } });
      fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true }); await settle();
      expect(mocks.update).toHaveBeenCalledTimes(1);
      committed = { ...fileA, title: 'Queued renamed A', content: 'First renamed draft', updated_at: token2 };
      pending.resolve(committed);
      await settle(); evidence(`late-save ${strict} renamed queue`);
      expect(mocks.update).toHaveBeenCalledTimes(2);
      expect(mocks.update).toHaveBeenNthCalledWith(1, fileA.id, expect.objectContaining({ title: 'Queued renamed A', content: 'First renamed draft', base_updated_at: fileA.updated_at }));
      expect(mocks.update).toHaveBeenNthCalledWith(2, fileA.id, expect.objectContaining({ title: 'Queued renamed A', content: 'Second renamed draft', base_updated_at: token2 }));
      expect(textarea()).toHaveValue('Second renamed draft');
      expect(mocks.refresh).toHaveBeenCalledTimes(1); expect(mocks.select).toHaveBeenCalledTimes(1);
    });

    for (const result of ['success', 'conflict'] as const) {
      for (const boundary of ['file', 'project', 'review', 'unmount'] as const) {
        it(`C2 fences review ${result} completion after ${boundary} replacement`, async () => {
          mocks.context.diffReviewState = review();
          const pending = deferred<typeof fileA>(); mocks.update.mockReturnValueOnce(pending.promise);
          const view = mount(strict); await settle();
          fireEvent.click(screen.getByRole('button', { name: 'editor:applyChanges' })); await settle();
          expect(mocks.update).toHaveBeenCalledTimes(1);
          expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ content: 'Reviewed A', base_updated_at: fileA.updated_at, change_type: 'ai_edit' }));
          if (boundary === 'unmount') view.unmount();
          else {
            if (boundary !== 'review') mocks.context.selectedItem = selected(fileB);
            if (boundary === 'project') {
              mocks.context.currentProjectId = 'project-b';
              mocks.get.mockResolvedValue({ ...fileB, project_id: 'project-b' });
            }
            mocks.context.diffReviewState = boundary === 'review' ? review(fileA, 'New review A') : review(fileB, 'New review B');
            view.redraw(); await settle();
          }
          const liveReview = mocks.context.diffReviewState;
          if (result === 'success') pending.resolve({ ...fileA, content: 'Reviewed A', updated_at: token2 });
          else pending.reject(new ApiError(409, 'ERR_CONFLICT', { reason: 'stale_write', current_content: 'Server A conflict', current_updated_at: token2 }));
          await settle(); evidence(`C2 ${strict} ${result} ${boundary}`);
          expect(mocks.exit).not.toHaveBeenCalled(); expect(mocks.enter).not.toHaveBeenCalled();
          expect(mocks.context.diffReviewState).toBe(liveReview); expect(mocks.refresh).not.toHaveBeenCalled();
          if (boundary !== 'unmount') {
            mocks.context.diffReviewState = null; view.redraw(); await settle();
            expect(textarea()).toHaveValue(boundary === 'review' ? fileA.content : fileB.content);
            // The next actual child save also observes the unchanged live file token.
            fireEvent.change(textarea(), { target: { value: 'Next live edit' } }); save(); await settle();
            expect(mocks.update).toHaveBeenLastCalledWith(boundary === 'review' ? fileA.id : fileB.id, expect.objectContaining({ base_updated_at: fileA.updated_at }));
          }
        });
      }
      it(`C2 preserves same-file same-review ${result} completion`, async () => {
        mocks.context.diffReviewState = review();
        const pending = deferred<typeof fileA>(); mocks.update.mockReturnValueOnce(pending.promise);
        const view = mount(strict); await settle();
        fireEvent.click(screen.getByRole('button', { name: 'editor:applyChanges' })); await settle();
        if (result === 'success') pending.resolve({ ...fileA, content: 'Reviewed A', updated_at: token2 });
        else pending.reject(new ApiError(409, 'ERR_CONFLICT', { reason: 'stale_write', current_content: 'Server A conflict', current_updated_at: token2 }));
        await settle(); view.redraw(); await settle();
        if (result === 'success') {
          expect(mocks.exit).toHaveBeenCalledTimes(1); expect(textarea()).toHaveValue('Reviewed A');
          expect(mocks.refresh).toHaveBeenCalledTimes(1);
        } else {
          expect(mocks.enter).toHaveBeenCalledWith(fileA.id, 'Reviewed A', 'Server A conflict'); expect(mocks.exit).not.toHaveBeenCalled();
          mocks.context.diffReviewState = null; view.redraw(); await settle();
        }
        fireEvent.change(textarea(), { target: { value: 'Next reviewed edit' } }); save(); await settle();
        expect(mocks.update).toHaveBeenLastCalledWith(fileA.id, expect.objectContaining({ base_updated_at: token2 }));
      });
    }

    for (const boundary of ['project', 'unmount'] as const) {
      it(`C3 stops new creation after folder lookup across ${boundary}`, async () => {
        mocks.context.selectedItem = null;
        const pending = deferred<{ tree: [] }>(); mocks.getTree.mockReturnValue(pending.promise);
        const view = mount(strict); await settle();
        fireEvent.click(screen.getByText('editor:fileTree.newDraft')); await settle();
        expect(mocks.getTree).toHaveBeenCalledTimes(1);
        if (boundary === 'unmount') view.unmount();
        else { mocks.context.currentProjectId = 'project-b'; mocks.context.selectedItem = selected(fileB); mocks.get.mockResolvedValue({ ...fileB, project_id: 'project-b' }); view.redraw(); await settle(); }
        pending.resolve({ tree: [] }); await settle(); evidence(`C3 lookup ${strict} ${boundary}`);
        expect(mocks.create).not.toHaveBeenCalled(); expect(mocks.select).not.toHaveBeenCalled(); expect(mocks.refresh).not.toHaveBeenCalled();
      });
      for (const result of ['success', 'error'] as const) {
        it(`C3 preserves completed-create ${result} notification without stale selection after ${boundary}`, async () => {
          mocks.context.selectedItem = null;
          const pending = deferred<typeof fileA>(); mocks.create.mockReturnValue(pending.promise);
          const view = mount(strict); await settle(); fireEvent.click(screen.getByText('editor:fileTree.newDraft')); await settle();
          expect(mocks.create).toHaveBeenCalledTimes(1);
          if (boundary === 'unmount') view.unmount();
          else { mocks.context.currentProjectId = 'project-b'; mocks.context.selectedItem = selected(fileB); mocks.get.mockResolvedValue({ ...fileB, project_id: 'project-b' }); view.redraw(); await settle(); }
          if (result === 'success') pending.resolve({ ...fileA, id: 'created-a' }); else pending.reject(new Error('Create rejected'));
          await settle(); evidence(`C3 completion ${strict} ${result} ${boundary}`);
          expect(result === 'success' ? mocks.success : mocks.error).toHaveBeenCalledWith(result === 'success' ? 'editor:success.fileCreated' : 'editor:error.createFailed');
          expect(mocks.select).not.toHaveBeenCalled(); expect(mocks.refresh).not.toHaveBeenCalled();
        });
      }
    }
    it('C3 preserves mounted same-project create selection/refresh/notification', async () => {
      mocks.context.selectedItem = null; mount(strict); await settle();
      fireEvent.click(screen.getByText('editor:fileTree.newDraft')); await settle();
      expect(mocks.create).toHaveBeenCalledTimes(1); expect(mocks.select).toHaveBeenCalledWith({ ...selected(), id: 'created-a' });
      expect(mocks.refresh).toHaveBeenCalledTimes(1); expect(mocks.success).toHaveBeenCalledTimes(1);
    });

    for (const stage of ['edit', 'failure', 'pending-edit'] as const) {
      it(`C4 shows dirty status after prior save at ${stage} and permits successful retry`, async () => {
        mount(strict); await settle(); fireEvent.change(textarea(), { target: { value: 'First edit' } }); save(); await settle();
        expect(screen.getByText('editor:savedJustNow')).toBeInTheDocument();
        fireEvent.change(textarea(), { target: { value: 'Second edit' } });
        if (stage === 'failure') { mocks.update.mockRejectedValueOnce(new Error('Save rejected')); save(); await settle(); }
        if (stage === 'pending-edit') {
          const pending = deferred<typeof fileA>(); mocks.update.mockReturnValueOnce(pending.promise); save(); await settle();
          expect(screen.getByText('editor:saving')).toBeInTheDocument();
          fireEvent.change(textarea(), { target: { value: 'Third edit during save' } });
          pending.resolve({ ...fileA, content: 'Second edit', updated_at: token2 }); await settle();
        }
        evidence(`C4 ${strict} ${stage}`);
        expect(screen.getByText('editor:unsaved')).toBeInTheDocument(); expect(screen.queryByText('editor:savedJustNow')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'editor:save' })).toBeEnabled();
        save(); await settle(); expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument(); expect(screen.getByText('editor:savedJustNow')).toBeInTheDocument();
      });
    }

    it('C5 adopts a clean refresh token for the first subsequent edit', async () => {
      const view = mount(strict); await settle();
      mocks.context.lastEditedFileId = fileA.id; mocks.context.editorRefreshVersion = 1;
      mocks.get.mockResolvedValue({ ...fileA, content: 'Clean server refresh', updated_at: token2 });
      view.redraw(); await settle(); await advance(100);
      expect(textarea()).toHaveValue('Clean server refresh');
      fireEvent.change(textarea(), { target: { value: 'Clean server refresh plus edit' } }); save(); await settle();
      expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ base_updated_at: token2 }));
    });
    for (const external of [false, true]) {
      it(`C5 queue advances only from own returned token (external during stats=${external})`, async () => {
        const first = deferred<typeof fileA>(); const stats = deferred<void>();
        mocks.update.mockReturnValueOnce(first.promise);
        if (external) mocks.recordStats.mockReturnValueOnce(stats.promise);
        const view = mount(strict); await settle();
        fireEvent.change(textarea(), { target: { value: 'Own first save' } });
        fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true }); await settle();
        fireEvent.change(textarea(), { target: { value: 'Own queued second save' } });
        fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true }); await settle();
        expect(mocks.update).toHaveBeenCalledTimes(1);
        first.resolve({ ...fileA, content: 'Own first save', updated_at: token2 }); await settle();
        if (external) {
          expect(mocks.update).toHaveBeenCalledTimes(1);
          mocks.context.aiEditingFileId = fileA.id; mocks.context.lastEditedFileId = fileA.id; mocks.context.editorRefreshVersion = 1;
          mocks.get.mockResolvedValue({ ...fileA, content: 'External after own save', updated_at: '2026-10-06T10:00:00.000003' });
          view.redraw(); await settle(); await advance(100);
          // A queued own draft is unsaved text: the external copy opens a comparison, never replaces it.
          expect(mocks.enter).toHaveBeenCalledWith(fileA.id, 'Own queued second save', 'External after own save');
          expect(screen.queryByPlaceholderText('editor:placeholder.contentPlaceholder')).toBeNull();
          stats.resolve(); await settle();
        }
        evidence(`C5 queue ${strict} external=${external}`);
        expect(mocks.update).toHaveBeenCalledTimes(2);
        expect(mocks.update).toHaveBeenNthCalledWith(1, fileA.id, expect.objectContaining({ base_updated_at: fileA.updated_at }));
        expect(mocks.update).toHaveBeenNthCalledWith(2, fileA.id, expect.objectContaining({ content: 'Own queued second save', base_updated_at: token2 }));
      });
    }
    for (const outcome of ['failure', 'conflict'] as const) {
      it(`C5 ${outcome} does not advance a queued draft token`, async () => {
        const first = deferred<typeof fileA>(); const second = deferred<typeof fileA>();
        mocks.update.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
        mount(strict); await settle();
        fireEvent.change(textarea(), { target: { value: 'First dirty version' } }); fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true }); await settle();
        fireEvent.change(textarea(), { target: { value: 'Queued local version' } }); fireEvent.keyDown(textarea(), { key: 's', ctrlKey: true }); await settle();
        const error = outcome === 'failure' ? new Error('Failed PUT') : new ApiError(409, 'ERR_CONFLICT', {
          reason: 'stale_write', current_content: 'External server', current_updated_at: token2,
        });
        first.reject(error); await settle(); evidence(`C5 ${strict} queue ${outcome}`);
        expect(mocks.update).toHaveBeenCalledTimes(2);
        expect(mocks.update).toHaveBeenNthCalledWith(2, fileA.id, expect.objectContaining({ content: 'Queued local version', base_updated_at: fileA.updated_at }));
        second.reject(error); await settle();
        if (outcome === 'conflict') expect(mocks.context.diffReviewState?.originalContent).toBe('Queued local version');
        else expect(screen.getByText('editor:unsaved')).toBeInTheDocument();
      });
    }
    it('C5 keeps old-file flush token and initializes the newly loaded file token', async () => {
      const pending = deferred<typeof fileA>(); mocks.update.mockReturnValueOnce(pending.promise);
      mocks.get.mockImplementation(async (id: string) => id === fileB.id ? { ...fileB, updated_at: '2026-10-06T10:00:00.000100' } : fileA);
      const view = mount(strict); await settle();
      fireEvent.change(textarea(), { target: { value: 'Dirty old A' } });
      mocks.context.selectedItem = selected(fileB); view.redraw(); await settle();
      expect(mocks.update).toHaveBeenCalledTimes(1); expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ content: 'Dirty old A', base_updated_at: fileA.updated_at }));
      pending.resolve({ ...fileA, updated_at: token2 }); await settle();
      expect(textarea()).toHaveValue(fileB.content);
      fireEvent.change(textarea(), { target: { value: 'New clean B edit' } }); save(); await settle();
      expect(mocks.update).toHaveBeenLastCalledWith(fileB.id, expect.objectContaining({ base_updated_at: '2026-10-06T10:00:00.000100' }));
    });

    it('C5 AI refresh over a dirty draft keeps the draft and compares it with the server copy', async () => {
      const server = { ...fileA, content: 'Server AI content', updated_at: token2 };
      const view = mount(strict); await settle();
      expect(mocks.get).toHaveBeenCalledTimes(strict ? 2 : 1);
      fireEvent.change(textarea(), { target: { value: 'Local unsaved draft' } });
      mocks.context.aiEditingFileId = fileA.id; mocks.context.lastEditedFileId = fileA.id; mocks.context.editorRefreshVersion = 1;
      mocks.get.mockResolvedValue(server); view.redraw(); await settle(); await advance(100);
      expect(mocks.get).toHaveBeenCalledTimes(strict ? 3 : 2);
      expect(mocks.update).not.toHaveBeenCalled();
      expect(screen.queryByPlaceholderText('editor:placeholder.contentPlaceholder')).toBeNull();
      // The author's draft is the left side; the AI's copy is the change to review.
      expect(mocks.enter).toHaveBeenCalledWith(fileA.id, 'Local unsaved draft', 'Server AI content');
      expect(mocks.error).toHaveBeenCalledWith('editor:aiEditedWhileDirty');
      // While the comparison is open the debounce does not race it with the old token.
      mocks.context.aiEditingFileId = null; view.redraw(); await settle();
      await advance(3000);
      expect(mocks.update).not.toHaveBeenCalled();
      fireEvent.click(screen.getByTitle(/editor:(finishReview|applyChanges)/)); await settle();
      evidence(`C5 ${strict} dirty refresh review`);
      expect(mocks.update).toHaveBeenCalledTimes(1);
      expect(mocks.update).toHaveBeenCalledWith(fileA.id, expect.objectContaining({ content: 'Local unsaved draft', base_updated_at: token2 }));
    });
  });
}
