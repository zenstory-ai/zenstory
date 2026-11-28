import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToolResultCard } from '../ToolResultCard';
import { createStreamFileSyncHandlers } from '../../hooks/streaming/createStreamFileSync';
import { toast } from '../../lib/toast';

vi.mock('../LazyMarkdown', () => ({
  LazyMarkdown: ({ children }: { children: string }) => <div>{children}</div>,
  default: ({ children }: { children: string }) => <div>{children}</div>,
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: vi.fn(() => ({ user: null })),
}));

const mockT = vi.fn((key: string, options?: Record<string, unknown>) => {
  if (key === 'chat:tool.edit_partial_failed') {
    return `部分修改未生效（成功 ${options?.applied} / 未生效 ${options?.failed}）`;
  }
  if (key === 'chat:tool.view_edit_details') {
    return `查看变更详情（${options?.count}）`;
  }
  if (key === 'chat:tool.failed') {
    return `${options?.label}失败`;
  }
  if (key === 'chat:fileEdit.partialFailed') {
    return `《${options?.title}》有 ${options?.count} 处修改未生效。`;
  }
  if (key === 'chat:fileEdit.partialFailedUntitled') {
    return `有 ${options?.count} 处修改未生效。`;
  }
  if (key === 'chat:fileEdit.allFailed') {
    return `《${options?.title}》有 ${options?.count} 处修改未生效，文件未改动。`;
  }
  if (key === 'chat:fileEdit.allFailedUntitled') {
    return `有 ${options?.count} 处修改未生效，文件未改动。`;
  }
  const map: Record<string, string> = {
    'chat:tool.edit_success': '修改完成',
    'chat:tool.edit_file': '编辑文件',
    'chat:tool.batch_replace_files': '批量替换',
    'chat:tool.write_chapter': '写章节',
    'chat:tool.create_file': '创建文件',
    'chat:tool.failureHint.notFound': '未在原文中找到对应段落，文件未改动。',
    'chat:tool.failureHint.ambiguous': '要修改的片段在原文中出现多处，文件未改动。',
    'chat:tool.failureHint.noChange': '修改前后内容一致，无需改动。',
    'chat:tool.failureHint.authorChanged': '这个文件在 AI 读过之后又被改过，这一步没有改动。',
    'chat:tool.failureHint.fileMissing': '目标文件已不存在或已被删除，文件未改动。',
    'chat:tool.failureHint.permissionDenied': '无权修改该文件，文件未改动。',
    'chat:tool.failureHint.generic': '这一步未能完成，文件未改动。',
    'chat:edit.replace': '替换',
    'chat:edit.insert_after': '插入后',
    'chat:edit.label_delete': '删除',
    'chat:edit.label_add': '新增',
    'chat:fileType.draft': '正文',
    'common:undo': '撤销',
    'common:untitled': '未命名',
  };
  return map[key] ?? key;
});

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: vi.fn() },
  useTranslation: () => ({ t: mockT }),
}));

const i18nStub = vi.hoisted(() => ({ language: 'zh' }));
vi.mock('../../lib/i18n', () => ({
  default: i18nStub,
}));

