import type React from "react";
import type { FileUpdateVersionIntent } from "../../lib/api";
import type { DiffReviewState } from "../../types";

export type SaveOutcome = "saved" | "conflict" | "failed";

export type SaveResult =
  | { outcome: "saved"; updatedAt: string }
  | { outcome: "conflict" | "failed" };

export interface SaveSubmission {
  fileId?: string;
  title: string;
  content: string;
  baseUpdatedAt?: string;
  previousTitle: string;
  versionIntent?: FileUpdateVersionIntent;
}

export interface EditorServerBaseline {
  version: number;
  fileId: string;
  title: string;
  content: string;
  updatedAt?: string;
}

export interface EditorRecoveredDraft {
  capturedAt: string;
  serverTitle: string;
  serverContent: string;
}

export interface NaturalPolishRequestParams {
  projectId: string;
  fileId: string;
  fileType?: string;
  fileTitle?: string;
  selectedText?: string;
}

export interface SimpleEditorProps {
  /** Static UI only: never persisted as manuscript content. */
  emptyHint?: (focusEditor: () => void) => React.ReactNode;
  userId?: string;
  fileId?: string;
  projectId?: string;
  fileType?: string;
  fileTitle?: string;
  baseUpdatedAt?: string;
  /** The body of the server copy `baseUpdatedAt` belongs to (what is saved). */
  savedContent?: string;
  title: string;
  /** The author's editable body; streaming previews must never become a saved draft. */
  content: string;
  /** Display only while streaming; excluded from draft, dirty and save-token state. */
  streamingContent?: string;
  onTitleChange: (title: string) => void;
  onContentChange: (content: string) => void;
  onSave: (submission: SaveSubmission) => Promise<SaveResult>;
  onHistoryRestore?: () => Promise<void>;
  onFlushReady?: (flush: (() => Promise<SaveOutcome>) | null) => void;
  /**
   * The parent switched the editor to a newer server copy (for example after
   * an AI write). Each new `version` resets the draft, save token, dirty flag
   * and word-count baseline to this copy; the current `title`/`content` props
   * become the draft (an unsaved title rename stays dirty).
   */
  serverBaseline?: EditorServerBaseline;
  /**
   * While a comparison is open, the draft to keep locally if the editor goes
   * away (leaving the project, closing the page) instead of the raw draft;
   * null when this comparison has no such draft.
   */
  getReviewLeaveDraft?: () => { title: string; content: string; baseUpdatedAt?: string } | null;
  readOnly?: boolean;
  isStreaming?: boolean;
  /** Whether the chat agent is currently running a turn (disables editor-triggered AI actions). */
  isChatStreaming?: boolean;
  /**
   * AI 正在编辑这份文件（file_edit_start ~ file_edit_end 之间）。
   * 期间必须挂起防抖自动保存：编辑前拍下的整篇快照若在 AI 写完之后才发出，
   * 会把 AI 的改动整篇覆盖掉（服务端的乐观并发校验会挡下来并返回 409，
   * 但让用户吃一次冲突提示不如根本不发这次请求）。
   */
  isAiEditing?: boolean;
  recoveredDraft?: EditorRecoveredDraft;
  /** Dispatch a structured ui_action="natural_polish" turn to the chat agent (after flushing dirty edits). */
  onNaturalPolish?: (params: NaturalPolishRequestParams) => void;
  /** Focus and select the title field once on mount/open for freshly created files (desktop only). */
  autoFocusTitle?: boolean;
  onAutoFocusConsumed?: () => void;
  // Diff review props
  diffReviewState?: DiffReviewState | null;
  onAcceptEdit?: (editId: string) => void;
  onRejectEdit?: (editId: string) => void;
  onResetEdit?: (editId: string) => void;
  onAcceptAllEdits?: () => void;
  onRejectAllEdits?: () => void;
  onFinishReview?: () => void;
}
