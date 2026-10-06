/**
 * @fileoverview Tool Result Card Component
 * @module components/ToolResultCard
 *
 * A versatile component for displaying AI agent tool execution results.
 * Renders different UI based on the tool type (create_file, update_file,
 * edit_file, delete_file, query_files, update_project) and result status
 * (pending, success, error, conflict).
 *
 * Features:
 * - Tool call loading state with animated spinner
 * - Success/error states with appropriate icons
 * - Diff-style display for file edits (replace, insert, delete)
 * - Conflict display with severity levels and suggestions
 * - Response card with action buttons (apply, retry, copy)
 * - Used snippets display with view links
 * - Internationalization support via react-i18next
 */

import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../lib/i18n';
import {
  CheckCircle2,
  AlertCircle,
  XCircle,
  Copy,
  RotateCcw,
  FileText,
  Info,
  Lightbulb,
  Edit3,
  ArrowRight,
  Plus,
  Minus,
  Loader2,
  FilePlus,
  FileEdit,
  Trash2,
  Search,
  User,
  BookOpen,
  ScrollText,
  Settings,
  Layers,
} from 'lucide-react';
import type { Conflict, AgentResponse, FileEditUndoTarget } from '../types';

const quote = (text: string) => {
  const isZh = i18n.language === 'zh';
  return `${isZh ? '「' : '"'}${text}${isZh ? '」' : '"'}`;
};

const isValidUndoTimestamp = (value: unknown): value is string => {
  if (typeof value !== 'string') return false;
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/,
  );
  if (!match) return false;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    return false;
  }

  const isLeapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, isLeapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (day < 1 || day > daysInMonth[month - 1]) return false;

  const timezone = match[8];
  if (timezone !== 'Z') {
    const offsetHour = Number(timezone.slice(1, 3));
    const offsetMinute = Number(timezone.slice(4, 6));
    if (offsetHour > 23 || offsetMinute > 59) return false;
  }
  return true;
};

/**
 * Props for the ToolResultCard component.
 *
 * @interface ToolResultCardProps
 */
interface ToolResultCardProps {
  /**
   * The type of content to render.
   * - 'response': Full agent response with text, actions, and optional conflicts
   * - 'tool_call': Tool execution in progress (loading state)
   * - 'tool_result': Completed tool execution result
   * - 'conflict': Conflict warning with severity and suggestions
   */
  type: 'response' | 'tool_call' | 'tool_result' | 'conflict';

  /**
   * Agent response data for type='response'.
   * Contains text, apply_action, used_snippets, conflicts, and reasoning.
   */
  response?: AgentResponse;

  /**
   * Name of the tool being executed or completed.
   * Used to determine icon and display label.
   * @example 'create_file', 'update_file', 'query_files'
   */
  toolName?: string;

  /**
   * Tool execution result or arguments.
   * For tool_call: contains arguments (title, file_type, etc.)
   * For tool_result: contains result data (id, content, details, etc.)
   */
  result?: Record<string, unknown>;

  /**
   * Error message if tool execution failed.
   * Displayed with error styling when present.
   */
  error?: string;

  /**
   * Whether the tool is currently executing.
   * Shows loading spinner and "Processing..." text when true.
   * @default false
   */
  isPending?: boolean;

  /**
   * Callback to undo a file edit operation.
   * Called with immutable edit provenance when undo button is clicked.
   * Only shown for successful edit_file results.
   */
  onUndo?: (target: FileEditUndoTarget) => void;

  /**
   * Callback to apply the response content.
   * Shown as primary action button for type='response'.
   */
  onApply?: () => void;

  /**
   * Callback to retry the operation.
   * Shown as secondary action button.
   */
  onRetry?: () => void;

  /**
   * Callback to copy the response text.
   * Shown as tertiary action button.
   */
  onCopy?: () => void;

  /**
   * Callback to view a snippet in detail.
   * Called with the snippet ID when view button is clicked.
   */
  onViewSnippet?: (snippetId: string) => void;
}

/**
 * Returns CSS classes for severity-based text and border colors.
 *
 * @param severity - Severity level ('high', 'medium', 'low')
 * @returns CSS classes for text and border colors
 *
 * @example
 * const colorClass = getSeverityColor('high'); // 'text-[hsl(var(--error))] border-[hsl(var(--error))]'
 */
const getSeverityColor = (severity: string) => {
  switch (severity) {
    case 'high':
      return 'text-[hsl(var(--error))] border-[hsl(var(--error))]';
    case 'medium':
      return 'text-[hsl(var(--warning))] border-[hsl(var(--warning))]';
    case 'low':
      return 'text-[hsl(var(--success))] border-[hsl(var(--success))]';
    default:
      return 'text-[hsl(var(--text-secondary))] border-[hsl(var(--text-secondary))]';
  }
};

/**
 * Returns CSS classes for severity-based background colors.
 *
 * @param severity - Severity level ('high', 'medium', 'low')
 * @returns CSS classes for background color
 *
 * @example
 * const bgClass = getSeverityBgColor('high'); // 'bg-[hsl(var(--error)/0.1)]'
 */
