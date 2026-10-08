import { createContext, StrictMode, useContext } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FileSearchProvider } from '../../contexts/FileSearchContext';
import type { FileTreeNode, SelectedItem } from '../../types';
import { FileTreePane } from '../sidebar/FileTreePane';
import { MobileFileTree } from '../MobileFileTree';

const mocks = vi.hoisted(() => ({
  getTree: vi.fn(), create: vi.fn(), delete: vi.fn(), upload: vi.fn(), uploadDraft: vi.fn(),
  select: vi.fn(), switchToEditor: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn(),
  loggerError: vi.fn(), addMaterial: vi.fn(() => true), removeMaterial: vi.fn(),
  attached: false, atLimit: false,
}));
const ProjectFixture = createContext({
  currentProjectId: 'project-1' as string | null,
  fileTreeVersion: 0,
  selectedItem: null as SelectedItem | null,
  setSelectedItem: mocks.select,
});
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => useContext(ProjectFixture) }));
vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({ isMobile: false, switchToEditor: mocks.switchToEditor }),
}));
vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  MAX_ATTACHED_MATERIALS: 5,
  useMaterialAttachment: () => ({
    addMaterial: mocks.addMaterial, removeMaterial: mocks.removeMaterial,
    isMaterialAttached: () => mocks.attached, isAtLimit: mocks.atLimit,
  }),
}));
vi.mock('../../lib/api', () => ({ fileApi: mocks }));
vi.mock('../../lib/toast', () => ({ toast: { error: mocks.error, success: mocks.success, info: mocks.info } }));
vi.mock('../../lib/logger', () => ({ logger: { error: mocks.loggerError, warn: vi.fn() } }));

