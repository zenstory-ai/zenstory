import { act, fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import type { RenderOptions } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SimpleEditor } from '../SimpleEditor';
import { MobileLayoutProvider, useMobileLayout } from '../../contexts/MobileLayoutContext';
import { toast } from '../../lib/toast';
import type { DiffReviewState } from '../../types';
import {
  BEFORE_CHUNK_RELOAD_EVENT,
  readEditorDraftSnapshot,
} from '../../lib/editorDraftRecovery';
import { setOpenEditorFlush } from '../../lib/editorSaveTracker';

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'test-user'],
    quota: () => ['subscription-quota', 'test-user'],
    quotaLite: () => ['quota', 'test-user'],
  },
}));

let testQueryClient: QueryClient;
const QueryWrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={testQueryClient}>{children}</QueryClientProvider>
);
const render = (ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) =>
  rtlRender(ui, { wrapper: QueryWrapper, ...options });

const quotaPayload = (limit: number) => ({
  ai_conversations: { used: 2, limit, remaining: limit === -1 ? -1 : limit - 2, reset_at: null },
});

vi.mock('../../contexts/TextQuoteContext', () => ({
  useTextQuote: () => ({
    addQuote: vi.fn(),
  }),
}));

vi.mock('../../hooks/useGestures', () => ({
  usePinchZoom: () => ({
    zoom: 1,
    bind: () => ({}),
    resetZoom: vi.fn(),
  }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string | Record<string, unknown>) =>
      typeof fallback === 'string'
        ? fallback
        : fallback
          ? `${key}:${JSON.stringify(fallback)}`
          : key,
  }),
}));

vi.mock('../FileVersionHistory', () => ({
  FileVersionHistory: () => null,
}));

vi.mock('../InlineDiffEditor', () => ({
  InlineDiffEditor: () => null,
}));

vi.mock('../DiffToolbar', () => ({
  DiffToolbar: () => null,
}));

vi.mock('../DiffReviewSplitView', () => ({
  DiffReviewSplitView: () => <div data-testid="paragraph-review" />,
}));

vi.mock('../SelectionToolbar', () => ({
  // Stand-in for the floating 「引用到对话」 toolbar: just the action, no positioning.
  SelectionToolbar: ({ onAdd }: { onAdd: () => void }) => (
    <button type="button" onClick={onAdd}>quote-to-chat</button>
  ),
}));

