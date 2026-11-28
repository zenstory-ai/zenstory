import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToolResultCard } from '../ToolResultCard';
import { MessageList, type Message } from '../MessageList';
import { canPassOpenFileForToolCall, toolCardPayload } from '../chat/messageHelpers';
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
  const isEn = i18nStub.language === 'en';
  const map: Record<string, string> = isEn
    ? {
        'chat:tool.edit_success': 'Edit done',
        'chat:tool.edit_file': 'Edit File',
        'chat:tool.rename_file': 'Rename File',
        'chat:tool.update_file': 'Update File',
        'chat:tool.batch_replace_files': 'Batch Replace',
        'chat:tool.write_chapter': 'Write chapter',
        'chat:tool.create_file': 'Create File',
        'chat:tool.failureHint.notFound': 'Could not find the target passage in the text; the file was not changed.',
        'chat:tool.failureHint.ambiguous': 'That passage appears more than once in the text; the file was not changed.',
        'chat:tool.failureHint.noChange': 'The text before and after was identical, so nothing needed to change.',
        'chat:tool.failureHint.authorChanged': 'This file changed after the AI read it, so nothing was changed in this step.',
        'chat:tool.failureHint.fileMissing': 'This file no longer exists; nothing was changed.',
        'chat:tool.failureHint.permissionDenied': "You don't have permission to modify this file; nothing was changed.",
        'chat:tool.failureHint.generic': 'This step did not complete; the file was not changed.',
        'chat:tool.partialFailureHint.notFound': 'Could not find the target passage for the remaining edit; it did not apply.',
        'chat:tool.partialFailureHint.ambiguous': 'The target passage for the remaining edit appeared more than once; it did not apply.',
        'chat:tool.partialFailureHint.noChange': 'The skipped edit matched the existing text, so no change was needed.',
        'chat:tool.partialFailureHint.invalidEdit': 'Part of the edit instructions was incomplete; the remaining edit did not apply.',
        'chat:tool.partialFailureHint.generic': 'The remaining edit did not apply; completed changes were saved.',
        'chat:edit.replace': 'Replace',
        'chat:edit.insert_after': 'Insert after',
        'chat:edit.label_delete': 'Delete',
        'chat:edit.label_add': 'Add',
        'chat:fileType.draft': 'Manuscript',
        'common:undo': 'Undo',
        'common:untitled': 'Untitled',
      }
    : {
        'chat:tool.edit_success': '修改完成',
        'chat:tool.edit_file': '编辑文件',
        'chat:tool.rename_file': '重命名文件',
        'chat:tool.update_file': '更新文件',
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
        'chat:tool.partialFailureHint.notFound': '部分目标片段未在原文中找到，其余修改未生效。',
        'chat:tool.partialFailureHint.ambiguous': '部分目标片段在原文中出现多处，其余修改未生效。',
        'chat:tool.partialFailureHint.noChange': '未生效的修改与原文一致，无需改动。',
        'chat:tool.partialFailureHint.invalidEdit': '部分修改参数不完整，其余修改未生效。',
        'chat:tool.partialFailureHint.generic': '其余修改未能生效，已生效的部分已保存。',
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

      // Single-line partial-scoped reason without claiming "文件未改动" or leaking raw model prompt
      expect(screen.getByText('部分目标片段未在原文中找到，其余修改未生效。')).toBeInTheDocument();
      expect(screen.queryByText(/文件未改动/)).not.toBeInTheDocument();
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

    it('uses localized partialFailureHint in English UI without claiming "the file was not changed"', () => {
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
                  error_type: 'anchor_not_found',
                  user_message: '未在原文中找到对应段落，文件未改动。',
                },
              ],
            },
          }}
        />,
      );

      expect(
        screen.getByText('Could not find the target passage for the remaining edit; it did not apply.'),
      ).toBeInTheDocument();
      expect(screen.queryByText(/the file was not changed/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/文件未改动/)).not.toBeInTheDocument();
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

    it('does not render clickable open-file button or leak rejected title from raw arguments when rename_file/update_file fails with result: null', () => {
      const onOpenFile = vi.fn();
      const failedRename = {
        id: 'tc-rename-err',
        tool_name: 'rename_file',
        status: 'error' as const,
        error: 'Duplicate title',
        result: null as unknown as Record<string, unknown>,
        arguments: { id: 'f-deleted-or-unauthorized', title: '被拒绝的新标题' },
      };
      const failedUpdate = {
        id: 'tc-update-err',
        tool_name: 'update_file',
        status: 'error' as const,
        error: 'Forbidden',
        result: null as unknown as Record<string, unknown>,
        arguments: { id: 'f-unauthorized-2', title: '未授权覆盖标题' },
      };

      expect(canPassOpenFileForToolCall(failedRename)).toBe(false);
      expect(canPassOpenFileForToolCall(failedUpdate)).toBe(false);
      expect(toolCardPayload(failedRename)).toEqual({});
      expect(toolCardPayload(failedUpdate)).toEqual({});

      const assistantMsg: Message = {
        id: 'msg-err-args',
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        displayItems: [
          {
            id: 'di-tools',
            type: 'tool_calls',
            toolCalls: [failedRename, failedUpdate],
            timestamp: new Date(),
          },
        ],
      };

      render(<MessageList messages={[assistantMsg]} onOpenFile={onOpenFile} />);

      expect(screen.getByText('重命名文件失败')).toBeInTheDocument();
      expect(screen.getByText('更新文件失败')).toBeInTheDocument();
      expect(screen.queryByText('被拒绝的新标题')).not.toBeInTheDocument();
      expect(screen.queryByText('未授权覆盖标题')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '被拒绝的新标题' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '未授权覆盖标题' })).not.toBeInTheDocument();
      expect(onOpenFile).not.toHaveBeenCalled();
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