function node(id: string, title: string, file_type = 'draft', children: FileTreeNode[] = []): FileTreeNode {
  return { id, title, file_type, parent_id: null, order: 0, metadata: null, children };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
function mountTree(mobile: boolean, strict = false, fileTreeVersion = 0) {
  let project = { currentProjectId: 'project-1' as string | null, fileTreeVersion, selectedItem: null as SelectedItem | null, setSelectedItem: mocks.select };
  const content = () => {
    const tree = <ProjectFixture.Provider value={project}><FileSearchProvider>
      {mobile ? <MobileFileTree /> : <FileTreePane />}
    </FileSearchProvider></ProjectFixture.Provider>;
    return strict ? <StrictMode>{tree}</StrictMode> : tree;
  };
  const view = render(content());
  return {
    ...view,
    update(next: Partial<typeof project>) { project = { ...project, ...next }; view.rerender(content()); },
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.attached = false;
  mocks.atLimit = false;
  mocks.addMaterial.mockReturnValue(true);
  mocks.getTree.mockResolvedValue({ tree: [node('root', '角色', 'folder', [node('file', 'Author', 'character')])] });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

for (const mobile of [false, true]) {
  describe(mobile ? 'production mobile tree' : 'production desktop tree', () => {
    describe.each([false, true])('post-unmount non-draft completion (StrictMode=%s)', strict => {
      it.each([
        { mutation: 'create', rejected: false }, { mutation: 'create', rejected: true },
        { mutation: 'delete', rejected: false }, { mutation: 'delete', rejected: true },
        { mutation: 'material', rejected: false }, { mutation: 'material', rejected: true },
      ])('does not refresh or clear shared selection after $mutation (rejected=$rejected)', async ({ mutation, rejected }) => {
        const pending = deferred<object>();
        if (mutation === 'material') mocks.getTree.mockResolvedValue({ tree: [node('materials', 'Materials', 'folder')] });
        const view = mountTree(mobile, strict);
        await screen.findByText(mutation === 'material' ? 'Materials' : 'Author');
        let sharedSelection: SelectedItem | null = { id: 'file', title: 'Author', type: 'character' };
        mocks.select.mockImplementation((item: SelectedItem | null) => { sharedSelection = item; });
        if (mutation === 'create') {
          mocks.create.mockReturnValue(pending.promise);
          fireEvent.click(screen.getByTitle(mobile ? 'common:create character' : 'common:create common:fileTypes.character'));
          const input = screen.getByPlaceholderText('editor:fileTree.newCharacter');
          fireEvent.change(input, { target: { value: 'Pending author' } });
          fireEvent.keyDown(input, { key: 'Enter' });
          expect(mocks.create).toHaveBeenCalledTimes(1);
        } else if (mutation === 'delete') {
          view.update({ selectedItem: sharedSelection });
          mocks.delete.mockReturnValue(pending.promise);
          vi.stubGlobal('confirm', vi.fn(() => true));
          fireEvent.click(screen.getByTitle('common:delete'));
          expect(mocks.delete).toHaveBeenCalledTimes(1);
        } else {
          mocks.upload.mockReturnValue(pending.promise);
          fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(['material'], 'pending.txt')] } });
          expect(mocks.upload).toHaveBeenCalledTimes(1);
        }
        const treeCalls = mocks.getTree.mock.calls.length;
        const signal: AbortSignal = mocks.getTree.mock.calls.at(-1)![1].signal;
        view.unmount();
        expect(signal.aborted).toBe(true);
        // A remaining panel/provider may select a different file while this tree
        // is hidden. The pending delete captured the old selected file.
        const replacement: SelectedItem = { id: 'replacement', title: 'Elsewhere', type: 'draft' };
        mocks.select(replacement);
        mocks.select.mockClear();
        await act(async () => {
          if (rejected) pending.reject(new Error('Pending operation rejected'));
          else pending.resolve({});
        });
        expect.soft(mocks.getTree).toHaveBeenCalledTimes(treeCalls);
        expect.soft(mocks.select).not.toHaveBeenCalled();
        expect.soft(sharedSelection).toEqual(replacement);
        // Preserve the existing global notification policy while hidden.
        if (mutation === 'material' && !rejected) expect(mocks.success).toHaveBeenCalledWith('editor:fileTree.uploadSuccess');
        else expect(mocks.success).not.toHaveBeenCalled();
        if (rejected && mutation !== 'material') expect(mocks.error).toHaveBeenCalledWith(`editor:fileTree.${mutation}Failed`);
        else if (rejected && !mobile) expect(mocks.error).toHaveBeenCalledWith('Pending operation rejected');
        else expect(mocks.error).not.toHaveBeenCalled();
      });
    });

    it.each([false, true])('loads and silently refreshes with nonzero fileTreeVersion (StrictMode=%s)', async strict => {
      const view = mountTree(mobile, strict, 3);
      await screen.findByText('Author');
      expect(mocks.getTree.mock.calls.at(-1)![1].signal.aborted).toBe(false);
      const initialCalls = mocks.getTree.mock.calls.length;
      view.update({ fileTreeVersion: 4 });
      await waitFor(() => expect(mocks.getTree).toHaveBeenCalledTimes(initialCalls + 1));
      expect(screen.getByText('Author')).toBeInTheDocument();
      expect(mocks.getTree.mock.calls.map(call => call[0]).every(id => id === 'project-1')).toBe(true);
    });

    it('preserves root expansion, nested collapse, order, selection and mobile switching', async () => {
      mocks.getTree.mockResolvedValue({ tree: [node('root', 'Root', 'folder', [
        node('nested', 'Nested', 'folder', [node('leaf', 'Leaf')]), node('last', 'Last'),
      ])] });
      const view = mountTree(mobile);
      await screen.findByText('Nested');
      expect(screen.queryByText('Leaf')).not.toBeInTheDocument();
      fireEvent.click(screen.getByText('Nested'));
      expect(screen.getByText('Leaf')).toBeInTheDocument();
      expect(Array.from(view.container.querySelectorAll('span')).filter(el => ['Leaf', 'Last'].includes(el.textContent || '')).map(el => el.textContent)).toEqual(['Leaf', 'Last']);
      fireEvent.click(screen.getByText('Leaf'));
      expect(mocks.select).toHaveBeenCalledWith({ id: 'leaf', title: 'Leaf', type: 'draft' });
      expect(mocks.switchToEditor).toHaveBeenCalledTimes(mobile ? 1 : 0);
      fireEvent.click(screen.getByText('Root'));
      expect(screen.queryByText('Nested')).not.toBeInTheDocument();
      expect(mocks.getTree).toHaveBeenCalledTimes(1);
    });

    it('invalidates pending responses when the current project is cleared', async () => {
      const request = deferred<{ tree: FileTreeNode[] }>();
      mocks.getTree.mockReturnValue(request.promise);
      const view = mountTree(mobile);
      const signal: AbortSignal = mocks.getTree.mock.calls[0][1].signal;
      view.update({ currentProjectId: null });
      await act(async () => { request.resolve({ tree: [node('old', 'Stale project file')] }); });
      expect(screen.queryByText('Stale project file')).not.toBeInTheDocument();
      expect(signal.aborted).toBe(true);
      expect(mocks.getTree).toHaveBeenCalledTimes(1);
      expect(screen.getByText('editor:fileTree.folderEmpty')).toBeInTheDocument();
    });

    it('ignores non-AbortError rejections from a cancelled transport', async () => {
      const request = deferred<{ tree: FileTreeNode[] }>();
      mocks.getTree.mockReturnValue(request.promise);
      const view = mountTree(mobile);
      view.update({ currentProjectId: null });
      await act(async () => { request.reject(new Error('late transport failure')); });
      expect(mocks.loggerError).not.toHaveBeenCalled();
      expect(screen.getByText('editor:fileTree.folderEmpty')).toBeInTheDocument();
    });

    it('clears the loaded tree when there is no current project', async () => {
      const view = mountTree(mobile);
      await screen.findByText('Author');
      view.update({ currentProjectId: null });
      expect(screen.queryByText('Author')).not.toBeInTheDocument();
      expect(screen.getByText('editor:fileTree.folderEmpty')).toBeInTheDocument();
      expect(mocks.getTree).toHaveBeenCalledTimes(1);
    });

    it('does not leave previous-project files selectable during a project switch', async () => {
      const view = mountTree(mobile);
      await screen.findByText('Author');
      const request = deferred<{ tree: FileTreeNode[] }>();
      mocks.getTree.mockReturnValue(request.promise);
      view.update({ currentProjectId: 'project-2' });
      expect(screen.queryByText('Author')).not.toBeInTheDocument();
      await act(async () => { request.resolve({ tree: [node('new', 'New project file')] }); });
      expect(screen.getByText('New project file')).toBeInTheDocument();
    });

    it.each(mobile ? ['create', 'delete', 'upload'] : ['create', 'delete', 'upload', 'draft'])('does not reload an old project when a pending %s finishes after switching', async mutation => {
      const pending = deferred<{ total: number; errors: string[] }>();
      if (mutation === 'upload') mocks.getTree.mockResolvedValue({ tree: [node('materials', 'Materials', 'folder')] });
      if (mutation === 'draft') mocks.getTree.mockResolvedValue({ tree: [node('drafts', 'Drafts', 'folder')] });
      const view = mountTree(mobile);
      await screen.findByRole('searchbox');
      if (mutation === 'create') {
        mocks.create.mockReturnValue(pending.promise);
        fireEvent.click(await screen.findByTitle(mobile ? 'common:create character' : 'common:create common:fileTypes.character'));
        const input = screen.getByPlaceholderText('editor:fileTree.newCharacter');
        fireEvent.change(input, { target: { value: 'Created in old project' } });
        fireEvent.keyDown(input, { key: 'Enter' });
      } else if (mutation === 'delete') {
        mocks.delete.mockReturnValue(pending.promise);
        vi.stubGlobal('confirm', vi.fn(() => true));
        fireEvent.click(await screen.findByTitle('common:delete'));
      } else {
        if (mutation === 'draft') mocks.uploadDraft.mockReturnValue(pending.promise);
        else mocks.upload.mockReturnValue(pending.promise);
        await screen.findByTitle(mutation === 'draft' ? 'editor:fileTree.uploadDraft' : 'editor:fileTree.uploadMaterial');
        fireEvent.change(view.container.querySelector(mutation === 'draft' ? 'input[multiple]' : 'input[type="file"]')!, { target: { files: [new File(['text'], 'old.txt')] } });
      }
      mocks.getTree.mockResolvedValueOnce({ tree: [node('new', 'New project file')] });
      view.update({ currentProjectId: 'project-2' });
      await screen.findByText('New project file');
      await act(async () => { pending.resolve({ total: 1, errors: [] }); });
      expect(mocks.getTree).toHaveBeenCalledTimes(2);
      expect(screen.getByText('New project file')).toBeInTheDocument();
      expect(mocks.getTree.mock.calls.map(call => call[0])).toEqual(['project-1', 'project-2']);
    });

    it('keeps the new project selection when an old selected file finishes deleting', async () => {
      const pending = deferred<object>();
      mocks.delete.mockReturnValue(pending.promise);
      vi.stubGlobal('confirm', vi.fn(() => true));
      const view = mountTree(mobile);
      await screen.findByText('Author');
      view.update({ selectedItem: { id: 'file', title: 'Author', type: 'character' } });
      fireEvent.click(screen.getByTitle('common:delete'));
      mocks.getTree.mockResolvedValueOnce({ tree: [node('new', 'New project file')] });
      view.update({ currentProjectId: 'project-2', selectedItem: { id: 'new', title: 'New project file', type: 'draft' } });
      await screen.findByText('New project file');
      await act(async () => { pending.resolve({}); });
      expect(mocks.select).not.toHaveBeenCalled();
    });

    it('keeps the new project create form when an old create finishes', async () => {
      const pending = deferred<object>();
      mocks.create.mockReturnValue(pending.promise);
      const view = mountTree(mobile);
      fireEvent.click(await screen.findByTitle(mobile ? 'common:create character' : 'common:create common:fileTypes.character'));
      fireEvent.change(screen.getByPlaceholderText('editor:fileTree.newCharacter'), { target: { value: 'Old create' } });
      fireEvent.keyDown(screen.getByPlaceholderText('editor:fileTree.newCharacter'), { key: 'Enter' });
      mocks.getTree.mockResolvedValueOnce({ tree: [node('new-root', '设定', 'folder')] });
      view.update({ currentProjectId: 'project-2' });
      fireEvent.click(await screen.findByTitle(mobile ? 'common:create lore' : 'common:create common:fileTypes.lore'));
      const newInput = screen.getByPlaceholderText('editor:fileTree.newLore');
      fireEvent.change(newInput, { target: { value: 'New project idea' } });
      await act(async () => { pending.resolve({}); });
      expect(screen.getByPlaceholderText('editor:fileTree.newLore')).toHaveValue('New project idea');
    });

    it('keeps loaded files visible during a silent refresh and ignores its superseded result', async () => {
      const view = mountTree(mobile);
      await screen.findByText('Author');
      const stale = deferred<{ tree: FileTreeNode[] }>();
      const fresh = deferred<{ tree: FileTreeNode[] }>();
      mocks.getTree.mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise);
      view.update({ fileTreeVersion: 1 });
      expect(screen.getByText('Author')).toBeInTheDocument();
      view.update({ fileTreeVersion: 2 });
      await act(async () => { fresh.resolve({ tree: [node('new', 'Latest file')] }); });
      await act(async () => { stale.resolve({ tree: [node('old', 'Superseded file')] }); });
      expect(screen.queryByText('Superseded file')).not.toBeInTheDocument();
      expect(screen.getByText('Latest file')).toBeInTheDocument();
      expect(mocks.loggerError).not.toHaveBeenCalled();
    });

    it('shows the existing empty state after a non-abort load failure', async () => {
      mocks.getTree.mockRejectedValue(new Error('offline'));
      mountTree(mobile);
      expect(await screen.findByText('editor:fileTree.folderEmpty')).toBeInTheDocument();
      expect(mocks.loggerError).toHaveBeenCalledTimes(1);
    });

    it('cancels the StrictMode request and the current request on unmount without reporting errors', async () => {
      mocks.getTree.mockImplementation((_id, { signal }: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
      }));
      const view = mountTree(mobile, true);
      expect(mocks.getTree).toHaveBeenCalledTimes(2);
      expect(mocks.getTree.mock.calls[0][1].signal.aborted).toBe(true);
      view.unmount();
      await act(async () => { await Promise.resolve(); });
      expect(mocks.getTree.mock.calls[1][1].signal.aborted).toBe(true);
      expect(mocks.loggerError).not.toHaveBeenCalled();
    });

    it('creates with trimmed title and refreshes once without losing file selection behavior', async () => {
      mocks.create.mockResolvedValue({ id: 'created' });
      mountTree(mobile);
      fireEvent.click(await screen.findByTitle(mobile ? 'common:create character' : 'common:create common:fileTypes.character'));
      const input = screen.getByPlaceholderText('editor:fileTree.newCharacter');
      fireEvent.change(input, { target: { value: '  New author  ' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() => expect(mocks.getTree).toHaveBeenCalledTimes(2));
      expect(mocks.create).toHaveBeenCalledWith('project-1', { title: 'New author', file_type: 'character', parent_id: 'root', content: '' });
      await waitFor(() => expect(screen.queryByPlaceholderText('editor:fileTree.newCharacter')).not.toBeInTheDocument());
      expect(mocks.select).not.toHaveBeenCalled();
    });

    it('preserves a failed create name and error toast without refreshing', async () => {
      mocks.create.mockRejectedValue(new Error('offline'));
      mountTree(mobile);
      fireEvent.click(await screen.findByTitle(mobile ? 'common:create character' : 'common:create common:fileTypes.character'));
      const input = screen.getByPlaceholderText('editor:fileTree.newCharacter');
      fireEvent.change(input, { target: { value: 'Retry author' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('editor:fileTree.createFailed'));
      expect(input).toHaveValue('Retry author');
      expect(mocks.getTree).toHaveBeenCalledTimes(1);
    });

    it.each(['Escape', 'Enter'])('cancels an empty create via %s without an API call', async key => {
      mountTree(mobile);
      fireEvent.click(await screen.findByTitle(mobile ? 'common:create character' : 'common:create common:fileTypes.character'));
      fireEvent.keyDown(screen.getByPlaceholderText('editor:fileTree.newCharacter'), { key });
      expect(screen.queryByPlaceholderText('editor:fileTree.newCharacter')).not.toBeInTheDocument();
      expect(mocks.create).not.toHaveBeenCalled();
    });

    it.each([true, false])('preserves delete confirmation behavior (confirmed=%s)', async confirmed => {
      vi.stubGlobal('confirm', vi.fn(() => confirmed));
      mocks.delete.mockResolvedValue({});
      const view = mountTree(mobile);
      await screen.findByText('Author');
      view.update({ selectedItem: { id: 'file', type: 'character', title: 'Author' } });
      fireEvent.click(screen.getByTitle('common:delete'));
      if (confirmed) {
        await waitFor(() => expect(mocks.getTree).toHaveBeenCalledTimes(2));
        expect(mocks.delete).toHaveBeenCalledWith('file');
        expect(mocks.select).toHaveBeenCalledWith(null);
      } else {
        expect(mocks.delete).not.toHaveBeenCalled();
        expect(mocks.getTree).toHaveBeenCalledTimes(1);
        expect(mocks.select).not.toHaveBeenCalled();
      }
    });

    it('preserves the selected file on delete failure and shows the error toast', async () => {
      vi.stubGlobal('confirm', vi.fn(() => true));
      mocks.delete.mockRejectedValue(new Error('offline'));
      const view = mountTree(mobile);
      await screen.findByText('Author');
      view.update({ selectedItem: { id: 'file', type: 'character', title: 'Author' } });
      fireEvent.click(screen.getByTitle('common:delete'));
      await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('editor:fileTree.deleteFailed'));
      expect(screen.getByText('Author')).toBeInTheDocument();
      expect(mocks.select).not.toHaveBeenCalled();
      expect(mocks.getTree).toHaveBeenCalledTimes(1);
    });

    it.each([false, true])('preserves material attachment interactions (attached=%s)', async attached => {
      mocks.attached = attached;
      mocks.getTree.mockResolvedValue({ tree: [node('snippet', 'Material', 'snippet')] });
      mountTree(mobile);
      fireEvent.click(await screen.findByTitle(attached ? 'editor:fileTree.removeFromChat' : 'editor:fileTree.addToChat'));
      if (attached) expect(mocks.removeMaterial).toHaveBeenCalledWith('snippet');
      else expect(mocks.addMaterial).toHaveBeenCalledWith('snippet', 'Material');
      expect(mocks.select).not.toHaveBeenCalled();
    });

    it('preserves the attachment limit alert', async () => {
      mocks.atLimit = true;
      mocks.addMaterial.mockReturnValue(false);
      vi.stubGlobal('alert', vi.fn());
      mocks.getTree.mockResolvedValue({ tree: [node('snippet', 'Material', 'snippet')] });
      mountTree(mobile);
      fireEvent.click(await screen.findByTitle('editor:fileTree.addToChat'));
      expect(window.alert).toHaveBeenCalledWith('editor:fileTree.maxMaterials');
    });

    it.each([false, true])('preserves material upload refresh/error behavior (failure=%s)', async failure => {
      mocks.getTree.mockResolvedValue({ tree: [node('materials', 'Materials', 'folder')] });
      if (failure) mocks.upload.mockRejectedValue(new Error('Upload rejected'));
      else mocks.upload.mockResolvedValue({ id: 'uploaded' });
      const view = mountTree(mobile);
      const button = await screen.findByTitle('editor:fileTree.uploadMaterial');
      const input = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
      const file = new File(['material'], 'material.txt', { type: 'text/plain' });
      fireEvent.click(button);
      fireEvent.change(input, { target: { files: [file] } });
      if (failure) {
        if (mobile) await screen.findByText('Upload rejected');
        else await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('Upload rejected'));
        expect(mocks.getTree).toHaveBeenCalledTimes(1);
        expect(mocks.success).not.toHaveBeenCalled();
      } else {
        await waitFor(() => expect(mocks.success).toHaveBeenCalledWith('editor:fileTree.uploadSuccess'));
        expect(mocks.getTree).toHaveBeenCalledTimes(2);
      }
      expect(mocks.upload).toHaveBeenCalledWith('project-1', file);
      expect(input.value).toBe('');
      expect(button).not.toBeDisabled();
    });

    it('uses the real search hook to preserve nested parent paths and selection', async () => {
      const leaf = node('leaf', 'Needle chapter'); leaf.parent_id = 'part';
      mocks.getTree.mockResolvedValue({ tree: [node('book', 'Book', 'folder', [node('part', 'Part', 'folder', [leaf])])] });
      mountTree(mobile);
      await screen.findByText('Book');
      const input = screen.getByRole('searchbox');
      fireEvent.focus(input);
      fireEvent.change(input, { target: { value: 'Needle' } });
      expect(await screen.findByText('Book > Part')).toBeInTheDocument();
      fireEvent.keyDown(input, { key: 'Enter' });
      expect(mocks.select).toHaveBeenCalledWith({ id: 'leaf', type: 'draft', title: 'Needle chapter' });
    });

    it('renders and selects a 24-level nested file through the production renderer', async () => {
      let root = node('deep-leaf', 'Deep leaf');
      for (let depth = 23; depth >= 0; depth--) root = node(`depth-${depth}`, `Level ${depth}`, 'folder', [root]);
      mocks.getTree.mockResolvedValue({ tree: [root] });
      const view = mountTree(mobile);
      await screen.findByText('Level 1');
      for (let depth = 1; depth < 24; depth++) fireEvent.click(screen.getByText(`Level ${depth}`));
      fireEvent.click(screen.getByText('Deep leaf'));
      expect(mocks.select).toHaveBeenCalledWith({ id: 'deep-leaf', title: 'Deep leaf', type: 'draft' });
      expect(mocks.getTree).toHaveBeenCalledTimes(1);
      expect(view.container.querySelectorAll('button[title="common:delete"]')).toHaveLength(1);
    });

    it('measures the actual expanded renderer on 1,000 files and collapses without network calls', async () => {
      const children = Array.from({ length: 1000 }, (_, i) => node(`file-${i}`, `File ${i}`));
      mocks.getTree.mockResolvedValue({ tree: [node('root', 'Large root', 'folder', children)] });
      const start = performance.now();
      const cpuStart = process.cpuUsage();
      const view = mountTree(mobile);
      await screen.findByText('File 999');
      const expandedDom = view.container.querySelectorAll('*').length;
      const expandedRows = view.container.querySelectorAll('button[title="common:delete"]').length;
      const loadedMs = performance.now() - start;
      const cpu = process.cpuUsage(cpuStart);
      const collapseStart = performance.now();
      fireEvent.click(screen.getByText('Large root'));
      const collapsedDom = view.container.querySelectorAll('*').length;
      console.log('M04_RENDER_MEASUREMENT', JSON.stringify({ renderer: mobile ? 'mobile' : 'desktop', files: children.length, getTreeCalls: mocks.getTree.mock.calls.length, expandedRows, expandedDom, collapsedDom, loadedMs, loadCpuMs: (cpu.user + cpu.system) / 1000, collapseMs: performance.now() - collapseStart }));
      expect(expandedRows).toBe(1000);
      expect(screen.queryByText('File 999')).not.toBeInTheDocument();
      expect(mocks.getTree).toHaveBeenCalledTimes(1);
    });
  });
}

