import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FileSearchProvider } from '../../../contexts/FileSearchContext';
import { FileTreePane } from '../FileTreePane';

const mocks = vi.hoisted(() => ({
  select: vi.fn(),
  switchToEditor: vi.fn(),
  clearSearch: vi.fn(),
  getTree: vi.fn(),
  loggerError: vi.fn(),
  createFile: vi.fn(),
  deleteFile: vi.fn(),
  toastError: vi.fn(),
  toastInfo: vi.fn(),
  addMaterial: vi.fn(() => true),
  isAtLimit: false,
  projectId: 'project-1' as string | null,
  selectedItem: null as { id: string; type: string; title: string } | null,
  results: [{ id: 'draft-1', fileType: 'draft', title: 'Draft' }],
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('../../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProjectId: mocks.projectId, selectedItem: mocks.selectedItem, setSelectedItem: mocks.select, fileTreeVersion: 0 }),
}));
vi.mock('../../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({ isMobile: false, switchToEditor: mocks.switchToEditor }),
}));
vi.mock('../../../contexts/MaterialAttachmentContext', () => ({
  MAX_ATTACHED_MATERIALS: 5,
  useMaterialAttachment: () => ({ addMaterial: mocks.addMaterial, removeMaterial: vi.fn(), isMaterialAttached: () => false, isAtLimit: mocks.isAtLimit }),
}));
vi.mock('../../../lib/api', () => ({
  fileApi: { getTree: mocks.getTree, create: mocks.createFile, delete: mocks.deleteFile },
}));
vi.mock('../../../lib/toast', () => ({ toast: { error: mocks.toastError, success: vi.fn(), info: mocks.toastInfo } }));
vi.mock('../../../lib/logger', () => ({ logger: { error: mocks.loggerError } }));
vi.mock('../../../hooks/useFileSearch', () => ({
  useFileSearch: () => ({ results: mocks.results, isSearching: false, clearSearch: mocks.clearSearch }),
}));
vi.mock('../../SearchResultsDropdown', () => ({
  default: ({ onClose }: { onClose: () => void }) => <div data-testid="search-results"><button onClick={onClose}>Close results</button></div>,
}));

async function openSearch() {
  render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
  const input = await screen.findByRole('searchbox');
  fireEvent.change(input, { target: { value: 'Draft' } });
  expect(screen.getByTestId('search-results')).toBeInTheDocument();
  return input;
}

describe('FileTreePane search navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.projectId = 'project-1';
    mocks.getTree.mockResolvedValue({ tree: [{ id: 'draft-1', title: 'Draft', file_type: 'draft', children: [] }] });
  });

  it('does not focus/open search merely because a project is loaded', async () => {
    render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    expect(await screen.findByRole('searchbox')).not.toHaveFocus();
  });

  it.each([{ isComposing: true }, { keyCode: 229 }])('ignores native composition navigation %o without cancelling IME', async (flags) => {
    await openSearch();
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...flags });
    fireEvent(window, event);
    expect(event.defaultPrevented).toBe(false);
    expect(mocks.select).not.toHaveBeenCalled();
    expect(screen.getByTestId('search-results')).toBeInTheDocument();
  });

  it('only selects after composition ends and closes results', async () => {
    const input = await openSearch();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(mocks.select).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(mocks.select).toHaveBeenCalledTimes(1);
    expect(mocks.select).toHaveBeenCalledWith({ id: 'draft-1', type: 'draft', title: 'Draft' });
    expect(screen.queryByTestId('search-results')).not.toBeInTheDocument();
  });

  it.each(['escape', 'clear', 'close'])('closes results via %s', async (action) => {
    const input = await openSearch();
    if (action === 'escape') fireEvent.keyDown(input, { key: 'Escape' });
    if (action === 'clear') fireEvent.click(screen.getByRole('button', { name: 'common:clearSearch' }));
    if (action === 'close') fireEvent.click(screen.getByRole('button', { name: 'Close results' }));
    expect(screen.queryByTestId('search-results')).not.toBeInTheDocument();
  });
});