const getSeverityBgColor = (severity: string) => {
  switch (severity) {
    case 'high':
      return 'bg-[hsl(var(--error)/0.1)]';
    case 'medium':
      return 'bg-[hsl(var(--warning)/0.1)]';
    case 'low':
      return 'bg-[hsl(var(--success)/0.1)]';
    default:
      return 'bg-[hsl(var(--bg-tertiary))]';
  }
};

/**
 * Returns display icon and localized label for a tool name.
 *
 * @param toolName - Internal tool name (e.g., 'create_file', 'update_file')
 * @param t - Translation function from react-i18next
 * @returns Object with icon React node and localized label string
 *
 * @example
 * const { icon, label } = getToolDisplayInfo('create_file', t);
 * // icon: <FilePlus /> with success color
 * // label: t('chat:tool.create_file')
 */
const getToolDisplayInfo = (toolName: string, t: (key: string) => string): { icon: React.ReactNode; label: string } => {
  switch (toolName) {
    case 'create_file':
      return { 
        icon: <FilePlus className="w-4 h-4 text-[hsl(var(--success-light))]" />, 
        label: t('chat:tool.create_file')
      };
    case 'update_file':
      return { 
        icon: <FileEdit className="w-4 h-4 text-[hsl(var(--accent-primary))]" />, 
        label: t('chat:tool.update_file')
      };
    case 'edit_file':
      return { 
        icon: <Edit3 className="w-4 h-4 text-[hsl(var(--success-light))]" />, 
        label: t('chat:tool.edit_file')
      };
    case 'delete_file':
      return { 
        icon: <Trash2 className="w-4 h-4 text-[hsl(var(--error))]" />, 
        label: t('chat:tool.delete_file')
      };
    case 'query_files':
      return { 
        icon: <Search className="w-4 h-4 text-[hsl(var(--ref-tag-text))]" />, 
        label: t('chat:tool.query_files')
      };
    case 'update_project':
      return {
        icon: <Settings className="w-4 h-4 text-[hsl(var(--warning))]" />,
        label: t('chat:tool.update_project')
      };
    case 'parallel_execute':
      return {
        icon: <Layers className="w-4 h-4 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.parallel_execute')
      };
    case 'load_skill':
      return {
        icon: <BookOpen className="w-4 h-4 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.load_skill')
      };
    case 'read_skill_resource':
      return {
        icon: <FileText className="w-4 h-4 text-[hsl(var(--accent-primary))]" />,
        label: t('chat:tool.read_skill_resource')
      };
    default:
      return { 
        icon: <FileText className="w-4 h-4 text-[hsl(var(--ref-tag-text))]" />, 
        label: toolName 
      };
  }
};

/**
 * Returns display icon and localized label for a file type.
 *
 * @param fileType - File type (outline, draft, character, lore, snippet)
 * @param t - Translation function from react-i18next
 * @returns Object with icon React node and localized label string
 *
 * @example
 * const { icon, label } = getFileTypeInfo('outline', t);
 * // icon: <ScrollText /> with accent color
 * // label: t('chat:fileType.outline')
 */
const getFileTypeInfo = (fileType: string, t: (key: string) => string): { icon: React.ReactNode; label: string } => {
  switch (fileType) {
    case 'outline':
      return { 
        icon: <ScrollText className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />, 
        label: t('chat:fileType.outline')
      };
    case 'draft':
      return { 
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--success-light))]" />, 
        label: t('chat:fileType.draft')
      };
    case 'character':
      return { 
        icon: <User className="w-3.5 h-3.5 text-[hsl(var(--warning))]" />, 
        label: t('chat:fileType.character')
      };
    case 'lore':
      return { 
        icon: <BookOpen className="w-3.5 h-3.5 text-[hsl(var(--ref-tag-text))]" />, 
        label: t('chat:fileType.lore')
      };
    case 'snippet':
      return { 
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />, 
        label: t('chat:fileType.snippet')
      };
    default:
      return { 
        icon: <FileText className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />, 
        label: t('chat:fileType.default')
      };
  }
};

/**
 * Returns localized action text for an apply action type.
 *
 * @param applyAction - Type of apply action (insert, replace, new_snippet, reference_only)
 * @param t - Translation function from react-i18next
 * @returns Localized action text for button label
 *
 * @example
 * const text = getActionText('insert', t); // t('chat:action.insert')
 */
const getActionText = (applyAction: string, t: (key: string) => string) => {
  switch (applyAction) {
    case 'insert':
      return t('chat:action.insert');
    case 'replace':
      return t('chat:action.replace');
    case 'new_snippet':
      return t('chat:action.new_snippet');
    case 'reference_only':
      return t('chat:action.reference_only');
    default:
      return t('chat:action.apply');
  }
};

