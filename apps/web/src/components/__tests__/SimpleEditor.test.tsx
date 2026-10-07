import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SimpleEditor } from '../SimpleEditor';
import { toast } from '../../lib/toast';
import { writingStatsApi } from '../../lib/writingStatsApi';

vi.mock('../../lib/naturalPolishApi', () => ({
  naturalPolishApi: {
    naturalPolish: vi.fn(),
  },
}));

vi.mock('../../lib/writingStatsApi', () => ({
  writingStatsApi: {
    recordStats: vi.fn(),
  },
}));

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
    t: (key: string, fallback?: string) => fallback ?? key,
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

vi.mock('../SelectionToolbar', () => ({
  SelectionToolbar: () => null,
}));

describe('SimpleEditor', () => {
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
  });

  afterEach(() => {
    vi.restoreAllMocks();
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

  it('natural polish triggers diff review with rewritten text', async () => {
    const { naturalPolishApi } = await import('../../lib/naturalPolishApi');
    const naturalPolishMock = naturalPolishApi.naturalPolish as unknown as ReturnType<typeof vi.fn>;
    naturalPolishMock.mockResolvedValueOnce('  Rewritten  ');

    const onEnterDiffReview = vi.fn();
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
        onEnterDiffReview={onEnterDiffReview}
      />
    );

    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
    selectText(textarea);
    fireEvent.keyDown(textarea, { key: 'R', code: 'KeyR', ctrlKey: true, shiftKey: true });

    await waitFor(() => {
      expect(onEnterDiffReview).toHaveBeenCalledTimes(1);
    });

    expect(onEnterDiffReview).toHaveBeenCalledWith(
      'file-1',
      'Hello world',
      'Rewritten world'
    );
  });

  it('natural polish ignores stale result after file switch', async () => {
    const { naturalPolishApi } = await import('../../lib/naturalPolishApi');
    const naturalPolishMock = naturalPolishApi.naturalPolish as unknown as ReturnType<typeof vi.fn>;

    let resolvePolish: (value: string) => void = () => {};
    const deferred = new Promise<string>((resolve) => {
      resolvePolish = resolve;
    });
    naturalPolishMock.mockReturnValueOnce(deferred);

    const onEnterDiffReview = vi.fn();
    const { rerender } = render(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="Hello world"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' })}
        onEnterDiffReview={onEnterDiffReview}
      />
    );

    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
    selectText(textarea);
    fireEvent.keyDown(textarea, { key: 'R', code: 'KeyR', ctrlKey: true, shiftKey: true });

    // Switch file before the request resolves.
    rerender(
      <SimpleEditor
        projectId="project-1"
        fileId="file-2"
        fileType="draft"
        title="File 2"
        content="Another content"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' })}
        onEnterDiffReview={onEnterDiffReview}
      />
    );

    resolvePolish('Rewritten');
    await waitFor(() => {
      // flush promise microtasks
      expect(true).toBe(true);
    });

    expect(onEnterDiffReview).not.toHaveBeenCalled();
  });

  it('natural polish surfaces backend error message', async () => {
    const { naturalPolishApi } = await import('../../lib/naturalPolishApi');
    const naturalPolishMock = naturalPolishApi.naturalPolish as unknown as ReturnType<typeof vi.fn>;
    naturalPolishMock.mockRejectedValueOnce(new Error('ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED'));

    const toastErrorSpy = vi.spyOn(toast, 'error').mockImplementation(() => {});

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

    const textarea = screen.getByPlaceholderText('editor:placeholder.contentPlaceholder') as HTMLTextAreaElement;
    selectText(textarea);
    fireEvent.keyDown(textarea, { key: 'R', code: 'KeyR', ctrlKey: true, shiftKey: true });

    await waitFor(() => {
      expect(toastErrorSpy).toHaveBeenCalledWith('ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED');
    });
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

  it('keeps dirty baseline and skips stats when save reports a conflict', async () => {
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
    expect(writingStatsApi.recordStats).not.toHaveBeenCalled();
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

  it('resolves queued save version and stats deltas against the preceding successful save', async () => {
    let resolveFirstSave: (outcome: { outcome: 'saved'; updatedAt: string }) => void = () => {};
    const firstSave = new Promise<{ outcome: 'saved'; updatedAt: string }>((resolve) => { resolveFirstSave = resolve; });
    const onSave = vi.fn().mockReturnValueOnce(firstSave).mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    vi.mocked(writingStatsApi.recordStats).mockResolvedValue(undefined as never);
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
    expect(onSave.mock.calls[1][0]).toMatchObject({
      content: 'First saved text plus', versionIntent: { skip_version: true, word_count: 4 },
    });
    expect(writingStatsApi.recordStats).toHaveBeenNthCalledWith(1, 'project-1', {
      word_count: 3, words_added: 1, words_deleted: 0,
    });
    expect(writingStatsApi.recordStats).toHaveBeenNthCalledWith(2, 'project-1', {
      word_count: 4, words_added: 1, words_deleted: 0,
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
});
