import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToolResultCard } from '../ToolResultCard';
import { MessageList, type Message } from '../MessageList';
import { ChatStreamErrorCard } from '../chat/ChatStatusBanners';
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
  if (key === 'chat:tool.failed_edits_summary') {
    return `未生效的修改（${options?.count}）`;
  }
  if (key === 'chat:tool.skipped_files_summary') {
    return `未修改的文件（${options?.count}）`;
  }
  if (key === 'chat:tool.view_edit_details') {
    return `查看变更详情（${options?.count}）`;
  }
  if (key === 'chat:tool.failed') {
    return `${options?.label}失败`;
  }
  if (key === 'chat:fileEdit.partialFailed') {
    return `《${options?.title}》有 ${options?.count} 处修改未生效，可在对话卡片查看详情或重试。`;
  }
  if (key === 'chat:fileEdit.partialFailedUntitled') {
    return `有 ${options?.count} 处修改未生效，可在对话卡片查看详情或重试。`;
  }
  if (key === 'chat:fileEdit.allFailed') {
    return `《${options?.title}》有 ${options?.count} 处修改未生效，可在对话卡片查看详情或重试。`;
  }
  if (key === 'chat:fileEdit.allFailedUntitled') {
    return `有 ${options?.count} 处修改未生效，可在对话卡片查看详情或重试。`;
  }
  const map: Record<string, string> = {
    'chat:tool.edit_success': '修改完成',
    'chat:tool.edit_file': '编辑文件',
    'chat:tool.batch_replace_files': '批量替换',
    'chat:tool.write_chapter': '写章节',
    'chat:tool.create_file': '创建文件',
    'chat:tool.failed_edit_target': '目标原文',
    'chat:tool.failed_edit_anchor': '插入锚点',
    'chat:tool.failed_edit_attempted': '尝试写入',
    'chat:tool.copy_attempted_text': '复制这段文本',
    'chat:tool.copied_attempted_text': '已复制',
    'chat:tool.open_target_file': '打开目标文件',
    'chat:tool.retry_reread_edit': '重读文件并重试',
    'chat:tool.failureHint.notFound': '未在原文中找到对应段落，文件未改动。',
    'chat:tool.failureHint.notFoundStreaming': '未在原文中找到对应段落，AI 正在重新定位。',
    'chat:tool.failureHint.ambiguous': '要修改的片段在原文中出现多处，文件未改动。',
    'chat:tool.failureHint.noChange': '修改前后内容一致，无需改动。',
    'chat:tool.failureHint.authorChanged': '这个文件在 AI 读过之后又被改过，这一步没有改动。',
    'chat:tool.failureHint.fileMissing': '目标文件已不存在或已被删除，文件未改动。',
    'chat:tool.failureHint.generic': '这一步未能完成，文件未改动。',
    'chat:edit.replace': '替换',
    'chat:edit.insert_after': '插入后',
    'chat:edit.label_delete': '删除',
    'chat:edit.label_add': '新增',
    'chat:fileType.draft': '正文',
    'chat:panel.streamErrorTitle': '这次生成中断了',
    'chat:panel.streamErrorQuotaPaused': '今日额度用完',
    'common:undo': '撤销',
    'common:retry': '重试',
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

describe('AI edit failure visibility and intervention actions', () => {
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
          old_preview: '找不到的旧句子。',
          new_preview: '试图改写的新句子。',
        },
      ],
      undo: {
        before_version_number: 2,
        expected_after_updated_at: '2026-11-27T10:00:00Z',
      },
    };

    it('renders warning state instead of green edit_success, shows failed details, copy, open-file, undo, and retry', async () => {
      const user = userEvent.setup();
      const onOpenFile = vi.fn();
      const onUndo = vi.fn();
      const onRetryEditFailure = vi.fn();

      const { container } = render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ data: partialData }}
          toolArguments={{
            id: 'ch-1',
            edits: [
              { op: 'replace', old: '窗外下着雨。', new: '窗外飘着雪。' },
              { op: 'replace', old: '找不到的旧句子。', new: '完整未截断的改写句子。' },
            ],
          }}
          onOpenFile={onOpenFile}
          onUndo={onUndo}
          chatMessageId="msg-1"
          toolUseId="tc-1"
          onRetryEditFailure={onRetryEditFailure}
        />,
      );

      // Calm warning title, never green "修改完成"
      expect(screen.getByText('部分修改未生效（成功 1 / 未生效 1）')).toBeInTheDocument();
      expect(screen.queryByText('修改完成')).not.toBeInTheDocument();
      expect(container.querySelector('.text-\\[hsl\\(var\\(--warning-text\\)\\)\\]')).not.toBeNull();

      // Failed edit details are visible by default without leaking raw model prompt
      expect(screen.getByText('未生效的修改（1）')).toBeInTheDocument();
      expect(screen.getByText('找不到的旧句子。')).toBeInTheDocument();
      expect(screen.getByText('试图改写的新句子。')).toBeInTheDocument();
      expect(screen.queryByText(/RAW_MODEL_PROMPT_DO_NOT_SHOW/)).not.toBeInTheDocument();

      // Open target file works
      await user.click(screen.getByRole('button', { name: '第1章 雨夜' }));
      expect(onOpenFile).toHaveBeenCalledWith('ch-1', 'draft', '第1章 雨夜');

      // Copy attempted text copies full unclipped text from toolArguments and shows feedback
      const clipboardSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
      await user.click(screen.getByRole('button', { name: '复制这段文本' }));
      expect(clipboardSpy).toHaveBeenCalledWith('完整未截断的改写句子。');
      await waitFor(() => {
        expect(screen.getByText('已复制')).toBeInTheDocument();
      });

      // Undo control still present
      expect(screen.getByRole('button', { name: '撤销' })).toBeInTheDocument();

      // Explicit retry action dispatches context
      await user.click(screen.getByRole('button', { name: '重读文件并重试' }));
      expect(onRetryEditFailure).toHaveBeenCalledWith({
        toolName: 'edit_file',
        fileId: 'ch-1',
        title: '第1章 雨夜',
        fileType: 'draft',
        failedCount: 1,
      });
    });

    it('hides retry action while streaming or when nextStepsBlocked', () => {
      const onRetryEditFailure = vi.fn();
      const { rerender } = render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ data: partialData }}
          isStreaming={true}
          onRetryEditFailure={onRetryEditFailure}
        />,
      );
      expect(screen.queryByRole('button', { name: '重读文件并重试' })).not.toBeInTheDocument();

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ data: partialData }}
          isStreaming={false}
          nextStepsBlocked={true}
          onRetryEditFailure={onRetryEditFailure}
        />,
      );
      expect(screen.queryByRole('button', { name: '重读文件并重试' })).not.toBeInTheDocument();
    });
  });

  describe('ToolFailureCard visibility and actions', () => {
    it('renders clickable file row, failed edit previews, neutral settled copy, copy button, and retry action', async () => {
      const user = userEvent.setup();
      const onOpenFile = vi.fn();
      const onRetryEditFailure = vi.fn();

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
              // Retired present-tense string from historical DB records must settle to neutral wording
              user_message: 'AI 没在原文里找到要改的那一段，正在重新定位。',
              failed_edits: [
                {
                  index: 0,
                  op: 'insert_after',
                  error_type: 'anchor_not_found',
                  anchor_preview: '走廊尽头传来脚步声。',
                  text_preview: '她屏住了呼吸。',
                },
              ],
            },
          }}
          onOpenFile={onOpenFile}
          onRetryEditFailure={onRetryEditFailure}
        />,
      );

      expect(screen.getByText('编辑文件失败')).toBeInTheDocument();
      // Settled wording replaces retired present-tense wording
      expect(screen.queryByText(/正在重新定位/)).not.toBeInTheDocument();
      expect(screen.getAllByText('未在原文中找到对应段落，文件未改动。').length).toBeGreaterThanOrEqual(1);

      // Clickable file row
      await user.click(screen.getByRole('button', { name: '第2章 长夜' }));
      expect(onOpenFile).toHaveBeenCalledWith('ch-2', 'draft', '第2章 长夜');

      // Anchor & attempted text + copy
      expect(screen.getByText('走廊尽头传来脚步声。')).toBeInTheDocument();
      expect(screen.getByText('她屏住了呼吸。')).toBeInTheDocument();
      const clipboardSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined);
      await user.click(screen.getByRole('button', { name: '复制这段文本' }));
      expect(clipboardSpy).toHaveBeenCalledWith('她屏住了呼吸。');

      // Explicit retry
      await user.click(screen.getByRole('button', { name: '重读文件并重试' }));
      expect(onRetryEditFailure).toHaveBeenCalledWith({
        toolName: 'edit_file',
        fileId: 'ch-2',
        title: '第2章 长夜',
        fileType: 'draft',
        failedCount: 1,
      });
    });

    it('falls back to toolArguments.id and toolArguments.edits on historical error records without result metadata', async () => {
      const user = userEvent.setup();
      const onOpenFile = vi.fn();

      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          error="failed"
          result={{ error_type: 'anchor_not_found' }}
          toolArguments={{
            id: 'legacy-file-9',
            edits: [{ op: 'replace', old: '旧台词', new: '新台词' }],
          }}
          onOpenFile={onOpenFile}
        />,
      );

      await user.click(screen.getByRole('button', { name: '打开目标文件' }));
      expect(onOpenFile).toHaveBeenCalledWith('legacy-file-9', '', '打开目标文件');
      expect(screen.getByText('旧台词')).toBeInTheDocument();
      expect(screen.getByText('新台词')).toBeInTheDocument();
    });

    it('renders clickable skipped files on batch_replace_files error and partial cards', async () => {
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

      expect(screen.getByText('未修改的文件（1）')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: '第10章 尾声' }));
      expect(onOpenFile).toHaveBeenCalledWith('f-10', 'draft', '第10章 尾声');

      onOpenFile.mockClear();
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
              ],
            },
          }}
          onOpenFile={onOpenFile}
        />,
      );

      await user.click(screen.getByRole('button', { name: '第2章' }));
      expect(onOpenFile).toHaveBeenCalledWith('f-2', 'draft', '第2章');
    });
  });

  describe('Unresolved failure gating in MessageList', () => {
    it('hides retry action when an edit failure is followed by a later clean edit to the same file', () => {
      const onRetryEditFailure = vi.fn();
      const message: Message = {
        id: 'm-resolved',
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        displayItems: [
          {
            id: 'tc-1',
            type: 'tool_calls',
            timestamp: new Date(),
            toolCalls: [
              {
                id: 'call-fail',
                tool_name: 'edit_file',
                arguments: { id: 'f-1', edits: [{ op: 'replace', old: 'a', new: 'b' }] },
                status: 'error',
                error: '未在原文中找到对应段落，文件未改动。',
                result: { id: 'f-1', title: '第1章', file_type: 'draft', error_type: 'anchor_not_found' },
              },
              {
                id: 'call-ok',
                tool_name: 'edit_file',
                arguments: { id: 'f-1', edits: [{ op: 'replace', old: 'a', new: 'b' }] },
                status: 'success',
                result: { id: 'f-1', title: '第1章', file_type: 'draft', edits_applied: 1, details: [] },
              },
            ],
          },
        ],
      };

      render(
        <MessageList
          messages={[message]}
          onRetryEditFailure={onRetryEditFailure}
        />,
      );

      expect(screen.queryByRole('button', { name: '重读文件并重试' })).not.toBeInTheDocument();
    });

    it('shows retry action only for the unresolved file and hides it when nextStepsBlocked', () => {
      const onRetryEditFailure = vi.fn();
      const message: Message = {
        id: 'm-unresolved',
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        displayItems: [
          {
            id: 'tc-1',
            type: 'tool_calls',
            timestamp: new Date(),
            toolCalls: [
              {
                id: 'call-partial',
                tool_name: 'edit_file',
                arguments: { id: 'f-2', edits: [] },
                status: 'success',
                result: {
                  id: 'f-2',
                  title: '第2章',
                  file_type: 'draft',
                  edits_applied: 1,
                  partial_success: true,
                  failed_edits: [{ index: 1, op: 'replace', error_type: 'anchor_not_found', old_preview: 'x', new_preview: 'y' }],
                },
              },
            ],
          },
        ],
      };

      const { rerender } = render(
        <MessageList
          messages={[message]}
          onRetryEditFailure={onRetryEditFailure}
          nextStepsBlocked={false}
        />,
      );

      const retryBtn = screen.getByRole('button', { name: '重读文件并重试' });
      fireEvent.click(retryBtn);
      expect(onRetryEditFailure).toHaveBeenCalledTimes(1);

      rerender(
        <MessageList
          messages={[message]}
          onRetryEditFailure={onRetryEditFailure}
          nextStepsBlocked={true}
        />,
      );
      expect(screen.queryByRole('button', { name: '重读文件并重试' })).not.toBeInTheDocument();
    });
  });

  describe('ChatStreamErrorCard circuit breaker retry', () => {
    it('renders explicit retry action on ERR_AGENT_TOOL_FAILURE_LIMIT and hides when blocked', async () => {
      const user = userEvent.setup();
      const onRetryToolFailureLimit = vi.fn();

      const { rerender } = render(
        <ChatStreamErrorCard
          error="AI 这一步连续几次都没做成，这一轮先停下了。"
          errorCode="ERR_AGENT_TOOL_FAILURE_LIMIT"
          isAiQuotaLimit={false}
          isNetworkQuotaLimit={false}
          aiSendBlocked={false}
          canRetry={false}
          isMobile={false}
          onRetry={vi.fn()}
          onRetryToolFailureLimit={onRetryToolFailureLimit}
          onOpenUpgradeModal={vi.fn()}
        />,
      );

      const button = screen.getByRole('button', { name: '重读文件并重试' });
      await user.click(button);
      expect(onRetryToolFailureLimit).toHaveBeenCalledTimes(1);

      rerender(
        <ChatStreamErrorCard
          error="AI 这一步连续几次都没做成，这一轮先停下了。"
          errorCode="ERR_AGENT_TOOL_FAILURE_LIMIT"
          isAiQuotaLimit={false}
          isNetworkQuotaLimit={false}
          aiSendBlocked={true}
          canRetry={false}
          isMobile={false}
          onRetry={vi.fn()}
          onRetryToolFailureLimit={onRetryToolFailureLimit}
          onOpenUpgradeModal={vi.fn()}
        />,
      );
      expect(screen.queryByRole('button', { name: '重读文件并重试' })).not.toBeInTheDocument();
    });
  });

  describe('createStreamFileSync toast guard', () => {
    it('never formats empty 《》 when title is missing on partial/all-failed file_edit_end', () => {
      const errorSpy = vi.spyOn(toast, 'error').mockImplementation(() => '');
      const sync = createStreamFileSyncHandlers(
        {
          fileContentEndByFileIdRef: { current: new Set() },
          fileEditMetaByIdRef: { current: new Map() },
          aiEditingTargetRef: { current: null },
          pendingFileTreeRefreshRef: { current: false },
          roundWroteFilesRef: { current: false },
          roundMayDeleteFilesRef: { current: false },
          deletionTreeCheckRef: { current: null },
          fileTreeRefreshTimerRef: { current: null },
        },
        {
          triggerFileTreeRefresh: vi.fn(),
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
      expect(errorSpy).toHaveBeenCalledWith('有 2 处修改未生效，可在对话卡片查看详情或重试。');
      expect(errorSpy.mock.calls[0]?.[0]).not.toContain('《》');

      errorSpy.mockClear();
      sync.onFileEditEnd('f-titled', 1, undefined, undefined, 'draft', '第3章 风起', {
        failedCount: 1,
        partialSuccess: true,
        allFailed: false,
        warnings: [],
      });
      expect(errorSpy).toHaveBeenCalledWith('《第3章 风起》有 1 处修改未生效，可在对话卡片查看详情或重试。');
      errorSpy.mockRestore();
    });
  });
});