/**
 * Determines if an apply action can be performed.
 * Reference-only responses cannot be applied.
 *
 * @param applyAction - Type of apply action
 * @returns True if the action can be applied, false otherwise
 */
const getCanApply = (applyAction: string) => {
  return applyAction !== 'reference_only';
};

/**
 * Formats content length for display, converting large values to k-characters.
 *
 * @param length - Content length in characters
 * @param t - Translation function with interpolation support
 * @returns Formatted string (e.g., "150 字符" or "2.5k 字符")
 *
 * @example
 * formatContentLength(500, t);  // '500 字符'
 * formatContentLength(2500, t); // '2.5k 字符'
 */
const formatContentLength = (length: number, t: (key: string, options?: Record<string, unknown>) => string): string => {
  if (length < 1000) return t('chat:size.characters', { length });
  return t('chat:size.kCharacters', { length: (length / 1000).toFixed(1) });
};

/**
 * Component for displaying AI tool execution results.
 *
 * Renders appropriate UI based on the type prop:
 * - **tool_call**: Shows loading state with tool name and description
 * - **tool_result**: Shows success/error with tool-specific details
 * - **conflict**: Shows conflict warning with severity and suggestions
 * - **response**: Shows full response with text, actions, and snippets
 *
 * The component handles multiple tool types with specialized rendering:
 * - create_file: Shows created file info with type and size
 * - update_file: Shows updated file info
 * - edit_file: Shows detailed diff with expandable changes
 * - delete_file: Shows deletion confirmation
 * - query_files: Shows found files list
 * - update_project: Shows updated project fields
 *
 * @param props - Component props (see ToolResultCardProps interface)
 * @returns React element or null if type doesn't match any case
 *
 * @example
 * // Tool call loading state
 * <ToolResultCard
 *   type="tool_call"
 *   toolName="create_file"
 *   result={{ title: "Chapter 1", file_type: "draft" }}
 *   isPending={true}
 * />
 *
 * @example
 * // Tool result success state
 * <ToolResultCard
 *   type="tool_result"
 *   toolName="edit_file"
 *   result={{ id: "file-123", undo: { before_version_number: 3, expected_after_updated_at: "2026-10-06T12:34:56Z" } }}
 *   onUndo={(target) => handleUndo(target)}
 * />
 *
 * @example
 * // Full response with actions
 * <ToolResultCard
 *   type="response"
 *   response={{
 *     text: "Generated content...",
 *     apply_action: "insert",
 *     used_snippets: [{ id: "1", title: "Context" }]
 *   }}
 *   onApply={handleApply}
 *   onCopy={handleCopy}
 *   onViewSnippet={(id) => viewSnippet(id)}
 * />
 */
