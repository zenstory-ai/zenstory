import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MobileFileTree } from '../MobileFileTree';

const {
  mockSetSelectedItem,
  mockSwitchToEditor,
  mockGetTree,
  mockUpload,
  mockAddMaterial,
  mockRemoveMaterial,
  mockIsMaterialAttached,
  mockToastSuccess,
  mockToastInfo,
  mockToastError,
  mockDelete,
  mockAttachmentLimit,
  mockSelected,
} = vi.hoisted(() => ({
  mockSetSelectedItem: vi.fn(),
  mockSwitchToEditor: vi.fn(),
  mockGetTree: vi.fn(),
  mockUpload: vi.fn(),
  mockAddMaterial: vi.fn(() => true),
  mockRemoveMaterial: vi.fn(),
  mockIsMaterialAttached: vi.fn(() => false),
  mockToastSuccess: vi.fn(),
  mockToastInfo: vi.fn(),
  mockToastError: vi.fn(),
  mockDelete: vi.fn(),
  mockAttachmentLimit: { value: false },
  mockSelected: { value: null as { id: string; type: string; title: string } | null },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: 'project-1',
    selectedItem: mockSelected.value,
    setSelectedItem: mockSetSelectedItem,
    fileTreeVersion: 0,
  }),
}));

vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({
    switchToEditor: mockSwitchToEditor,
  }),
}));

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  MAX_ATTACHED_MATERIALS: 5,
  useMaterialAttachment: () => ({
    addMaterial: mockAddMaterial,
    removeMaterial: mockRemoveMaterial,
    isMaterialAttached: mockIsMaterialAttached,
    get isAtLimit() {
      return mockAttachmentLimit.value;
    },
  }),
}));

vi.mock('../../lib/toast', () => ({
  toast: {
    success: mockToastSuccess,
    info: mockToastInfo,
    error: mockToastError,
  },
}));