describe('FileTreePane request cancellation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.projectId = 'project-1';
  });

  it('handles the AbortError raised by an in-flight tree request on unmount', async () => {
    let requestSignal: AbortSignal | undefined;
    const abortError = new DOMException('The operation was aborted', 'AbortError');
    mocks.getTree.mockImplementation((_projectId: string, options: { signal: AbortSignal }) => {
      requestSignal = options.signal;
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(abortError), { once: true });
      });
    });
    const unhandled = vi.fn();
    window.addEventListener('unhandledrejection', unhandled);

    try {
      const view = render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
      await waitFor(() => expect(mocks.getTree).toHaveBeenCalledTimes(1));
      view.unmount();
      await waitFor(() => expect(requestSignal?.aborted).toBe(true));
      await act(async () => {
        await Promise.resolve();
      });

      expect(mocks.loggerError).not.toHaveBeenCalled();
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('unhandledrejection', unhandled);
    }
  });

  it('aborts StrictMode and project-change requests without logging their rejections', async () => {
    const requests: Array<{
      projectId: string;
      signal: AbortSignal;
      resolve: (value: { tree: Array<{ id: string; title: string; file_type: string; children: never[] }> }) => void;
    }> = [];
    mocks.getTree.mockImplementation((projectId: string, options: { signal: AbortSignal }) => {
      return new Promise((resolve, reject) => {
        requests.push({ projectId, signal: options.signal, resolve });
        options.signal.addEventListener(
          'abort',
          () => reject(new DOMException('The operation was aborted', 'AbortError')),
          { once: true },
        );
      });
    });
    const unhandled = vi.fn();
    window.addEventListener('unhandledrejection', unhandled);

    try {
      const view = render(
        <StrictMode><FileSearchProvider><FileTreePane /></FileSearchProvider></StrictMode>,
      );
      await waitFor(() => expect(requests.length).toBeGreaterThanOrEqual(2));
      expect(requests[0].signal.aborted).toBe(true);

      mocks.projectId = 'project-2';
      view.rerender(
        <StrictMode><FileSearchProvider><FileTreePane /></FileSearchProvider></StrictMode>,
      );
      await waitFor(() => expect(requests.some((request) => request.projectId === 'project-2')).toBe(true));
      const currentRequest = [...requests].reverse().find((request) => request.projectId === 'project-2');
      expect(currentRequest).toBeDefined();
      expect(requests.filter((request) => request.projectId === 'project-1').every((request) => request.signal.aborted)).toBe(true);
      await act(async () => {
        currentRequest?.resolve({
          tree: [{ id: 'project-2-file', title: 'Project Two File', file_type: 'draft', children: [] }],
        });
      });

      expect(await screen.findByText('Project Two File')).toBeInTheDocument();
      expect(mocks.loggerError).not.toHaveBeenCalled();
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('unhandledrejection', unhandled);
    }
  });

  it('does not let a stale response overwrite the current project tree', async () => {
    const pending: Array<{
      projectId: string;
      resolve: (value: { tree: Array<{ id: string; title: string; file_type: string; children: never[] }> }) => void;
    }> = [];
    mocks.getTree.mockImplementation((projectId: string) => new Promise((resolve) => {
      pending.push({ projectId, resolve });
    }));

    const view = render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    await waitFor(() => expect(pending).toHaveLength(1));
    mocks.projectId = 'project-2';
    view.rerender(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    await waitFor(() => expect(pending).toHaveLength(2));

    await act(async () => {
      pending[1].resolve({
        tree: [{ id: 'new-file', title: 'Current Project File', file_type: 'draft', children: [] }],
      });
    });
    expect(await screen.findByText('Current Project File')).toBeInTheDocument();
    await act(async () => {
      pending[0].resolve({
        tree: [{ id: 'old-file', title: 'Stale Project File', file_type: 'draft', children: [] }],
      });
      await Promise.resolve();
    });

    expect(screen.queryByText('Stale Project File')).not.toBeInTheDocument();
    expect(screen.getByText('Current Project File')).toBeInTheDocument();
    expect(mocks.loggerError).not.toHaveBeenCalled();
  });
});

describe('FileTreePane create placeholder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.projectId = 'project-1';
  });

  it('labels a new file in the script folder as a script, not a project', async () => {
    mocks.getTree.mockResolvedValue({
      tree: [{ id: 'folder-script', title: '剧本', file_type: 'folder', children: [] }],
    });
    render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    fireEvent.click(await screen.findByTitle('common:create common:fileTypes.script'));
    expect(screen.getByPlaceholderText('editor:fileTree.newScript')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('editor:fileTree.newProject')).not.toBeInTheDocument();
  });
});