const ToolResultCardComponent: React.FC<ToolResultCardProps> = ({
  type,
  response,
  toolName,
  result,
  error,
  isPending,
  onUndo,
  onApply,
  onRetry,
  onCopy,
  onViewSnippet,
}) => {
  const { t } = useTranslation(['chat', 'common']);

  // Memoize event handlers to prevent unnecessary re-renders
  const handleUndo = useCallback((target: FileEditUndoTarget) => {
    if (onUndo) {
      onUndo(target);
    }
  }, [onUndo]);

  const handleViewSnippet = useCallback((snippetId: string) => {
    if (onViewSnippet) {
      onViewSnippet(snippetId);
    }
  }, [onViewSnippet]);

  // Render Tool Call (loading state)
  if (type === 'tool_call' && toolName) {
    const { icon, label } = getToolDisplayInfo(toolName, t);

    // Extract user-friendly info from arguments
    const args = result || {};
    const title = args.title as string | undefined;
    const fileType = args.file_type as string | undefined;
    
    // Build description based on tool type
    let description = '';
    if (toolName === 'create_file' && title) {
      const typeInfo = fileType ? getFileTypeInfo(fileType, t) : null;
      description = typeInfo ? `${typeInfo.label}${quote(title)}` : quote(title);
    } else if (toolName === 'query_files') {
      const queryFileType = args.file_type as string | undefined;
      if (queryFileType) {
        const typeInfo = getFileTypeInfo(queryFileType, t);
        description = t('chat:tool.query_description', { label: typeInfo.label });
      } else {
        description = t('chat:tool.query_files_default');
      }
    } else if (toolName === 'update_project') {
      description = t('chat:tool.update_status_description');
    } else if (toolName === 'load_skill' && typeof args.name === 'string' && args.name) {
      description = quote(args.name);
    } else if (toolName === 'read_skill_resource' && typeof args.path === 'string' && args.path) {
      description = typeof args.name === 'string' && args.name
        ? `${quote(args.name)} · ${args.path}`
        : args.path;
    } else if (title) {
      description = quote(title);
    }
    
    return (
      <div className="bg-[hsl(var(--ref-tag-bg))] border border-[hsl(var(--border-color))] rounded-lg px-3 py-2">
        <div className="flex items-center gap-2">
          {isPending ? (
            <Loader2 className="w-4 h-4 text-[hsl(var(--accent-primary))] animate-spin" />
          ) : (
            icon
          )}
          <span className="text-sm text-[hsl(var(--text-primary))]">
            {label}
            {description && <span className="text-[hsl(var(--text-secondary))]"> {description}</span>}
          </span>
          {isPending && (
            <span className="text-xs text-[hsl(var(--text-secondary))] animate-pulse ml-auto">
              {t('chat:tool.processing_ellipsis')}
            </span>
          )}
        </div>
      </div>
    );
  }

  // Render Tool Result
  if (type === 'tool_result' && toolName) {
    // Handle error case
    if (error) {
      const { label } = getToolDisplayInfo(toolName, t);
      const userMessage = (result?.user_message as string | undefined) || error;
      return (
        <div className="bg-[hsl(var(--diff-remove-bg))] border border-[hsl(var(--error))] rounded-lg px-3 py-2">
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-[hsl(var(--error))]" />
            <span className="text-sm text-[hsl(var(--error))]">
              {t('chat:tool.failed', { label })}
            </span>
          </div>
          <p className="text-xs text-[hsl(var(--text-secondary))] mt-1 ml-6">
            {userMessage}
          </p>
        </div>
      );
    }

    // Handle success cases by tool type
    const data = (result?.data || result) as Record<string, unknown>;
    
    // create_file success
    if (toolName === 'create_file' && data) {
      const title = data.title as string || t('common:untitled');
      const fileType = data.file_type as string || '';
      const contentLength = typeof data.content === 'string' ? data.content.length : 0;
      const typeInfo = getFileTypeInfo(fileType, t);
      
      return (
        <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg px-3 py-2">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[hsl(var(--success-light))]" />
            <span className="shrink-0 text-sm text-[hsl(var(--success-light))]">
              {t('chat:tool.created', { type: typeInfo.label })}
            </span>
            <span className="flex basis-full min-w-0 items-start gap-1 pl-6 text-sm text-[hsl(var(--text-primary))]">
              <span className="shrink-0">{typeInfo.icon}</span>
              <span className="min-w-0 break-words">{title}</span>
            </span>
            {contentLength > 0 && (
              <span className="text-xs text-[hsl(var(--text-secondary))] ml-auto">
                {formatContentLength(contentLength, t)}
              </span>
            )}
          </div>
        </div>
      );
    }
    
    // update_file success
    if (toolName === 'update_file' && data) {
      const title = data.title as string || t('chat:fileDefault');
      const contentLength = typeof data.content === 'string' ? data.content.length : 0;
      
      return (
        <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg px-3 py-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[hsl(var(--success-light))]" />
            <span className="text-sm text-[hsl(var(--success-light))]">
              {t('chat:tool.updated')}
            </span>
            <span className="text-sm text-[hsl(var(--text-primary))]">
              {quote(title)}
            </span>
            {contentLength > 0 && (
              <span className="text-xs text-[hsl(var(--text-secondary))] ml-auto">
                {formatContentLength(contentLength, t)}
              </span>
            )}
          </div>
        </div>
      );
    }
    
    // delete_file success
    if (toolName === 'delete_file') {
      return (
        <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg px-3 py-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[hsl(var(--success-light))]" />
            <span className="text-sm text-[hsl(var(--success-light))]">
              {t('chat:tool.deleted')}
            </span>
          </div>
        </div>
      );
    }
    
    // query_files success
    if (toolName === 'query_files' && data) {
      const files = Array.isArray(data) ? data : [];
      const count = files.length;
      
      if (count === 0) {
        return (
          <div className="bg-[hsl(var(--ref-tag-bg))] border border-[hsl(var(--border-color))] rounded-lg px-3 py-2">
            <div className="flex items-center gap-2">
              <Search className="w-4 h-4 text-[hsl(var(--text-secondary))]" />
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t('chat:tool.no_files_found')}
              </span>
            </div>
          </div>
        );
      }
      
      return (
        <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg px-3 py-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[hsl(var(--success-light))]" />
            <span className="text-sm text-[hsl(var(--success-light))]">
              {t('chat:tool.files_found', { count })}
            </span>
          </div>
          {count <= 5 && (
            <div className="mt-2 ml-6 space-y-1">
              {files.map((file: Record<string, unknown>, index: number) => {
                const fileTitle = file.title as string || t('common:untitled');
                const fileType = file.file_type as string || '';
                const typeInfo = getFileTypeInfo(fileType, t);
                return (
                  <div key={index} className="flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]">
                    {typeInfo.icon}
                    <span>{fileTitle}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      );
    }
    
    // update_project success
    if (toolName === 'update_project' && data) {
      const projectStatus = data.project_status as Record<string, unknown> | undefined;
      const rawUpdatedFields = data.updated_fields ?? projectStatus?.updated_fields;
      const updatedFields = Array.isArray(rawUpdatedFields)
        ? rawUpdatedFields.filter((field): field is string => typeof field === 'string')
        : [];
      const fieldLabels: Record<string, string> = {
        summary: t('chat:project.field.summary'),
        current_phase: t('chat:project.field.current_phase'),
        writing_style: t('chat:project.field.writing_style'),
        notes: t('chat:project.field.notes'),
      };

      const updatedLabels = updatedFields
        .map((f) => fieldLabels[f] || f)
        .join(t('common:separator'));
      
      return (
        <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg px-3 py-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[hsl(var(--success-light))]" />
            <span className="text-sm text-[hsl(var(--success-light))]">
              {t('chat:tool.status_updated')}
            </span>
            {updatedLabels && (
              <span className="text-xs text-[hsl(var(--text-secondary))]">
                ({updatedLabels})
              </span>
            )}
          </div>
        </div>
      );
    }
    
    // edit_file success - special detailed rendering
    if (toolName === 'edit_file' && data) {
      const fileId = typeof data.id === 'string' ? data.id.trim() : '';
      const undo = data.undo && typeof data.undo === 'object' && !Array.isArray(data.undo)
        ? data.undo as Record<string, unknown>
        : null;
      const beforeVersionNumber = undo?.before_version_number;
      const expectedAfterUpdatedAt = undo?.expected_after_updated_at;
      const undoTarget: FileEditUndoTarget | null =
        fileId
        && typeof beforeVersionNumber === 'number'
        && Number.isSafeInteger(beforeVersionNumber)
        && beforeVersionNumber > 0
        && isValidUndoTimestamp(expectedAfterUpdatedAt)
          ? {
              fileId,
              beforeVersionNumber,
              expectedAfterUpdatedAt,
            }
          : null;
      const details = data.details as Array<{
        op: string;
        old_preview?: string;
        new_preview?: string;
        anchor_preview?: string;
        text_preview?: string;
        text_len?: number;
        deleted_preview?: string;
        count?: number;
      }> || [];
      
      const getOpIcon = (op: string) => {
        switch (op) {
          case 'replace':
            return <ArrowRight className="w-3 h-3 text-[hsl(var(--ref-tag-text))]" />;
          case 'append':
          case 'prepend':
          case 'insert_after':
          case 'insert_before':
            return <Plus className="w-3 h-3 text-[hsl(var(--success-light))]" />;
          case 'delete':
            return <Minus className="w-3 h-3 text-[hsl(var(--error))]" />;
          default:
            return <Edit3 className="w-3 h-3 text-[hsl(var(--ref-tag-text))]" />;
        }
      };
      
      const getOpLabel = (op: string) => {
        switch (op) {
          case 'replace': return t('chat:edit.replace');
          case 'append': return t('chat:edit.append');
          case 'prepend': return t('chat:edit.prepend');
          case 'insert_after': return t('chat:edit.insert_after');
          case 'insert_before': return t('chat:edit.insert_before');
          case 'delete': return t('chat:edit.label_delete');
          default: return op;
        }
      };
      
      return (
        <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg p-3">
          <div className="flex items-center gap-2 mb-2">
            <Edit3 className="w-4 h-4 text-[hsl(var(--success-light))]" />
            <span className="text-sm text-[hsl(var(--success-light))] font-medium">
              {t('chat:tool.edit_success')}
            </span>
            {onUndo && undoTarget && (
              <button
                onClick={() => handleUndo(undoTarget)}
                className="flex items-center gap-1 px-2 py-1 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors ml-auto"
                title={t('chat:tool.undo_edit')}
              >
                <RotateCcw className="w-3 h-3" />
                {t('common:undo')}
              </button>
            )}
          </div>
          
          {details && details.length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer select-none text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] flex items-center gap-1 list-none"
                style={{ listStyle: 'none' }}
              >
                <svg
                  className="w-3 h-3 transition-transform duration-200"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                {t('chat:tool.view_edit_details', { count: details.length })}
              </summary>
              <div className="space-y-2 mt-2">
                {details.map((detail, index) => (
                  <div key={index} className="bg-[hsl(var(--bg-tertiary))] rounded p-2 text-xs">
                    <div className="flex items-center gap-1 mb-1">
                      {getOpIcon(detail.op)}
                      <span className="text-[hsl(var(--ref-tag-text))] font-medium">
                        {getOpLabel(detail.op)}
                      </span>
                      {detail.count && detail.count > 1 && (
                        <span className="text-[hsl(var(--text-secondary))]">
                          × {detail.count}
                        </span>
                      )}
                    </div>

                    {detail.op === 'replace' && (
                      <div className="space-y-2 mt-1">
                        <div className="bg-[hsl(var(--diff-remove-bg))] p-2 rounded border-l-2 border-[hsl(var(--diff-remove-text))]">
                          <div className="flex items-center gap-1 mb-1">
                            <Minus className="w-3 h-3 text-[hsl(var(--diff-remove-text))]" />
                            <span className="text-xs text-[hsl(var(--diff-remove-text))]">{t('chat:edit.label_delete')}</span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                            {detail.old_preview}
                          </div>
                        </div>
                        <div className="bg-[hsl(var(--diff-add-bg))] p-2 rounded border-l-2 border-[hsl(var(--diff-add-text))]">
                          <div className="flex items-center gap-1 mb-1">
                            <Plus className="w-3 h-3 text-[hsl(var(--diff-add-text))]" />
                            <span className="text-xs text-[hsl(var(--diff-add-text))]">{t('chat:edit.label_add')}</span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                            {detail.new_preview}
                          </div>
                        </div>
                      </div>
                    )}

                    {(detail.op === 'append' || detail.op === 'prepend') && (
                      <div className="bg-[hsl(var(--diff-add-bg))] p-2 rounded border-l-2 border-[hsl(var(--diff-add-text))] mt-1">
                        <div className="flex items-center gap-1 mb-1">
                          <Plus className="w-3 h-3 text-[hsl(var(--diff-add-text))]" />
                          <span className="text-xs text-[hsl(var(--diff-add-text))]">
                            {detail.op === 'append' ? t('chat:edit.append_content') : t('chat:edit.prepend_content')}
                            {detail.text_len && detail.text_len > 200 && (
                              <span className="ml-1 text-[hsl(var(--text-secondary))]">
                                ({formatContentLength(detail.text_len, t)})
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                          {detail.text_preview}
                        </div>
                      </div>
                    )}

                    {(detail.op === 'insert_after' || detail.op === 'insert_before') && (
                      <div className="space-y-2 mt-1">
                        <div className="bg-[hsl(var(--bg-secondary))] p-2 rounded border-l-2 border-[hsl(var(--text-secondary))]">
                          <div className="flex items-center gap-1 mb-1">
                            <span className="text-xs text-[hsl(var(--text-secondary))]">
                              {detail.op === 'insert_after' ? t('chat:edit.insert_after_label') : t('chat:edit.insert_before_label')}
                            </span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-secondary))] whitespace-pre-wrap break-words font-normal max-h-40 overflow-auto">
                            {detail.anchor_preview}
                          </div>
                        </div>
                        <div className="bg-[hsl(var(--diff-add-bg))] p-2 rounded border-l-2 border-[hsl(var(--diff-add-text))]">
                          <div className="flex items-center gap-1 mb-1">
                            <Plus className="w-3 h-3 text-[hsl(var(--diff-add-text))]" />
                            <span className="text-xs text-[hsl(var(--diff-add-text))]">
                              {t('chat:edit.insert_content')}
                              {detail.text_len && detail.text_len > 200 && (
                                <span className="ml-1 text-[hsl(var(--text-secondary))]">
                                  ({formatContentLength(detail.text_len, t)})
                                </span>
                              )}
                            </span>
                          </div>
                          <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                            {detail.text_preview}
                          </div>
                        </div>
                      </div>
                    )}

                    {detail.op === 'delete' && (
                      <div className="bg-[hsl(var(--diff-remove-bg))] p-2 rounded border-l-2 border-[hsl(var(--diff-remove-text))] mt-1">
                        <div className="flex items-center gap-1 mb-1">
                          <Minus className="w-3 h-3 text-[hsl(var(--diff-remove-text))]" />
                          <span className="text-xs text-[hsl(var(--diff-remove-text))]">{t('chat:edit.delete_content')}</span>
                        </div>
                        <div className="text-xs leading-5 text-[hsl(var(--text-primary))] whitespace-pre-wrap break-words font-normal max-h-56 overflow-auto">
                          {detail.deleted_preview}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      );
    }
    
    // parallel_execute success - show per-subtask status so partial failures
    // are visible (the aggregate envelope stays "success" to preserve data).
    if (toolName === 'parallel_execute' && data) {
      const tasks = Array.isArray(data.tasks)
        ? (data.tasks as Array<Record<string, unknown>>)
        : [];
      const total = (data.total_tasks as number) ?? tasks.length;
      const completed =
        (data.completed as number) ??
        tasks.filter((task) => task.status === 'completed').length;
      const failed =
        (data.failed as number) ??
        tasks.filter((task) => task.status === 'failed').length;
      const anyFailed = failed > 0;
      const accentClass = anyFailed
        ? 'text-[hsl(var(--warning))]'
        : 'text-[hsl(var(--success-light))]';

      return (
        <div
          className={`bg-[hsl(var(--result-bg))] border rounded-lg px-3 py-2 ${
            anyFailed
              ? 'border-[hsl(var(--warning))]'
              : 'border-[hsl(var(--result-border))]'
          }`}
        >
          <div className="flex items-center gap-2">
            <Layers className={`w-4 h-4 ${accentClass}`} />
            <span className={`text-sm ${accentClass}`}>
              {t('chat:tool.parallel_summary', { total, completed, failed })}
            </span>
          </div>
          {tasks.length > 0 && (
            <div className="mt-2 space-y-1">
              {tasks.map((task, index) => {
                const isFailed = task.status === 'failed';
                const desc =
                  (task.description as string) ||
                  (task.type as string) ||
                  `#${index + 1}`;
                return (
                  <div
                    key={(task.id as string) || index}
                    className="flex items-start gap-2 text-xs"
                  >
                    {isFailed ? (
                      <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[hsl(var(--error))]" />
                    ) : (
                      <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[hsl(var(--success-light))]" />
                    )}
                    <div className="min-w-0">
                      <span className="text-[hsl(var(--text-secondary))]">
                        [{task.type as string}]
                      </span>{' '}
                      <span className="text-[hsl(var(--text-primary))] break-words">
                        {desc}
                      </span>
                      {isFailed && task.error ? (
                        <div className="text-[hsl(var(--error))] break-words">
                          {task.error as string}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      );
    }

    // load_skill / read_skill_resource success: the server only ships the skill
    // name, resource path and sizes (full instructions/content go to the model only)
    if ((toolName === 'load_skill' || toolName === 'read_skill_resource') && data) {
      const { label } = getToolDisplayInfo(toolName, t);
      const skillName = typeof data.skill_name === 'string' ? data.skill_name : '';
      const path = typeof data.path === 'string' ? data.path : '';
      const detail = [skillName ? quote(skillName) : '', path].filter(Boolean).join(' · ');
      return (
        <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg px-3 py-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[hsl(var(--success-light))]" />
            <span className="text-sm text-[hsl(var(--success-light))]">
              {label}{t('chat:tool.complete_suffix')}
            </span>
            {detail && (
              <span className="text-sm text-[hsl(var(--text-primary))] truncate">{detail}</span>
            )}
          </div>
        </div>
      );
    }

    // Default fallback - show minimal success message without technical details
    const { label } = getToolDisplayInfo(toolName, t);
    return (
      <div className="bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg px-3 py-2">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-[hsl(var(--success-light))]" />
          <span className="text-sm text-[hsl(var(--success-light))]">
            {label}{t('chat:tool.complete_suffix')}
          </span>
        </div>
      </div>
    );
  }

  // Render Conflict
  if (type === 'conflict' && result?.conflict) {
    const conflict = result.conflict as Conflict;
    return (
      <div
        className={`border rounded-lg p-3 mb-2 ${getSeverityColor(conflict.severity).split(' ')[1]} ${getSeverityBgColor(conflict.severity)}`}
      >
        <div className="flex items-start gap-2 mb-2">
          {conflict.severity === 'high' ? (
            <XCircle className="w-4 h-4 text-[hsl(var(--error))] shrink-0 mt-0.5" />
          ) : conflict.severity === 'medium' ? (
            <AlertCircle className="w-4 h-4 text-[hsl(var(--warning))] shrink-0 mt-0.5" />
          ) : (
            <Lightbulb className="w-4 h-4 text-[hsl(var(--success-light))] shrink-0 mt-0.5" />
          )}
          <div className="flex-1">
            <h4 className={`text-sm font-medium mb-1 ${getSeverityColor(conflict.severity).split(' ')[0]}`}>
              {conflict.title}
            </h4>
            <p className="text-sm text-[hsl(var(--text-secondary))] mb-2">
              {conflict.description}
            </p>
            {conflict.suggestions && conflict.suggestions.length > 0 && (
              <div className="mt-2">
                <div className="text-xs text-[hsl(var(--text-secondary))] mb-1">{t('chat:conflict.suggestions_label')}</div>
                <ul className="text-sm text-[hsl(var(--text-secondary))] space-y-1">
                  {conflict.suggestions.map((suggestion, sIndex) => (
                    <li key={sIndex} className="flex items-start gap-2">
                      <span className="text-[hsl(var(--accent-primary))]">•</span>
                      <span>{suggestion}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Render Full Response
  if (type === 'response' && response) {
    return (
      <div className="mb-4">
        {/* Response Text Card */}
        <div className="bg-[hsl(var(--success)/0.1)] border border-[hsl(var(--success)/0.3)] rounded-lg p-4 mb-4">
          <div className="flex items-center gap-2 mb-2">
            <FileText className="w-4 h-4 text-[hsl(var(--success-light))]" />
            <span className="text-sm text-[hsl(var(--success-light))] font-medium">{t('chat:response.generated_result')}</span>
          </div>

          <div className="text-[hsl(var(--text-secondary))] text-sm leading-relaxed whitespace-pre-wrap mb-4">
            {response.text}
          </div>

          {/* Action Buttons */}
          {(onApply || onRetry || onCopy) && (
            <div className="flex items-center gap-2 flex-wrap">
              {onApply && getCanApply(response.apply_action) && (
                <button
                  onClick={onApply}
                  className="flex items-center gap-1 px-3 py-1.5 bg-[hsl(var(--success))] text-white rounded-md text-sm font-medium hover:bg-[hsl(var(--success-dark))] transition-colors"
                >
                  <CheckCircle2 className="w-4 h-4" />
                  {getActionText(response.apply_action, t)}
                </button>
              )}

              {onRetry && (
                <button
                  onClick={onRetry}
                  className="flex items-center gap-1 px-3 py-1.5 bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] rounded-md text-sm hover:bg-[hsl(var(--bg-hover))] transition-colors"
                >
                  <RotateCcw className="w-4 h-4" />
                  {t('common:retry')}
                </button>
              )}

              {onCopy && (
                <button
                  onClick={onCopy}
                  className="flex items-center gap-1 px-3 py-1.5 bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] rounded-md text-sm hover:bg-[hsl(var(--bg-hover))] transition-colors"
                >
                  <Copy className="w-4 h-4" />
                  {t('common:copy')}
                </button>
              )}
            </div>
          )}
        </div>

        {/* Used Snippets */}
        {response.used_snippets && response.used_snippets.length > 0 && (
          <div className="bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] rounded-lg p-4 mb-4">
            <div className="flex items-center gap-2 mb-3">
              <Info className="w-4 h-4 text-[hsl(var(--info))]" />
              <span className="text-sm text-[hsl(var(--info))] font-medium">{t('chat:response.used_snippets')}</span>
            </div>

            <div className="space-y-2">
              {response.used_snippets.map((snippet: { id?: string; title?: string; content?: string }, index: number) => (
                <div
                  key={index}
                  className="flex items-start gap-2 p-2 bg-[hsl(var(--bg-secondary))] rounded-md"
                >
                  <span className="text-xs text-[hsl(var(--text-secondary))] shrink-0 mt-0.5">
                    {index + 1}.
                  </span>
                  <div className="flex-1">
                    <div className="text-sm text-[hsl(var(--text-primary))] font-medium mb-1">
                      {snippet.title || `Snippet #${snippet.id}`}
                    </div>
                    <div className="text-xs text-[hsl(var(--text-secondary))]">
                      {snippet.content?.substring(0, 100)}
                      {(snippet.content?.length ?? 0) > 100 && '...'}
                    </div>
                  </div>
                  {onViewSnippet && snippet.id && (
                    <button
                      onClick={() => handleViewSnippet(snippet.id!)}
                      className="text-xs text-[hsl(var(--accent-primary))] hover:text-[hsl(var(--accent-primary))] shrink-0"
                    >
                      {t('common:view')}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Conflicts */}
        {response.conflicts && response.conflicts.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-[hsl(var(--warning))]" />
              <span className="text-sm text-[hsl(var(--warning))] font-medium">
                {t('chat:response.conflicts_detected', { count: response.conflicts.length })}
              </span>
            </div>

            {response.conflicts.map((conflict, index) => (
              <div
                key={index}
                className={`border rounded-lg p-4 ${getSeverityColor(conflict.severity).split(' ')[1]} ${getSeverityBgColor(conflict.severity)}`}
              >
                <div className="flex items-start gap-2 mb-2">
                  {conflict.severity === 'high' ? (
                    <XCircle className="w-5 h-5 text-[hsl(var(--error))] shrink-0" />
                  ) : conflict.severity === 'medium' ? (
                    <AlertCircle className="w-5 h-5 text-[hsl(var(--warning))] shrink-0" />
                  ) : (
                    <Lightbulb className="w-5 h-5 text-[hsl(var(--success-light))] shrink-0" />
                  )}

                  <div className="flex-1">
                    <h4 className={`text-sm font-medium mb-1 ${getSeverityColor(conflict.severity).split(' ')[0]}`}>
                      {conflict.title}
                    </h4>
                    <p className="text-sm text-[hsl(var(--text-secondary))] mb-2">
                      {conflict.description}
                    </p>

                    {conflict.location && (
                      <div className="text-xs text-[hsl(var(--text-secondary))] mb-2">
                        {t('chat:conflict.location', { location: conflict.location })}
                      </div>
                    )}

                    {conflict.suggestions && conflict.suggestions.length > 0 && (
                      <div className="mt-3">
                        <div className="text-xs text-[hsl(var(--text-secondary))] mb-1">{t('chat:conflict.fix_suggestions')}</div>
                        <ul className="text-sm text-[hsl(var(--text-secondary))] space-y-1">
                          {conflict.suggestions.map((suggestion, sIndex) => (
                            <li key={sIndex} className="flex items-start gap-2">
                              <span className="text-[hsl(var(--accent-primary))]">•</span>
                              <span>{suggestion}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Reasoning */}
        {response.reasoning && (
          <div className="mt-4 p-3 bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] rounded-lg">
            <div className="flex items-start gap-2">
              <Lightbulb className="w-4 h-4 text-[hsl(var(--warning))] shrink-0 mt-0.5" />
              <div>
                <div className="text-xs text-[hsl(var(--text-secondary))] mb-1">{t('chat:response.reasoning_label')}</div>
                <div className="text-sm text-[hsl(var(--text-secondary))]">
                  {response.reasoning}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return null;
};

export const ToolResultCard = React.memo(ToolResultCardComponent);

export default ToolResultCard;