describe('SimpleEditor', () => {
  const reviewState = (origin: DiffReviewState['origin']): DiffReviewState => ({
    isReviewing: true,
    fileId: 'file-1',
    originalContent: 'Original paragraph.\n\nSecond original paragraph.',
    modifiedContent: 'Polished paragraph.\n\nSecond polished paragraph.',
    pendingEdits: [{
      id: 'edit-0',
      op: 'replace',
      oldText: 'Original paragraph.',
      newText: 'Polished paragraph.',
      status: 'pending',
    }],
    origin,
  });

  it('keeps save conflicts in the paragraph comparison', () => {
    render(
      <SimpleEditor
        fileId="file-1"
        title="Draft"
        content="Original paragraph.\n\nSecond original paragraph."
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn()}
        diffReviewState={reviewState('conflict')}
        onAcceptEdit={vi.fn()}
        onRejectEdit={vi.fn()}
        onResetEdit={vi.fn()}
      />,
    );

    expect(screen.getByTestId('paragraph-review')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'editor:naturalPolishCandidate' })).not.toBeInTheDocument();
  });

  it('shows successful save status after StrictMode effect replay', async () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const Harness = () => {
      const [content, setContent] = useState('Original');
      return <SimpleEditor fileId="strict-file" title="Draft" content={content} onTitleChange={vi.fn()} onContentChange={setContent} onSave={onSave} />;
    };
    render(<StrictMode><Harness /></StrictMode>);
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), { target: { value: 'Edited content' } });
    fireEvent.click(screen.getByRole('button', { name: 'editor:save' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument());
    expect(screen.getByText('editor:savedJustNow')).toBeInTheDocument();
  });

  const mockRaf = () =>
    vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((cb: FrameRequestCallback) => {
        cb(0);
        return 0;
      });

  const selectText = (textarea: HTMLTextAreaElement) => {
    textarea.focus();
    textarea.setSelectionRange(0, 5);
    fireEvent.select(textarea);
    fireEvent.mouseUp(textarea);
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    testQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('captures the latest dirty draft synchronously before a chunk reload', () => {
    const Harness = () => {
      const [content, setContent] = useState('Server body');
      return (
        <SimpleEditor
          userId="user-1"
          projectId="project-1"
          fileId="file-1"
          baseUpdatedAt="server-v1"
          title="Chapter"
          content={content}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={vi.fn()}
        />
      );
    };
    render(<Harness />);

    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.titlePlaceholder'), {
      target: { value: 'Latest unsaved title' },
    });
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Latest unsaved body' },
    });
    const captureEvent = new CustomEvent(BEFORE_CHUNK_RELOAD_EVENT, { cancelable: true });
    window.dispatchEvent(captureEvent);

    expect(readEditorDraftSnapshot(localStorage, {
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
    })).toMatchObject({
      title: 'Latest unsaved title',
      content: 'Latest unsaved body',
      baseUpdatedAt: 'server-v1',
      reason: 'chunk-reload',
    });
    expect(captureEvent.defaultPrevented).toBe(false);
  });

  it('captures the latest dirty draft synchronously on ordinary page exit', () => {
    const Harness = () => {
      const [content, setContent] = useState('Server body');
      return (
        <SimpleEditor
          userId="user-1"
          projectId="project-1"
          fileId="file-1"
          baseUpdatedAt="server-v1"
          title="Chapter"
          content={content}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={vi.fn()}
        />
      );
    };
    render(<Harness />);
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Typed immediately before refresh' },
    });

    window.dispatchEvent(new Event('beforeunload'));

    expect(readEditorDraftSnapshot(localStorage, {
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
    })).toMatchObject({
      content: 'Typed immediately before refresh',
      reason: 'page-exit',
    });
  });

  it('does not create a recovery snapshot when the editor is clean', () => {
    render(
      <SimpleEditor
        userId="user-1"
        projectId="project-1"
        fileId="file-1"
        title="Chapter"
        content="Server body"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );

    window.dispatchEvent(new CustomEvent(BEFORE_CHUNK_RELOAD_EVENT));

    expect(readEditorDraftSnapshot(localStorage, {
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
    })).toBeNull();
  });

  it('keeps editing available when recovery storage cannot be written', () => {
    const storageSpy = vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    const Harness = () => {
      const [content, setContent] = useState('Server body');
      return (
        <SimpleEditor
          userId="user-1"
          projectId="project-1"
          fileId="file-1"
          title="Chapter"
          content={content}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={vi.fn()}
        />
      );
    };
    render(<Harness />);
    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder');
    fireEvent.change(textarea, { target: { value: 'Still editable' } });

    const captureEvent = new CustomEvent(BEFORE_CHUNK_RELOAD_EVENT, { cancelable: true });
    expect(() => window.dispatchEvent(captureEvent)).not.toThrow();
    expect(captureEvent.defaultPrevented).toBe(true);
    expect(textarea).toHaveValue('Still editable');
    expect(storageSpy).toHaveBeenCalled();
  });

  it('requests native leave confirmation only when beforeunload snapshot capture fails', () => {
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });
    const Harness = () => {
      const [content, setContent] = useState('Server body');
      return (
        <SimpleEditor
          userId="user-1"
          projectId="project-1"
          fileId="file-1"
          title="Chapter"
          content={content}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={vi.fn()}
        />
      );
    };
    render(<Harness />);
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Unsaved body without local storage' },
    });
    const beforeUnload = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent;

    window.dispatchEvent(beforeUnload);

    expect(beforeUnload.defaultPrevented).toBe(true);
  });

  it('writes only one snapshot when chunk recovery proceeds into beforeunload', () => {
    const setItemSpy = vi.spyOn(localStorage, 'setItem');
    const Harness = () => {
      const [content, setContent] = useState('Server body');
      return (
        <SimpleEditor
          userId="user-1"
          projectId="project-1"
          fileId="file-1"
          title="Chapter"
          content={content}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={vi.fn()}
        />
      );
    };
    render(<Harness />);
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Latest body' },
    });

    window.dispatchEvent(new CustomEvent(BEFORE_CHUNK_RELOAD_EVENT, { cancelable: true }));
    window.dispatchEvent(new Event('beforeunload', { cancelable: true }));
    window.dispatchEvent(new Event('pagehide'));

    expect(setItemSpy).toHaveBeenCalledTimes(1);
    expect(readEditorDraftSnapshot(localStorage, {
      userId: 'user-1', projectId: 'project-1', fileId: 'file-1',
    })).toMatchObject({ reason: 'chunk-reload' });

    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Edited again after a canceled exit' },
    });
    window.dispatchEvent(new Event('beforeunload', { cancelable: true }));
    expect(setItemSpy).toHaveBeenCalledTimes(2);
    expect(readEditorDraftSnapshot(localStorage, {
      userId: 'user-1', projectId: 'project-1', fileId: 'file-1',
    })).toMatchObject({ content: 'Edited again after a canceled exit', reason: 'page-exit' });
  });

  it('restores a recovered draft as dirty without automatically saving it', async () => {
    vi.useFakeTimers();
    const onSave = vi.fn().mockResolvedValue({
      outcome: 'saved',
      updatedAt: 'server-v2',
    });
    const Harness = () => {
      const [content, setContent] = useState('Recovered body');
      return (
        <SimpleEditor
          userId="user-1"
          projectId="project-1"
          fileId="file-1"
          baseUpdatedAt="server-v1"
          title="Recovered chapter"
          content={content}
          recoveredDraft={{
            capturedAt: '2026-10-07T08:00:00.000Z',
            serverTitle: 'Server chapter',
            serverContent: 'Server body',
          }}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={onSave}
        />
      );
    };
    render(<Harness />);

    expect(screen.getByText('editor:unsaved')).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Recovered body edited again' },
    });
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(onSave).toHaveBeenCalledOnce();
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      content: 'Recovered body edited again',
      baseUpdatedAt: 'server-v1',
    }));
  });

  it.each([false, true])('keeps a restored draft local on unmount without saving it (pagehide: %s)', async (pagehide) => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 'server-v3' });
    const { unmount } = render(
      <SimpleEditor
        userId="user-1"
        projectId="project-1"
        fileId="file-1"
        baseUpdatedAt="newer-server-v2"
        title="Recovered chapter"
        content="Recovered body chosen after comparing the newer server version"
        recoveredDraft={{
          capturedAt: '2026-10-07T08:00:00.000Z',
          serverTitle: 'Newer server chapter',
          serverContent: 'Newer server body',
        }}
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={onSave}
        onFlushReady={vi.fn()}
      />,
    );

    if (pagehide) window.dispatchEvent(new Event('pagehide'));
    unmount();
    await act(async () => Promise.resolve());

    expect(onSave).not.toHaveBeenCalled();
    expect(readEditorDraftSnapshot(localStorage, {
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
    })).toMatchObject({
      title: 'Recovered chapter',
      content: 'Recovered body chosen after comparing the newer server version',
      baseUpdatedAt: 'newer-server-v2',
      reason: 'page-exit',
    });
  });

  it('does not report a restored draft as server-saved on flush but still permits explicit save', async () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 'server-v3' });
    const onFlushReady = vi.fn();
    render(
      <SimpleEditor
        userId="user-1"
        projectId="project-1"
        fileId="file-1"
        baseUpdatedAt="newer-server-v2"
        title="Recovered chapter"
        content="Recovered body"
        recoveredDraft={{
          capturedAt: '2026-10-07T08:00:00.000Z',
          serverTitle: 'Newer server chapter',
          serverContent: 'Newer server body',
        }}
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={onSave}
        onFlushReady={onFlushReady}
      />,
    );

    await act(async () => {
      expect(await onFlushReady.mock.calls.at(-1)?.[0]()).toBe('failed');
    });
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText('editor:unsaved')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'editor:save' }));
    await waitFor(() => expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument());
    expect(onSave).toHaveBeenCalledOnce();
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      content: 'Recovered body',
      baseUpdatedAt: 'newer-server-v2',
    }));
  });

  it('returns to a clean server baseline when a recovered draft is discarded', () => {
    const onSave = vi.fn();
    const commonProps = {
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
      baseUpdatedAt: 'server-v1',
      onTitleChange: vi.fn(),
      onContentChange: vi.fn(),
      onSave,
    };
    const { rerender } = render(
      <SimpleEditor
        {...commonProps}
        title="Recovered chapter"
        content="Recovered body"
        recoveredDraft={{
          capturedAt: '2026-10-07T08:00:00.000Z',
          serverTitle: 'Server chapter',
          serverContent: 'Server body',
        }}
      />,
    );
    expect(screen.getByText('editor:unsaved')).toBeInTheDocument();

    rerender(
      <SimpleEditor
        {...commonProps}
        title="Server chapter"
        content="Server body"
      />,
    );

    expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'editor:save' })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('shows natural polish action without admin gating', () => {
    render(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="Hello world"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' })}
      />
    );

    expect(screen.getByRole('button', { name: 'editor:naturalPolish' })).toBeInTheDocument();
  });

  const renderPolishableEditor = (
    onNaturalPolish = vi.fn(),
    content = 'Hello world',
    extraProps: Partial<React.ComponentProps<typeof SimpleEditor>> = {},
  ) => {
    render(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content={content}
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' })}
        onNaturalPolish={onNaturalPolish}
        {...extraProps}
      />
    );
    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
    return textarea;
  };

  const renderMobilePolishableEditor = (
    onNaturalPolish = vi.fn(),
    onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' }),
  ) => {
    const PanelTabs = () => {
      const { activePanel, switchToChat, switchToEditor } = useMobileLayout();
      return (
        <>
          <span data-testid="active-mobile-panel">{activePanel}</span>
          <button type="button" onClick={switchToChat}>tab-chat</button>
          <button type="button" onClick={switchToEditor}>tab-editor</button>
        </>
      );
    };
    render(
      <MobileLayoutProvider isMobile>
        <PanelTabs />
        <SimpleEditor
          projectId="project-1"
          fileId="file-1"
          fileType="draft"
          title="File 1"
          content="Hello world"
          onTitleChange={vi.fn()}
          onContentChange={vi.fn()}
          onSave={onSave}
          onNaturalPolish={onNaturalPolish}
        />
      </MobileLayoutProvider>,
    );
    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
    return { textarea };
  };

  it('polishes the whole open chapter via Agent when nothing is selected (no 6,000-char wall)', async () => {
    const onNaturalPolish = vi.fn();
    const longChapter = '文'.repeat(7500);
    renderPolishableEditor(onNaturalPolish, longChapter);

    const button = screen.getByRole('button', { name: 'editor:naturalPolish' });
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute('title', 'editor:naturalPolishTooltip');
    fireEvent.click(button);

    await waitFor(() => {
      expect(onNaturalPolish).toHaveBeenCalledWith({
        projectId: 'project-1',
        fileId: 'file-1',
        fileType: 'draft',
        fileTitle: 'File 1',
        selectedText: undefined,
      });
    });
  });

  it('shows selection scope label and sends selectedText when text is highlighted (click and Ctrl/Cmd+Shift+R)', async () => {
    const onNaturalPolish = vi.fn();
    const textarea = renderPolishableEditor(onNaturalPolish);
    selectText(textarea);

    const button = screen.getByRole('button', { name: 'editor:naturalPolishSelection' });
    expect(button).toHaveAttribute('title', 'editor:naturalPolishTooltipSelection');
    fireEvent.keyDown(textarea, { key: 'R', code: 'KeyR', ctrlKey: true, shiftKey: true });

    await waitFor(() => {
      expect(onNaturalPolish).toHaveBeenCalledWith({
        projectId: 'project-1',
        fileId: 'file-1',
        fileType: 'draft',
        fileTitle: 'File 1',
        selectedText: 'Hello',
      });
    });
    expect(textarea.selectionStart).toBe(textarea.selectionEnd);
  });

  it('flushes dirty edits BEFORE dispatching natural polish to Agent', async () => {
    const order: string[] = [];
    const onSave = vi.fn(async () => {
      order.push('save');
      return { outcome: 'saved' as const, updatedAt: '2026-10-06T10:00:00.000002' };
    });
    const onNaturalPolish = vi.fn(() => {
      order.push('polish');
    });
    const Harness = () => {
      const [content, setContent] = useState('Hello world');
      return (
        <SimpleEditor
          projectId="project-1"
          fileId="file-1"
          fileType="draft"
          title="File 1"
          content={content}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={onSave}
          onNaturalPolish={onNaturalPolish}
        />
      );
    };
    render(<Harness />);
    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Hello updated draft' } });

    fireEvent.click(screen.getByRole('button', { name: 'editor:naturalPolish' }));

    await waitFor(() => expect(onNaturalPolish).toHaveBeenCalledTimes(1));
    expect(order).toEqual(['save', 'polish']);
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ content: 'Hello updated draft' }));
  });

  it('aborts natural polish and shows an error toast when dirty-draft flush fails', async () => {
    const toastErrorSpy = vi.spyOn(toast, 'error').mockImplementation(() => {});
    const onNaturalPolish = vi.fn();
    const flushHook = vi.fn().mockResolvedValue('failed');
    setOpenEditorFlush(flushHook, undefined, () => 'file-1');
    try {
      renderPolishableEditor(onNaturalPolish);
      fireEvent.click(screen.getByRole('button', { name: 'editor:naturalPolish' }));

      await waitFor(() => expect(toastErrorSpy).toHaveBeenCalledWith('editor:naturalPolishSaveFailed'));
      expect(onNaturalPolish).not.toHaveBeenCalled();
    } finally {
      setOpenEditorFlush(null, flushHook);
      toastErrorSpy.mockRestore();
    }
  });

  it('aborts natural polish silently on 409 version conflict so Editor conflict review stays clean', async () => {
    const toastErrorSpy = vi.spyOn(toast, 'error').mockImplementation(() => {});
    const onNaturalPolish = vi.fn();
    const flushHook = vi.fn().mockResolvedValue('conflict');
    setOpenEditorFlush(flushHook, undefined, () => 'file-1');
    try {
      renderPolishableEditor(onNaturalPolish);
      fireEvent.click(screen.getByRole('button', { name: 'editor:naturalPolish' }));

      await waitFor(() => expect(flushHook).toHaveBeenCalledTimes(1));
      expect(onNaturalPolish).not.toHaveBeenCalled();
      expect(toastErrorSpy).not.toHaveBeenCalled();
    } finally {
      setOpenEditorFlush(null, flushHook);
      toastErrorSpy.mockRestore();
    }
  });

  it('disables natural polish when file is empty, streaming, chat streaming, or read-only', () => {
    const { rerender } = render(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="   "
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'editor:naturalPolish' })).toBeDisabled();

    rerender(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="Hello world"
        isStreaming
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'editor:naturalPolish' })).toBeDisabled();

    rerender(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="Hello world"
        isChatStreaming
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'editor:naturalPolish' })).toBeDisabled();

    rerender(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="Hello world"
        readOnly
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'editor:naturalPolish' })).toBeDisabled();
  });

  it('switches to the AI chat tab on mobile after dispatching natural polish, and clears stale selection on panel switch', async () => {
    const onNaturalPolish = vi.fn();
    const { textarea } = renderMobilePolishableEditor(onNaturalPolish);
    selectText(textarea);

    // Leaving the editor tab clears stale selection so returning does not polish invisible text.
    fireEvent.click(screen.getByRole('button', { name: 'tab-chat' }));
    fireEvent.click(screen.getByRole('button', { name: 'tab-editor' }));
    expect(textarea.selectionStart).toBe(textarea.selectionEnd);

    fireEvent.click(screen.getByRole('button', { name: 'editor:naturalPolish' }));

    await waitFor(() => expect(onNaturalPolish).toHaveBeenCalledWith(expect.objectContaining({
      fileId: 'file-1',
      selectedText: undefined,
    })));
    expect(screen.getByTestId('active-mobile-panel')).toHaveTextContent('chat');
  });

  it('quoting a selection to chat clears the selection scope back to full chapter', async () => {
    const onNaturalPolish = vi.fn();
    const textarea = renderPolishableEditor(onNaturalPolish);
    selectText(textarea);
    expect(screen.getByRole('button', { name: 'editor:naturalPolishSelection' })).toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: 'quote-to-chat' }));
    expect(screen.queryByRole('button', { name: 'quote-to-chat' })).not.toBeInTheDocument();
    expect(textarea.selectionStart).toBe(textarea.selectionEnd);
    expect(screen.getByRole('button', { name: 'editor:naturalPolish' })).toBeInTheDocument();
  });

  it('tells free users that natural polish uses one of today\'s AI messages', async () => {
    renderPolishableEditor();
    const polishButton = screen.getByRole('button', { name: 'editor:naturalPolish' });
    expect(polishButton).toHaveAttribute('title', 'editor:naturalPolishTooltip');

    // QuotaBadge 拉到额度后，提示跟着缓存更新，不需要编辑器自己再请求。
    act(() => {
      testQueryClient.setQueryData(['subscription-quota', 'test-user'], quotaPayload(3));
    });

    await waitFor(() => {
      expect(polishButton).toHaveAttribute('title', 'editor:naturalPolishTooltipFree');
    });
    // Cost stays in the hover tooltip instead of cluttering the compact editor footer button.
    expect(polishButton).not.toHaveTextContent('editor:naturalPolishCost');
  });

  it('keeps the plain natural polish tooltip for unlimited plans', () => {
    testQueryClient.setQueryData(['subscription-quota', 'test-user'], quotaPayload(-1));

    renderPolishableEditor();

    expect(screen.getByRole('button', { name: 'editor:naturalPolish' })).toHaveAttribute(
      'title',
      'editor:naturalPolishTooltip',
    );
  });

  it('labels the footer history button with a string title', () => {
    renderPolishableEditor();

    expect(screen.getByRole('button', { name: 'editor:history' })).toHaveAttribute('title', 'versions:title');
  });

  it('counts one paragraph per line, as Chinese manuscripts are written', () => {
    renderPolishableEditor(vi.fn(), '　　灯芯烧得只剩一指长。\n　　三年来，他头一回觉得心里有底。\n\n　　门被轻轻叩了两下。\n');

    expect(screen.getByTestId('editor-paragraph-count')).toHaveTextContent('3');
  });

  it('keeps the word count explanation as a quiet hover title without a click toast', () => {
    const toastInfoSpy = vi.spyOn(toast, 'info').mockImplementation(() => {});
    renderPolishableEditor();

    const wordCount = screen.getByTestId('editor-word-count');
    expect(wordCount.tagName).toBe('SPAN');
    expect(wordCount).toHaveAttribute('title', 'editor:wordCountHint');
    fireEvent.click(wordCount);
    expect(toastInfoSpy).not.toHaveBeenCalled();
    toastInfoSpy.mockRestore();
  });

  it('resets dirty state when switching files', () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const onTitleChange = vi.fn();
    const onContentChange = vi.fn();

    const { rerender } = render(
      <SimpleEditor
        fileId="file-1"
        title="File 1"
        content="Original content from file one"
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
      />
    );

    const contentTextarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder');
    fireEvent.change(contentTextarea, {
      target: { value: 'Original content from file one with edits' },
    });

    expect(screen.getByText('editor:unsaved')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'editor:save' })).toBeEnabled();

    rerender(
      <SimpleEditor
        fileId="file-2"
        title="File 2"
        content="Another file content"
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
      />
    );

    expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'editor:save' })).toBeDisabled();
  });

  it('keeps the dirty baseline when save reports a conflict', async () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'conflict' });
    render(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="Original content"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={onSave}
      />,
    );

    fireEvent.change(
      screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'),
      { target: { value: 'Local content that has not been persisted' } },
    );
    const saveButton = screen.getByRole('button', { name: 'editor:save' });
    expect(screen.getByText('editor:unsaved')).toBeInTheDocument();
    fireEvent.click(saveButton);

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(saveButton).toBeEnabled());
    expect(screen.getByText('editor:unsaved')).toBeInTheDocument();
  });

  it('does not create content version when only title changes after async file switch', async () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const onTitleChange = vi.fn();
    const onContentChange = vi.fn();

    const { rerender } = render(
      <SimpleEditor
        fileId="file-1"
        title="File 1"
        content="11111111111111111111"
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
      />
    );

    // Simulate file switch before async content finishes loading.
    rerender(
      <SimpleEditor
        fileId="file-2"
        title="File 2"
        content="11111111111111111111"
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
      />
    );

    // Async content update for the newly selected file.
    rerender(
      <SimpleEditor
        fileId="file-2"
        title="File 2"
        content="BBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
      />
    );

    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.titlePlaceholder'), {
      target: { value: 'File 2 - Renamed' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'editor:save' }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledTimes(1);
    });
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        fileId: 'file-2',
        title: 'File 2 - Renamed',
        content: 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBB',
        versionIntent: undefined,
      }),
    );
  });

  it('uses outer container scrolling and hides textarea inner scrollbar', () => {
    render(
      <SimpleEditor
        fileId="file-1"
        title="File 1"
        content={'line\n'.repeat(200)}
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' })}
      />
    );

    const contentTextarea = screen.getByPlaceholderText(
      'editor:placeholder.contentPlaceholder'
    );

    expect(contentTextarea.className).toContain('overflow-y-hidden');
  });

  it('keeps the active textarea height stable while typing and only shrinks after blur', () => {
    render(
      <SimpleEditor
        fileId="file-height"
        title="File 1"
        content={'line\n'.repeat(40)}
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' })}
      />
    );

    const contentTextarea = screen.getByPlaceholderText(
      'editor:placeholder.contentPlaceholder'
    ) as HTMLTextAreaElement;

    contentTextarea.style.height = '600px';

    const measuredHeight = 320;
    Object.defineProperty(contentTextarea, 'scrollHeight', {
      configurable: true,
      get: () => {
        if (contentTextarea.style.height === '0px') {
          return measuredHeight;
        }
        const currentHeight = Number.parseFloat(contentTextarea.style.height || '0');
        return Math.max(currentHeight, measuredHeight);
      },
    });

    contentTextarea.focus();
    fireEvent.change(contentTextarea, {
      target: { value: 'Shorter content after deleting a lot of text.' },
    });

    expect(contentTextarea.style.height).toBe('600px');

    fireEvent.blur(contentTextarea);

    expect(contentTextarea.style.height).toBe(`${measuredHeight}px`);
  });

  it('does not force auto-scroll to bottom when user scrolled away during streaming', async () => {
    const rafSpy = mockRaf();

    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const onTitleChange = vi.fn();
    const onContentChange = vi.fn();

    const { container, rerender } = render(
      <SimpleEditor
        fileId="file-1"
        title="File 1"
        content={'line\n'.repeat(100)}
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
        isStreaming
      />
    );

    const scrollContainer = container.querySelector('div.flex-1.overflow-auto') as HTMLDivElement;
    expect(scrollContainer).toBeTruthy();

    Object.defineProperty(scrollContainer, 'scrollHeight', {
      value: 2000,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(scrollContainer, 'clientHeight', {
      value: 500,
      writable: true,
      configurable: true,
    });
    scrollContainer.scrollTop = 1000; // far from bottom (not "near bottom")
    fireEvent.scroll(scrollContainer);

    rerender(
      <SimpleEditor
        fileId="file-1"
        title="File 1"
        content={'line\n'.repeat(120)}
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
        isStreaming
      />
    );

    await waitFor(() => {
      expect(scrollContainer.scrollTop).toBe(1000);
    });

    rafSpy.mockRestore();
  });

  it('auto-scrolls to bottom during streaming when already near bottom', async () => {
    const rafSpy = mockRaf();

    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const onTitleChange = vi.fn();
    const onContentChange = vi.fn();

    const { container, rerender } = render(
      <SimpleEditor
        fileId="file-1"
        title="File 1"
        content={'line\n'.repeat(100)}
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
        isStreaming
      />
    );

    const scrollContainer = container.querySelector('div.flex-1.overflow-auto') as HTMLDivElement;
    expect(scrollContainer).toBeTruthy();

    Object.defineProperty(scrollContainer, 'scrollHeight', {
      value: 2000,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(scrollContainer, 'clientHeight', {
      value: 500,
      writable: true,
      configurable: true,
    });
    scrollContainer.scrollTop = 1480; // near bottom: 2000 - 1480 - 500 = 20
    fireEvent.scroll(scrollContainer);

    rerender(
      <SimpleEditor
        fileId="file-1"
        title="File 1"
        content={'line\n'.repeat(120)}
        onTitleChange={onTitleChange}
        onContentChange={onContentChange}
        onSave={onSave}
        isStreaming
      />
    );

    await waitFor(() => {
      expect(scrollContainer.scrollTop).toBe(2000);
    });

    rafSpy.mockRestore();
  });

  it('keeps edits made during a deferred save dirty and saves them afterward', async () => {
    vi.useFakeTimers();
    let resolveFirstSave: (outcome: { outcome: 'saved'; updatedAt: string }) => void = () => {};
    const firstSave = new Promise<{ outcome: 'saved'; updatedAt: string }>((resolve) => {
      resolveFirstSave = resolve;
    });
    const onSave = vi
      .fn()
      .mockReturnValueOnce(firstSave)
      .mockResolvedValueOnce({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });

    const Harness = () => {
      const [content, setContent] = useState('Original');
      return (
        <SimpleEditor
          fileId="file-1"
          title="File 1"
          content={content}
          onTitleChange={vi.fn()}
          onContentChange={setContent}
          onSave={onSave}
        />
      );
    };

    render(<Harness />);
    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder');
    fireEvent.change(textarea, { target: { value: 'Submitted A' } });
    fireEvent.click(screen.getByRole('button', { name: 'editor:save' }));
    await act(async () => Promise.resolve());
    expect(onSave).toHaveBeenCalledTimes(1);

    fireEvent.change(textarea, { target: { value: 'Typed B while saving' } });
    await act(async () => resolveFirstSave({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(onSave).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it('resolves a queued save version and base token against the preceding successful save', async () => {
    let resolveFirstSave: (outcome: { outcome: 'saved'; updatedAt: string }) => void = () => {};
    const firstSave = new Promise<{ outcome: 'saved'; updatedAt: string }>((resolve) => { resolveFirstSave = resolve; });
    const onSave = vi.fn().mockReturnValueOnce(firstSave).mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const Harness = () => {
      const [content, setContent] = useState('Old text');
      return <SimpleEditor projectId="project-1" fileId="file-1" fileType="draft"
        title="Draft" content={content} onTitleChange={vi.fn()}
        onContentChange={setContent} onSave={onSave} />;
    };
    render(<Harness />);
    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder');
    fireEvent.change(textarea, { target: { value: 'First saved text' } });
    fireEvent.keyDown(textarea, { key: 's', ctrlKey: true });
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    fireEvent.change(textarea, { target: { value: 'First saved text plus' } });
    fireEvent.keyDown(textarea, { key: 's', ctrlKey: true });
    expect(onSave).toHaveBeenCalledTimes(1);
    await act(async () => resolveFirstSave({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
    expect(onSave.mock.calls[0][0]).toMatchObject({
      content: 'First saved text', baseUpdatedAt: undefined,
    });
    expect(onSave.mock.calls[1][0]).toMatchObject({
      content: 'First saved text plus', baseUpdatedAt: '2026-10-06T10:00:00.000002',
      versionIntent: { skip_version: true, word_count: 4 },
    });
  });

  it('flushes the previous file snapshot when fileId changes before debounce', async () => {
    vi.useFakeTimers();
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const onContentChange = vi.fn();
    const { rerender } = render(
      <SimpleEditor
        fileId="file-a"
        title="File A"
        content="Original A"
        onTitleChange={vi.fn()}
        onContentChange={onContentChange}
        onSave={onSave}
      />
    );

    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Edited A' },
    });
    rerender(
      <SimpleEditor
        fileId="file-b"
        title="File B"
        content="Original B"
        onTitleChange={vi.fn()}
        onContentChange={onContentChange}
        onSave={onSave}
      />
    );
    await act(async () => Promise.resolve());

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        fileId: 'file-a',
        title: 'File A',
        content: 'Edited A',
      }),
    );
    vi.useRealTimers();
  });

  it('flushes a dirty snapshot when the editor unmounts', async () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const { unmount } = render(
      <SimpleEditor
        fileId="file-a"
        title="File A"
        content="Original A"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={onSave}
        onFlushReady={vi.fn()}
      />
    );
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), {
      target: { value: 'Edited before navigation' },
    });

    unmount();
    await act(async () => Promise.resolve());

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        fileId: 'file-a',
        content: 'Edited before navigation',
      }),
    );
  });

  it('holds the autosave while the open file is streaming and resumes it when the stream ends', async () => {
    vi.useFakeTimers();
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 'saved-token' });
    const Harness = ({ isStreaming }: { isStreaming: boolean }) => {
      const [content, setContent] = useState('Original body');
      return (
        <SimpleEditor fileId="file-1" title="File" content={content} baseUpdatedAt="old-token"
          onTitleChange={vi.fn()} onContentChange={setContent} onSave={onSave}
          isStreaming={isStreaming} streamingContent={isStreaming ? 'AI preview' : undefined} />
      );
    };
    const view = render(<Harness isStreaming={false} />);
    fireEvent.change(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder'), { target: { value: 'Author edit' } });
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    view.rerender(<Harness isStreaming />);
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText('editor:unsaved')).toBeInTheDocument();

    view.rerender(<Harness isStreaming={false} />);
    expect(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder')).toHaveValue('Author edit');
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toMatchObject({ content: 'Author edit', baseUpdatedAt: 'old-token' });
    vi.useRealTimers();
  });

  it('keeps streaming display text out of the save baseline and author draft', async () => {
    vi.useFakeTimers();
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 'saved-token' });
    const onFlushReady = vi.fn();
    const props = { fileId: 'file-1', title: 'File', onTitleChange: vi.fn(), onContentChange: vi.fn(), onSave, onFlushReady };
    const view = render(<SimpleEditor {...props} content="Original body" baseUpdatedAt="old-token"
      isStreaming streamingContent="Partial preview" />);
    expect(screen.getByPlaceholderText('editor:placeholder.contentPlaceholder')).toHaveValue('Partial preview');
    view.rerender(<SimpleEditor {...props} content="Committed server body" baseUpdatedAt="new-token"
      isStreaming streamingContent="" serverBaseline={{ version: 1, fileId: 'file-1', title: 'File', content: 'Committed server body', updatedAt: 'new-token' }} />);
    const waitingTextarea = view.container.querySelector('textarea') as HTMLTextAreaElement;
    expect(waitingTextarea).toHaveValue('');
    expect(waitingTextarea).toHaveAttribute('placeholder', '');
    expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    await act(async () => { expect(await onFlushReady.mock.calls.at(-1)?.[0]()).toBe('saved'); });
    fireEvent.keyDown(waitingTextarea, { key: 's', ctrlKey: true });
    view.unmount();
    await act(async () => Promise.resolve());
    expect(onSave).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('shows a calm drafting waiting state and hides the author placeholder before the first streamed char arrives, then switches seamlessly to live prose', () => {
    vi.useFakeTimers();
    const props = {
      fileId: 'file-1',
      title: '第一章 雨夜',
      content: '',
      onTitleChange: vi.fn(),
      onContentChange: vi.fn(),
      onSave: vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 'saved-token' }),
    };

    const view = render(<SimpleEditor {...props} isStreaming streamingContent="" />);
    const textarea = view.container.querySelector('textarea') as HTMLTextAreaElement;

    // Author placeholder must stay hidden while waiting for the first streamed character,
    // while aria-label keeps the textarea accessible for screen readers / axe-core.
    expect(textarea).toHaveAttribute('placeholder', '');
    expect(textarea).toHaveAttribute('aria-label', 'editor:placeholder.contentPlaceholder');
    expect(screen.getByRole('textbox', { name: 'editor:placeholder.contentPlaceholder' })).toBe(textarea);
    expect(screen.queryByPlaceholderText('editor:placeholder.contentPlaceholder')).not.toBeInTheDocument();

    // Banner owns the single drafting status copy + elapsed seconds; canvas shows only a quiet visual skeleton
    const banner = screen.getByTestId('editor-streaming-banner');
    expect(banner).toHaveTextContent('editor:aiDraftingOpening');
    expect(screen.getByTestId('editor-streaming-banner-elapsed')).toHaveTextContent('editor:aiDraftingBannerElapsed');
    const waitingCanvas = screen.getByTestId('editor-streaming-waiting');
    expect(waitingCanvas).toHaveAttribute('aria-hidden', 'true');
    expect(waitingCanvas).toHaveTextContent('');
    expect(screen.getAllByText(/editor:aiDraftingOpening/)).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId('editor-streaming-banner-elapsed')).toHaveTextContent(
      'editor:aiDraftingBannerElapsed:{"seconds":3}'
    );

    // Seamless switch on the very first byte of displayContent
    view.rerender(<SimpleEditor {...props} isStreaming streamingContent="雨" />);
    expect(screen.queryByTestId('editor-streaming-waiting')).not.toBeInTheDocument();
    expect(screen.queryByTestId('editor-streaming-banner-elapsed')).not.toBeInTheDocument();
    expect(screen.getByTestId('editor-streaming-banner')).toHaveTextContent('editor:aiWriting');
    expect(textarea).toHaveValue('雨');
    vi.useRealTimers();
  });

  it('uses untitled drafting copy once in the banner when streaming starts on an untitled empty file', () => {
    const props = {
      fileId: 'file-2',
      title: '   ',
      content: '',
      onTitleChange: vi.fn(),
      onContentChange: vi.fn(),
      onSave: vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 'saved-token' }),
    };

    render(<SimpleEditor {...props} isStreaming streamingContent="" />);
    expect(screen.getByTestId('editor-streaming-banner')).toHaveTextContent('editor:aiDraftingOpeningUntitled');
    expect(screen.getAllByText(/editor:aiDraftingOpeningUntitled/)).toHaveLength(1);
    expect(screen.getByTestId('editor-streaming-waiting')).toHaveTextContent('');
  });

  it('moves focus from title input to body textarea on Enter (ignoring IME) and explains save state in tooltip', () => {
    const props = {
      fileId: 'file-3',
      title: 'Chapter 1',
      content: 'Opening line',
      onTitleChange: vi.fn(),
      onContentChange: vi.fn(),
      onSave: vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 'saved-token' }),
    };

    render(<SimpleEditor {...props} />);
    const titleInput = screen.getByPlaceholderText('editor:placeholder.titlePlaceholder');
    const bodyTextarea = screen.getByRole('textbox', { name: 'editor:placeholder.contentPlaceholder' });
    const saveButton = screen.getByRole('button', { name: 'editor:save' });

    // Clean state explains why Save is disabled
    expect(saveButton).toBeDisabled();
    expect(saveButton).toHaveAttribute('title', 'editor:alreadySaved');

    // IME composing Enter in title must not steal focus
    titleInput.focus();
    fireEvent.keyDown(titleInput, { key: 'Enter', keyCode: 229 });
    expect(titleInput).toHaveFocus();

    // Confirmed Enter in title moves focus smoothly to the body textarea
    fireEvent.keyDown(titleInput, { key: 'Enter' });
    expect(bodyTextarea).toHaveFocus();

    // Dirty state exposes the save shortcut in the tooltip
    fireEvent.change(bodyTextarea, { target: { value: 'Opening line edited' } });
    expect(saveButton).toBeEnabled();
    expect(saveButton.getAttribute('title')).toMatch(/^editor:saveWithShortcut:/);
  });

  it('auto-focuses and selects title input for freshly created files and explains streaming save disablement', async () => {
    const onAutoFocusConsumed = vi.fn();
    const { rerender } = render(
      <SimpleEditor
        fileId="file-new"
        title="新建章节"
        content=""
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 't1' })}
        autoFocusTitle={true}
        onAutoFocusConsumed={onAutoFocusConsumed}
      />,
    );

    const titleInput = screen.getByPlaceholderText('editor:placeholder.titlePlaceholder') as HTMLInputElement;
    await waitFor(() => expect(titleInput).toHaveFocus());
    expect(onAutoFocusConsumed).toHaveBeenCalledTimes(1);

    rerender(
      <SimpleEditor
        fileId="file-new"
        title="新建章节"
        content="正文"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: 't1' })}
        isStreaming={true}
      />,
    );
    const saveButton = screen.getByRole('button', { name: 'editor:save' });
    expect(saveButton).toBeDisabled();
    expect(saveButton).toHaveAttribute('title', 'editor:saveUnavailableStreaming');
  });

});