describe('Lightweight AI edit failure cards', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18nStub.language = 'zh';
  });

  describe('EditFileCard partial failure', () => {
    const partialData = {
      id: 'ch-1',
      title: '第1章 雨夜',
      file_type: 'draft',
      edits_applied: 1,
      partial_success: true,
      details: [{ op: 'replace', old_preview: '窗外下着雨。', new_preview: '窗外飘着雪。' }],
      failed_edits: [
        {
          index: 1,
          op: 'replace',
          error_type: 'anchor_not_found',
          error: 'RAW_MODEL_PROMPT_DO_NOT_SHOW',
          user_message: '未在原文中找到对应段落，文件未改动。',
        },
      ],
      undo: {
        before_version_number: 2,
        expected_after_updated_at: '2026-11-27T10:00:00Z',
      },
    };

    it('renders calm warning status, 1-line safe reason, file-open, undo, and applied details without drawer, copy, or retry controls', async () => {
      const user = userEvent.setup();
      const onOpenFile = vi.fn();
      const onUndo = vi.fn();

      const { container } = render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ data: partialData }}
          onOpenFile={onOpenFile}
          onUndo={onUndo}
          chatMessageId="msg-1"
          toolUseId="tc-1"
        />,
      );

      // Calm warning title, never green "修改完成"
      expect(screen.getByText('部分修改未生效（成功 1 / 未生效 1）')).toBeInTheDocument();
      expect(screen.queryByText('修改完成')).not.toBeInTheDocument();
      expect(container.querySelector('.text-\\[hsl\\(var\\(--warning-text\\)\\)\\]')).not.toBeNull();

      // Single-line safe reason from first failed edit without leaking raw model prompt
      expect(screen.getByText('未在原文中找到对应段落，文件未改动。')).toBeInTheDocument();
      expect(screen.queryByText(/RAW_MODEL_PROMPT_DO_NOT_SHOW/)).not.toBeInTheDocument();

      // No failed-edit drawer, copy button, or retry button
      expect(screen.queryByText(/未生效的修改/)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /复制/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /重读|重试/ })).not.toBeInTheDocument();

      // Normal file-open, undo, and applied details remain intact
      await user.click(screen.getByRole('button', { name: '第1章 雨夜' }));
      expect(onOpenFile).toHaveBeenCalledWith('ch-1', 'draft', '第1章 雨夜');
      expect(screen.getByRole('button', { name: '撤销' })).toBeInTheDocument();
      expect(screen.getByText('查看变更详情（1）')).toBeInTheDocument();
    });

    it('uses localized failureHint in English UI instead of Chinese user_message', () => {
      i18nStub.language = 'en';
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              ...partialData,
              failed_edits: [
                {
                  index: 1,
                  op: 'replace',
                  error_type: 'no_change',
                  user_message: '中文原因',
                },
              ],
            },
          }}
        />,
      );

      expect(screen.getByText('修改前后内容一致，无需改动。')).toBeInTheDocument();
      expect(screen.queryByText('中文原因')).not.toBeInTheDocument();
    });
  });

  describe('ToolFailureCard and BatchReplaceFilesCard', () => {
    it('renders neutral failure card with clickable file row and 1-line reason, without drawer, copy, or retry', async () => {
      const user = userEvent.setup();
      const onOpenFile = vi.fn();

      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          error="未在原文中找到对应段落，文件未改动。"
          result={{
            data: {
              id: 'ch-2',
              title: '第2章 长夜',
              file_type: 'draft',
              error_type: 'anchor_not_found',
              user_message: '未在原文中找到对应段落，文件未改动。',
              failed_edits: [
                {
                  index: 0,
                  op: 'insert_after',
                  error_type: 'anchor_not_found',
                  user_message: '未在原文中找到对应段落，文件未改动。',
                },
              ],
            },
          }}
          onOpenFile={onOpenFile}
        />,
      );

      expect(screen.getByText('编辑文件失败')).toBeInTheDocument();
      expect(screen.getByText('未在原文中找到对应段落，文件未改动。')).toBeInTheDocument();
      expect(screen.queryByText(/未生效的修改/)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /复制/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /重读|重试/ })).not.toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: '第2章 长夜' }));
      expect(onOpenFile).toHaveBeenCalledWith('ch-2', 'draft', '第2章 长夜');
    });

    it('suppresses file row on create_file, file_not_found, and permission_denied failures', () => {
      const onOpenFile = vi.fn();

      const { rerender } = render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          error="无权修改该文件，文件未改动。"
          result={{
            data: {
              id: 'secret-file-1',
              title: '未授权文件',
              file_type: 'draft',
              error_type: 'permission_denied',
            },
          }}
          onOpenFile={onOpenFile}
        />,
      );
      expect(screen.queryByText('未授权文件')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '未授权文件' })).not.toBeInTheDocument();

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          error="目标文件已不存在或已被删除，文件未改动。"
          result={{
            data: {
              id: 'deleted-file-1',
              title: '已删除章节',
              file_type: 'draft',
              error_type: 'file_not_found',
            },
          }}
          onOpenFile={onOpenFile}
        />,
      );
      expect(screen.queryByText('已删除章节')).not.toBeInTheDocument();

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          error="failed"
          result={{
            data: {
              id: 'unsaved-file-1',
              title: '未建成的章节',
              file_type: 'draft',
              error_type: 'invalid_edit',
            },
          }}
          onOpenFile={onOpenFile}
        />,
      );
      expect(screen.queryByText('未建成的章节')).not.toBeInTheDocument();
    });

    it('keeps BatchReplaceFilesCard inline skipped files clickable except file_not_found/permission_denied, and drops skipped drawer on error card', async () => {
      const user = userEvent.setup();
      const onOpenFile = vi.fn();

      const { rerender } = render(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          error="skipped"
          result={{
            data: {
              error_type: 'stale_content',
              skipped_files: [
                { id: 'f-10', title: '第10章 尾声', file_type: 'draft', reason: 'stale_content' },
              ],
            },
          }}
          onOpenFile={onOpenFile}
        />,
      );
      expect(screen.queryByText(/未修改的文件/)).not.toBeInTheDocument();

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          result={{
            data: {
              modified_files: 1,
              total_replacements: 2,
              files: [{ id: 'f-1', title: '第1章', file_type: 'draft', replacements: 2 }],
              skipped_files: [
                { id: 'f-2', title: '第2章', file_type: 'draft', reason: 'stale_content' },
                { id: 'f-secret', title: '禁止访问文件', file_type: 'draft', reason: 'permission_denied' },
                { id: 'f-gone', title: '缺失文件', file_type: 'draft', reason: 'file_not_found' },
              ],
            },
          }}
          onOpenFile={onOpenFile}
        />,
      );

      await user.click(screen.getByRole('button', { name: '第2章' }));
      expect(onOpenFile).toHaveBeenCalledWith('f-2', 'draft', '第2章');
      expect(screen.queryByRole('button', { name: '禁止访问文件' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '缺失文件' })).not.toBeInTheDocument();
      expect(screen.getByText('禁止访问文件')).toBeInTheDocument();
      expect(screen.getByText('缺失文件')).toBeInTheDocument();
    });
  });

  describe('createStreamFileSync toast guard and write-state gating', () => {
    it('never formats empty 《》, uses simplified toast copy without retry prompt, and does not mark roundWroteFilesRef or refresh tree when editsApplied === 0 / allFailed', () => {
      const errorSpy = vi.spyOn(toast, 'error').mockImplementation(() => '');
      const triggerFileTreeRefresh = vi.fn();
      const roundWroteFilesRef = { current: false };
      const sync = createStreamFileSyncHandlers(
        {
          fileContentEndByFileIdRef: { current: new Set() },
          fileEditMetaByIdRef: { current: new Map() },
          aiEditingTargetRef: { current: null },
          pendingFileTreeRefreshRef: { current: false },
          roundWroteFilesRef,
          roundMayDeleteFilesRef: { current: false },
          deletionTreeCheckRef: { current: null },
          fileTreeRefreshTimerRef: { current: null },
        },
        {
          triggerFileTreeRefresh,
          triggerEditorRefresh: vi.fn(),
          setSelectedItem: vi.fn(),
          appendFileContent: vi.fn(),
          finishFileStreaming: vi.fn(),
          startFileStreaming: vi.fn(),
          t: mockT,
        },
        vi.fn(),
      );

      sync.onFileEditEnd('f-untitled', 0, undefined, undefined, 'draft', '', {
        failedCount: 2,
        partialSuccess: false,
        allFailed: true,
        warnings: [],
      });
      expect(errorSpy).toHaveBeenCalledWith('有 2 处修改未生效，文件未改动。');
      expect(errorSpy.mock.calls[0]?.[0]).not.toContain('《》');
      expect(errorSpy.mock.calls[0]?.[0]).not.toContain('重试');
      expect(roundWroteFilesRef.current).toBe(false);
      expect(triggerFileTreeRefresh).not.toHaveBeenCalled();

      errorSpy.mockClear();
      sync.onFileEditEnd('f-titled', 1, undefined, undefined, 'draft', '第3章 风起', {
        failedCount: 1,
        partialSuccess: true,
        allFailed: false,
        warnings: [],
      });
      expect(errorSpy).toHaveBeenCalledWith('《第3章 风起》有 1 处修改未生效。');
      expect(errorSpy.mock.calls[0]?.[0]).not.toContain('重试');
      expect(roundWroteFilesRef.current).toBe(true);
      errorSpy.mockRestore();
    });
  });
});