describe('FileTreePane create/delete failures', () => {
  const tree = [{
    id: 'folder-characters',
    title: '角色',
    file_type: 'folder',
    children: [{ id: 'char-1', title: 'Lin Feng', file_type: 'character', children: [] }],
  }];

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.projectId = 'project-1';
    mocks.getTree.mockResolvedValue({ tree });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the createFailed toast and keeps the typed name when creating a file fails', async () => {
    mocks.createFile.mockRejectedValue(new Error('server error'));
    render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    fireEvent.click(await screen.findByTitle('common:create common:fileTypes.character'));
    const input = screen.getByPlaceholderText('editor:fileTree.newCharacter');
    fireEvent.change(input, { target: { value: '  Su Yan  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('editor:fileTree.createFailed'));
    expect(mocks.createFile).toHaveBeenCalledWith('project-1', {
      title: 'Su Yan',
      file_type: 'character',
      parent_id: 'folder-characters',
      content: '',
    });
    // The create input stays open with the author's text so they can retry.
    expect(screen.getByPlaceholderText('editor:fileTree.newCharacter')).toHaveValue('  Su Yan  ');
    expect(mocks.getTree).toHaveBeenCalledTimes(1);
  });

  it('shows the deleteFailed toast and keeps the file in the tree when deleting fails', async () => {
    const nativeConfirm = vi.fn(() => true);
    vi.stubGlobal('confirm', nativeConfirm);
    mocks.deleteFile.mockRejectedValue(new Error('server error'));
    render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    expect(await screen.findByText('Lin Feng')).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('common:delete'));

    const dialog = await screen.findByRole('dialog');
    expect(nativeConfirm).not.toHaveBeenCalled();
    expect(within(dialog).getByText('common:confirmDelete')).toBeInTheDocument();
    // The dialog names the file that is about to go.
    expect(within(dialog).getByText('Lin Feng')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'common:delete' }));

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('editor:fileTree.deleteFailed'));
    expect(mocks.deleteFile).toHaveBeenCalledWith('char-1');
    expect(mocks.select).not.toHaveBeenCalled();
    expect(screen.getByText('Lin Feng')).toBeInTheDocument();
    expect(mocks.getTree).toHaveBeenCalledTimes(1);
  });

  it('deletes only after the dialog is confirmed and keeps the file when cancelled', async () => {
    mocks.deleteFile.mockResolvedValue(undefined);
    render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    expect(await screen.findByText('Lin Feng')).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('common:delete'));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'common:cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mocks.deleteFile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTitle('common:delete'));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'common:delete' }));
    await waitFor(() => expect(mocks.deleteFile).toHaveBeenCalledWith('char-1'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(mocks.getTree).toHaveBeenCalledTimes(2));
  });
});

describe('FileTreePane material attachment limit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.projectId = 'project-1';
    mocks.isAtLimit = true;
    mocks.addMaterial.mockReturnValue(false);
    mocks.getTree.mockResolvedValue({
      tree: [{ id: 'snippet-1', title: 'Clue', file_type: 'snippet', children: [] }],
    });
  });

  afterEach(() => {
    mocks.isAtLimit = false;
    mocks.addMaterial.mockReturnValue(true);
    vi.unstubAllGlobals();
  });

  it('explains the limit in a toast instead of a native alert', async () => {
    const nativeAlert = vi.fn();
    vi.stubGlobal('alert', nativeAlert);
    render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);
    expect(await screen.findByText('Clue')).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('editor:fileTree.addToChat'));

    expect(nativeAlert).not.toHaveBeenCalled();
    expect(mocks.toastInfo).toHaveBeenCalledWith('editor:fileTree.maxMaterials');
  });
});

describe('FileTreePane reveals the selected file', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.projectId = 'project-1';
  });

  afterEach(() => {
    mocks.selectedItem = null;
  });

  it('expands nested folders down to a restored file, once', async () => {
    mocks.getTree.mockResolvedValue({
      tree: [{
        id: 'folder-drafts',
        title: '正文',
        file_type: 'folder',
        children: [{
          id: 'folder-vol-2',
          title: '第二卷',
          file_type: 'folder',
          children: [{ id: 'draft-9', title: '第九章', file_type: 'draft', children: [] }],
        }],
      }],
    });
    mocks.selectedItem = { id: 'draft-9', type: 'draft', title: '第九章' };

    render(<FileSearchProvider><FileTreePane /></FileSearchProvider>);

    expect(await screen.findByText('第九章')).toBeInTheDocument();

    // The author can still collapse the folder afterwards.
    fireEvent.click(screen.getByText('第二卷'));
    await waitFor(() => expect(screen.queryByText('第九章')).not.toBeInTheDocument());
  });
});