vi.mock('../../lib/api', () => ({
  fileApi: {
    getTree: (...args: unknown[]) => mockGetTree(...args),
    upload: (...args: unknown[]) => mockUpload(...args),
    create: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

vi.mock('../SearchResultsDropdown', () => ({
  default: () => null,
  SearchResultsDropdown: () => null,
}));

vi.mock('../FileSearchInput', () => ({
  FileSearchInput: ({
    value,
    onChange,
    onFocus,
    onBlur,
    onKeyDown,
    placeholder,
  }: {
    value: string;
    onChange: (v: string) => void;
    onFocus?: () => void;
    onBlur?: () => void;
    onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
    placeholder?: string;
  }) => (
    <input
      data-testid="search-input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onFocus={onFocus}
      onBlur={onBlur}
      onKeyDown={onKeyDown}
      placeholder={placeholder}
    />
  ),
}));

describe('MobileFileTree', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAttachmentLimit.value = false;
    mockSelected.value = null;
    mockAddMaterial.mockReturnValue(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows upload entry on material folder and uploads txt file', async () => {
    mockGetTree.mockResolvedValue({
      tree: [
        {
          id: 'material-folder-id',
          title: '素材',
          file_type: 'folder',
          parent_id: null,
          order: 0,
          content: '',
          metadata: null,
          children: [],
        },
      ],
    });
    mockUpload.mockResolvedValue({
      id: 'snippet-1',
    });

    render(<MobileFileTree />);

    const uploadButton = await screen.findByTitle('editor:fileTree.uploadMaterial');
    expect(uploadButton).toBeInTheDocument();

    const inputClickSpy = vi.spyOn(HTMLInputElement.prototype, 'click');
    fireEvent.click(uploadButton);
    expect(inputClickSpy).toHaveBeenCalled();
    inputClickSpy.mockRestore();

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['line1\nline2'], 'material.txt', { type: 'text/plain' });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(mockUpload).toHaveBeenCalledWith('project-1', file);
    });
    expect(mockToastSuccess).toHaveBeenCalledWith('editor:fileTree.uploadSuccess');
  });

  it('names the folder create button with the localized file type, not the internal id', async () => {
    mockGetTree.mockResolvedValue({
      tree: [
        {
          id: 'character-folder-id',
          title: '角色',
          file_type: 'folder',
          parent_id: null,
          order: 0,
          content: '',
          metadata: null,
          children: [],
        },
      ],
    });

    render(<MobileFileTree />);

    expect(
      await screen.findByRole('button', { name: 'common:create common:fileTypes.character' }),
    ).toBeInTheDocument();
    expect(screen.queryByTitle('common:create character')).not.toBeInTheDocument();
  });

  it('adds snippet to chat context when clicking add button', async () => {
    mockIsMaterialAttached.mockReturnValue(false);
    mockGetTree.mockResolvedValue({
      tree: [
        {
          id: 'snippet-1',
          title: '片段A',
          file_type: 'snippet',
          parent_id: null,
          order: 0,
          content: '',
          metadata: null,
          children: [],
        },
      ],
    });

    render(<MobileFileTree />);

    const addButton = await screen.findByTitle('editor:fileTree.addToChat');
    fireEvent.click(addButton);

    expect(mockAddMaterial).toHaveBeenCalledWith('snippet-1', '片段A');
  });
  it('confirms file deletion in an in-app dialog instead of window.confirm', async () => {
    const nativeConfirm = vi.fn(() => true);
    vi.stubGlobal('confirm', nativeConfirm);
    mockDelete.mockResolvedValue(undefined);
    mockGetTree.mockResolvedValue({
      tree: [
        {
          id: 'draft-1',
          title: '第一章',
          file_type: 'draft',
          parent_id: null,
          order: 0,
          content: '',
          metadata: null,
          children: [],
        },
      ],
    });

    render(<MobileFileTree />);

    fireEvent.click(await screen.findByTitle('common:delete'));
    let dialog = await screen.findByRole('dialog');
    expect(nativeConfirm).not.toHaveBeenCalled();
    expect(within(dialog).getByText('common:confirmDelete')).toBeInTheDocument();
    expect(within(dialog).getByText('第一章')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'common:cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTitle('common:delete'));
    dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'common:delete' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('draft-1'));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockSetSelectedItem).not.toHaveBeenCalled();
  });

  it('explains the material limit in a toast instead of a native alert', async () => {
    const nativeAlert = vi.fn();
    vi.stubGlobal('alert', nativeAlert);
    mockAttachmentLimit.value = true;
    mockAddMaterial.mockReturnValue(false);
    mockGetTree.mockResolvedValue({
      tree: [
        {
          id: 'snippet-1',
          title: '片段A',
          file_type: 'snippet',
          parent_id: null,
          order: 0,
          content: '',
          metadata: null,
          children: [],
        },
      ],
    });

    render(<MobileFileTree />);
    fireEvent.click(await screen.findByTitle('editor:fileTree.addToChat'));

    expect(nativeAlert).not.toHaveBeenCalled();
    expect(mockToastInfo).toHaveBeenCalledWith('editor:fileTree.maxMaterials');
  });
  it('expands nested folders down to a restored file', async () => {
    mockSelected.value = { id: 'draft-9', type: 'draft', title: '第九章' };
    mockGetTree.mockResolvedValue({
      tree: [
        {
          id: 'folder-drafts',
          title: '正文',
          file_type: 'folder',
          parent_id: null,
          order: 0,
          content: '',
          metadata: null,
          children: [
            {
              id: 'folder-vol-2',
              title: '第二卷',
              file_type: 'folder',
              parent_id: 'folder-drafts',
              order: 0,
              content: '',
              metadata: null,
              children: [
                {
                  id: 'draft-9',
                  title: '第九章',
                  file_type: 'draft',
                  parent_id: 'folder-vol-2',
                  order: 0,
                  content: '',
                  metadata: null,
                  children: [],
                },
              ],
            },
          ],
        },
      ],
    });

    render(<MobileFileTree />);

    expect(await screen.findByText('第九章')).toBeInTheDocument();
  });
});
