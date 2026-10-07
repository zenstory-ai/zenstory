/**
 * Round-3 收敛阶段回归：SimpleEditor 在「AI 正在编辑本文件」期间必须挂起自动保存。
 *
 * version-quota #1 fix 第 4 条：编辑器提交的是整篇正文快照，3 秒防抖期间 AI 的
 * edit_file 先落库时，这份过期快照会在它之后发出并把它整段覆盖掉。
 * 服务端的乐观并发校验（base_updated_at → 409）是最后一道防线，但更好的做法是
 * 在 file_edit_start ~ file_edit_end 之间根本不发这次请求。
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SimpleEditor } from '../SimpleEditor';

vi.mock('../../lib/naturalPolishApi', () => ({
  naturalPolishApi: { naturalPolish: vi.fn() },
}));

vi.mock('../../lib/writingStatsApi', () => ({
  writingStatsApi: { recordStats: vi.fn() },
}));

vi.mock('../../contexts/TextQuoteContext', () => ({
  useTextQuote: () => ({ addQuote: vi.fn() }),
}));

vi.mock('../../hooks/useGestures', () => ({
  usePinchZoom: () => ({ zoom: 1, bind: () => ({}), resetZoom: vi.fn() }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

vi.mock('../FileVersionHistory', () => ({ FileVersionHistory: () => null }));
vi.mock('../InlineDiffEditor', () => ({ InlineDiffEditor: () => null }));
vi.mock('../DiffToolbar', () => ({ DiffToolbar: () => null }));
vi.mock('../SelectionToolbar', () => ({ SelectionToolbar: () => null }));

const AUTO_SAVE_DEBOUNCE_MS = 3000;

describe('SimpleEditor: AI 编辑期间挂起自动保存', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(
      (cb: FrameRequestCallback) => {
        cb(0);
        return 0;
      },
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function renderEditor(props: Record<string, unknown>) {
    return render(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="原始正文"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' })}
        {...props}
      />,
    );
  }

  function typeInto(text: string) {
    const textarea = screen.getByPlaceholderText(
      'editor:placeholder.contentPlaceholder',
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: text } });
  }

  it('未标记 AI 编辑时，防抖到期后正常自动保存', async () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    renderEditor({ onSave });

    typeInto('用户改了一句');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_SAVE_DEBOUNCE_MS + 100);
    });

    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('isAiEditing 为 true 时不排自动保存（过期快照根本不会发出）', () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    renderEditor({ onSave, isAiEditing: true });

    typeInto('用户改了一句');
    act(() => {
      vi.advanceTimersByTime(AUTO_SAVE_DEBOUNCE_MS * 3);
    });

    expect(onSave).not.toHaveBeenCalled();
  });

  it('AI 编辑结束后恢复自动保存，用户的本地改动不会丢', async () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    const { rerender } = renderEditor({ onSave, isAiEditing: true });

    typeInto('用户改了一句');
    act(() => {
      vi.advanceTimersByTime(AUTO_SAVE_DEBOUNCE_MS * 2);
    });
    expect(onSave).not.toHaveBeenCalled();

    // file_edit_end 到达：标记清除
    rerender(
      <SimpleEditor
        projectId="project-1"
        fileId="file-1"
        fileType="draft"
        title="File 1"
        content="用户改了一句"
        onTitleChange={vi.fn()}
        onContentChange={vi.fn()}
        onSave={onSave}
        isAiEditing={false}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTO_SAVE_DEBOUNCE_MS + 100);
    });

    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('AI 编辑期间手动 Ctrl+S 同样被挡住', () => {
    const onSave = vi.fn().mockResolvedValue({ outcome: 'saved', updatedAt: '2026-10-06T10:00:00.000002' });
    renderEditor({ onSave, isAiEditing: true });

    typeInto('用户改了一句');
    const textarea = screen.getByPlaceholderText(
      'editor:placeholder.contentPlaceholder',
    ) as HTMLTextAreaElement;
    fireEvent.keyDown(textarea, { key: 's', ctrlKey: true });

    expect(onSave).not.toHaveBeenCalled();
  });
});
