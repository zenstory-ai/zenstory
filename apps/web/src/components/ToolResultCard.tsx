/**
 * @fileoverview Tool Result Card Component
 * @module components/ToolResultCard
 *
 * Thin shell routing AI agent tool execution calls & results to focused card
 * renderers under `./chat/toolCards/*`.
 */

import React, { useCallback, useContext } from 'react';
import { useTranslation } from 'react-i18next';
import type { FileEditUndoTarget } from '../types';
import { UndoBlockedContext, UndoneEditsContext } from '../lib/undoneEdits';
import type { ToolResultCardProps } from './chat/toolCards/toolCardHelpers';
import { ToolCallPendingCard } from './chat/toolCards/ToolCallPendingCard';
import { ToolFailureCard } from './chat/toolCards/ToolFailureCard';
import {
  BatchReplaceFilesCard,
  CreateFileCard,
  DeleteFileCard,
  EditFileCard,
  RenameFileCard,
  UpdateFileCard,
  WriteChapterCard,
} from './chat/toolCards/FileWriteToolCards';
import {
  FallbackToolCard,
  ParallelExecuteCard,
  QueryOrSearchFilesCard,
  ReadFilesCard,
  SkillToolCard,
  UpdateProjectCard,
} from './chat/toolCards/ReadAndMetaToolCards';

const ToolResultCardComponent: React.FC<ToolResultCardProps> = ({
  type,
  toolName,
  result,
  error,
  isPending,
  interrupted,
  onUndo,
  onRestoreDeletion,
  toolUseId,
  chatMessageId,
  onOpenFile,
  onOpenAIMemory,
}) => {
  const { t } = useTranslation(['chat', 'common']);
  const undoneEdits = useContext(UndoneEditsContext);
  const undoBlocked = useContext(UndoBlockedContext);

  const handleUndo = useCallback((target: FileEditUndoTarget) => {
    if (onUndo) {
      onUndo(target);
    }
  }, [onUndo]);

  if (type === 'tool_call' && toolName) {
    return (
      <ToolCallPendingCard
        toolName={toolName}
        args={result || {}}
        isPending={isPending}
        interrupted={interrupted}
        t={t}
      />
    );
  }

  if (type === 'tool_result' && toolName) {
    if (error) {
      return (
        <ToolFailureCard
          toolName={toolName}
          result={result}
          isPending={isPending}
          interrupted={interrupted}
          onOpenFile={onOpenFile}
          onOpenAIMemory={onOpenAIMemory}
          t={t}
        />
      );
    }

    const data = (result?.data || result) as Record<string, unknown>;
    const fileId = typeof data?.id === 'string' ? data.id.trim() : '';
    const sharedRowProps = { onOpenFile, isPending, interrupted };
    const undoProps = {
      chatMessageId,
      toolUseId,
      undoneEdits,
      undoBlocked,
      onUndo: onUndo ? handleUndo : undefined,
    };

    if (toolName === 'create_file' && data) {
      return <CreateFileCard data={data} fileId={fileId} t={t} {...sharedRowProps} />;
    }
    if (toolName === 'write_chapter' && data) {
      return <WriteChapterCard data={data} fileId={fileId} t={t} {...undoProps} {...sharedRowProps} />;
    }
    if (toolName === 'update_file' && data) {
      return <UpdateFileCard data={data} fileId={fileId} t={t} {...sharedRowProps} />;
    }
    if (toolName === 'rename_file' && data && typeof data === 'object' && !Array.isArray(data)) {
      return <RenameFileCard data={data} t={t} />;
    }
    if (toolName === 'delete_file') {
      const deleted = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
      return (
        <DeleteFileCard
          data={deleted}
          chatMessageId={chatMessageId}
          toolUseId={toolUseId}
          onRestoreDeletion={onRestoreDeletion}
          t={t}
        />
      );
    }
    if ((toolName === 'query_files' || toolName === 'search_files') && data) {
      return <QueryOrSearchFilesCard data={data} t={t} {...sharedRowProps} />;
    }
    if (toolName === 'read_files' && data) {
      return <ReadFilesCard data={data} t={t} {...sharedRowProps} />;
    }
    if (toolName === 'batch_replace_files' && data && !Array.isArray(data)) {
      return <BatchReplaceFilesCard data={data} t={t} {...undoProps} {...sharedRowProps} />;
    }
    if (toolName === 'update_project' && data) {
      return (
        <UpdateProjectCard
          data={data}
          isPending={isPending}
          interrupted={interrupted}
          onOpenAIMemory={onOpenAIMemory}
          t={t}
        />
      );
    }
    if (toolName === 'edit_file' && data) {
      return (
        <EditFileCard
          data={data}
          fileId={fileId}
          t={t}
          {...undoProps}
          {...sharedRowProps}
        />
      );
    }
    if (toolName === 'parallel_execute' && data) {
      return <ParallelExecuteCard data={data} t={t} />;
    }
    if ((toolName === 'load_skill' || toolName === 'read_skill_resource') && data) {
      return <SkillToolCard toolName={toolName} data={data} t={t} />;
    }
    return <FallbackToolCard toolName={toolName} t={t} />;
  }

  return null;
};

export const ToolResultCard = React.memo(ToolResultCardComponent);