describe('desktop draft uploads', () => {
  beforeEach(() => mocks.getTree.mockResolvedValue({ tree: [node('drafts', 'Drafts', 'folder')] }));

  it.each([false, true])('uploads sequentially and aggregates chapter/error results (StrictMode=%s)', async strict => {
    const first = deferred<{ total: number; errors: string[] }>();
    mocks.uploadDraft.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ total: 2, errors: ['second.md: ERR_FILE_TYPE_INVALID'] });
    const view = mountTree(false, strict);
    await screen.findByTitle('editor:fileTree.uploadDraft');
    const files = [new File(['first'], 'first.txt'), new File(['second'], 'second.md')];
    const input = view.container.querySelector<HTMLInputElement>('input[multiple]')!;
    fireEvent.change(input, { target: { files } });
    expect(mocks.uploadDraft).toHaveBeenCalledTimes(1);
    expect(mocks.uploadDraft).toHaveBeenNthCalledWith(1, 'project-1', files[0]);
    expect(screen.getByTitle('editor:fileTree.uploadDraft')).toBeDisabled();
    await act(async () => { first.resolve({ total: 1, errors: [] }); });
    await waitFor(() => expect(mocks.uploadDraft).toHaveBeenCalledTimes(2));
    expect(mocks.uploadDraft).toHaveBeenNthCalledWith(2, 'project-1', files[1]);
    expect(mocks.error).toHaveBeenCalledWith('second.md: editor:fileTree.errorFileTypeInvalid');
    expect(mocks.success).toHaveBeenCalledWith('editor:fileTree.draftUploadSuccess');
    expect(mocks.getTree).toHaveBeenCalledTimes(strict ? 3 : 2);
    expect(screen.getByTitle('editor:fileTree.uploadDraft')).not.toBeDisabled();
    expect(input.value).toBe('');
  });

  it.each(['empty', 'failure'])('preserves draft %s notifications and restores the upload button', async outcome => {
    if (outcome === 'empty') mocks.uploadDraft.mockResolvedValue({ total: 0, errors: [] });
    else mocks.uploadDraft.mockRejectedValue(new Error('Draft upload rejected'));
    const view = mountTree(false);
    await screen.findByTitle('editor:fileTree.uploadDraft');
    fireEvent.change(view.container.querySelector('input[multiple]')!, { target: { files: [new File([''], 'empty.txt')] } });
    if (outcome === 'empty') await waitFor(() => expect(mocks.info).toHaveBeenCalledWith('editor:fileTree.draftUploadEmpty'));
    else await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('Draft upload rejected'));
    expect(mocks.getTree).toHaveBeenCalledTimes(outcome === 'empty' ? 2 : 1);
    expect(screen.getByTitle('editor:fileTree.uploadDraft')).not.toBeDisabled();
  });

  it.each([false, true])('stops a sequential draft batch after unmount without notifications or refresh (StrictMode=%s)', async strict => {
    const first = deferred<{ total: number; errors: string[] }>();
    mocks.uploadDraft.mockReturnValue(first.promise);
    const view = mountTree(false, strict);
    await screen.findByTitle('editor:fileTree.uploadDraft');
    fireEvent.change(view.container.querySelector('input[multiple]')!, { target: { files: [new File([''], 'one.txt'), new File([''], 'two.txt')] } });
    view.unmount();
    await act(async () => { first.resolve({ total: 1, errors: [] }); });
    expect(mocks.uploadDraft).toHaveBeenCalledTimes(1);
    expect(mocks.getTree).toHaveBeenCalledTimes(strict ? 2 : 1);
    expect(mocks.success).not.toHaveBeenCalled();
  });
});

it('preserves the mobile search filter ancestors and matched leaves without changing expansion', async () => {
  mocks.getTree.mockResolvedValue({ tree: [node('root', 'Book', 'folder', [node('part', 'Part', 'folder', [node('leaf', 'Needle'), node('other', 'Unmatched')])])] });
  mountTree(true);
  fireEvent.click(await screen.findByText('Part'));
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Needle' } });
  expect(screen.getByText('Book')).toBeInTheDocument();
  expect(screen.getByText('Part')).toBeInTheDocument();
  expect(screen.getByText('Needle')).toBeInTheDocument();
  expect(screen.queryByText('Unmatched')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'common:clearSearch' }));
  expect(screen.getByText('Unmatched')).toBeInTheDocument();
});
