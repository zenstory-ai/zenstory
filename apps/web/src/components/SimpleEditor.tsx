import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { DiffToolbar } from "./DiffToolbar";
import { EditorStreamingBanner, EditorStreamingCanvasWaiting } from "./EditorStreamingWaiting";
import { SelectionToolbar } from "./SelectionToolbar";
import { useTextQuote } from "../contexts/TextQuoteContext";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import { usePinchZoom } from "../hooks/useGestures";
import { useAnimatedPresence } from "../hooks/useAnimatedPresence";
import { EditorStatusBar } from "./editor/EditorStatusBar";
import { EditorTitleBar } from "./editor/EditorTitleBar";
import { useEditorPersistence } from "./editor/useEditorPersistence";
import { useEditorSelection } from "./editor/useEditorSelection";
import {
  SIMPLE_EDITOR_MIN_HEIGHT_PX,
  useEditorTextareaLayout,
} from "./editor/useEditorTextareaLayout";
import type { SimpleEditorProps } from "./editor/types";

export type { SaveOutcome, SaveResult, SaveSubmission } from "./editor/types";
import { useNaturalPolish } from "./editor/useNaturalPolish";

const FileVersionHistory = lazy(() =>
  import("./FileVersionHistory").then((m) => ({ default: m.FileVersionHistory }))
);
const DiffReviewSplitView = lazy(() =>
  import("./DiffReviewSplitView").then((m) => ({ default: m.DiffReviewSplitView }))
);

interface EditorEmptyHintProps {
  renderHint: (focusEditor: () => void) => React.ReactNode;
  onFocusEditor: () => void;
}

const EditorEmptyHint = ({ renderHint, onFocusEditor }: EditorEmptyHintProps) => (
  <>{renderHint(onFocusEditor)}</>
);

export const SimpleEditor = ({
  emptyHint,
  userId,
  fileId,
  projectId,
  fileType,
  fileTitle,
  baseUpdatedAt,
  savedContent,
  title,
  content,
  streamingContent,
  onTitleChange,
  onContentChange,
  onSave,
  onHistoryRestore,
  onFlushReady,
  serverBaseline,
  getReviewLeaveDraft,
  readOnly = false,
  isStreaming = false,
  isChatStreaming = false,
  isAiEditing = false,
  recoveredDraft,
  onNaturalPolish,
  autoFocusTitle = false,
  onAutoFocusConsumed,
  diffReviewState,
  onAcceptEdit,
  onRejectEdit,
  onResetEdit,
  onAcceptAllEdits,
  onRejectAllEdits,
  onFinishReview,
}: SimpleEditorProps) => {
  const displayContent = isStreaming ? streamingContent ?? content : content;
  const { t } = useTranslation(["editor"]);
  const { addQuote } = useTextQuote();
  const { isMobile, activePanel, switchToChat } = useMobileLayout();

  const [showVersionHistory, setShowVersionHistory] = useState(false);
  const isVersionHistoryOpen = showVersionHistory && Boolean(fileId);
  const { isMounted: isVersionHistoryMounted } = useAnimatedPresence(isVersionHistoryOpen, 150);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const contentAreaRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);

  // Check if in diff review mode
  const isReviewMode = Boolean(diffReviewState?.isReviewing && diffReviewState.fileId === fileId);

  const focusEditorBody = useCallback(() => {
    textareaRef.current?.focus();
  }, []);

  // Pinch-to-zoom gesture support
  const { zoom, bind: bindPinchZoom, resetZoom } = usePinchZoom(1, 0.5, 2.5);
  useEffect(() => {
    resetZoom();
  }, [fileId, resetZoom]);

  const { adjustTextareaHeight, handleContentScroll, resetAutoScroll } = useEditorTextareaLayout({
    textareaRef,
    contentAreaRef,
    displayContent,
    isStreaming,
  });

  const {
    selectedText,
    selectionPosition,
    mirrorRef,
    selectionRangeRef,
    isSelectionToolbarOpen,
    isSelectionToolbarMounted,
    clearSelectionState,
    dropSelection,
    handleSelection,
    closeSelectionToolbar,
    handleAddQuote,
  } = useEditorSelection({
    textareaRef,
    fileId,
    fileTitle,
    title,
    isMobile,
    activePanel,
    addQuote,
  });

  const handleFileSwitch = useCallback(
    (didFileChange: boolean) => {
      adjustTextareaHeight(true);
      resetAutoScroll();
      if (didFileChange) {
        clearSelectionState();
      }
    },
    [adjustTextareaHeight, clearSelectionState, resetAutoScroll]
  );

  const handleReviewExitHeightAdjust = useCallback(() => {
    adjustTextareaHeight(true);
  }, [adjustTextareaHeight]);

  const {
    isSaving,
    lastSaved,
    isDirty,
    dirtyRef,
    suppressRecoveredAutoSaveRef,
    latestContentRef,
    latestTitleRef,
    latestFileIdRef,
    isMountedRef,
    handleSave,
    handleSaveRef,
    markTitleChanged,
    markContentChanged,
    prepareHistoryRollback,
    handleRollback,
  } = useEditorPersistence({
    userId,
    fileId,
    projectId,
    baseUpdatedAt,
    savedContent,
    title,
    content,
    onTitleChange,
    onContentChange,
    onSave,
    onHistoryRestore,
    onFlushReady,
    serverBaseline,
    getReviewLeaveDraft,
    isStreaming,
    isAiEditing,
    isReviewMode,
    diffReviewState,
    recoveredDraft,
    showVersionHistory,
    onFileSwitch: handleFileSwitch,
    onReviewExitHeightAdjust: handleReviewExitHeightAdjust,
  });

  const {
    isLimitedAiQuota,
    isFlushingNaturalPolish,
    isNaturalPolishBusy,
    hasPolishableContent,
    startNaturalPolish,
  } = useNaturalPolish({
    projectId,
    fileId,
    fileType,
    fileTitle,
    displayContent,
    readOnly,
    isStreaming,
    isChatStreaming,
    isAiEditing,
    isReviewMode,
    textareaRef,
    selectionRangeRef,
    latestContentRef,
    latestTitleRef,
    latestFileIdRef,
    dirtyRef,
    suppressRecoveredAutoSaveRef,
    isMountedRef,
    handleSaveRef,
    dropSelection,
    switchToChat,
    onNaturalPolish,
  });

  // Handle keyboard shortcuts
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      void handleSave();
      return;
    }

    // Diff review mode shortcuts
    // In review mode, we should not start new natural polish requests.
    if (isReviewMode) {
      if (e.shiftKey && (e.key === "y" || e.key === "Y")) {
        e.preventDefault();
        onAcceptAllEdits?.();
        return;
      }
      if (e.shiftKey && (e.key === "n" || e.key === "N")) {
        e.preventDefault();
        onRejectAllEdits?.();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        onFinishReview?.();
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        onRejectAllEdits?.();
        setTimeout(() => onFinishReview?.(), 50);
        return;
      }
      return;
    }

    // Cmd/Ctrl + Shift + Q: Add selection to quote
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "q" || e.key === "Q")) {
      e.preventDefault();
      const textarea = textareaRef.current;
      if (textarea && fileId) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value.substring(start, end).trim();
        if (text) {
          const displayTitle = fileTitle || title;
          addQuote(text, fileId, displayTitle);
        }
      }
      return;
    }

    // Cmd/Ctrl + Shift + R: Natural polish selection or whole open chapter via Agent
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "r" || e.key === "R")) {
      e.preventDefault();
      void startNaturalPolish();
    }
  };

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    markTitleChanged(e.target.value);
  };

  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    markContentChanged(e.target.value);
    // During IME composition (e.g. Chinese Pinyin), avoid resizing on each intermediate update.
    // We'll resize on `compositionend` / effect instead.
    if (!isComposingRef.current) {
      adjustTextareaHeight();
    }
  };

  const handleContentBlur = () => {
    adjustTextareaHeight(true);
  };

  return (
    <div
      className="relative flex flex-col h-full bg-[hsl(var(--bg-canvas))]"
      onKeyDown={handleKeyDown}
    >
      {/* Diff Review Toolbar - shown when in review mode */}
      {isReviewMode && diffReviewState && onAcceptAllEdits && onRejectAllEdits && onFinishReview && (
        <DiffToolbar
          pendingEdits={diffReviewState.pendingEdits}
          onAcceptAll={onAcceptAllEdits}
          onRejectAll={onRejectAllEdits}
          onFinish={onFinishReview}
        />
      )}

      {/* Streaming indicator banner (kept mounted for symmetric exit) */}
      <EditorStreamingBanner
        open={isStreaming && !isReviewMode}
        isWaitingForFirstToken={!displayContent}
        title={title}
      />

      <EditorTitleBar
        fileId={fileId}
        title={title}
        disabled={readOnly || isStreaming || isReviewMode}
        isReviewMode={isReviewMode}
        autoFocusTitle={autoFocusTitle}
        onAutoFocusConsumed={onAutoFocusConsumed}
        onTitleChange={handleTitleChange}
        onFocusBody={focusEditorBody}
      />

      {/* Content area - switch between normal editor and diff review */}
      {isReviewMode && diffReviewState && onAcceptEdit && onRejectEdit && onResetEdit ? (
        <Suspense
          fallback={
            <div className="flex-1 flex items-center justify-center text-[hsl(var(--text-secondary))]">
              <Loader2 size={24} className="animate-spin" />
            </div>
          }
        >
          <DiffReviewSplitView
            originalContent={diffReviewState.originalContent}
            modifiedContent={diffReviewState.modifiedContent}
            pendingEdits={diffReviewState.pendingEdits}
            onAcceptEdit={onAcceptEdit}
            onRejectEdit={onRejectEdit}
            onResetEdit={onResetEdit}
          />
        </Suspense>
      ) : (
        <div
          ref={contentAreaRef}
          data-editor-scroll-container="true"
          className="flex-1 overflow-auto"
          onScroll={handleContentScroll}
          style={{ overflowAnchor: "none" }}
          {...bindPinchZoom()}
        >
          <div className="px-4 sm:px-6 py-4">
            {!content.trim() && !readOnly && !isStreaming && emptyHint && (
              <EditorEmptyHint renderHint={emptyHint} onFocusEditor={focusEditorBody} />
            )}
            <div className="relative">
              <textarea
                ref={textareaRef}
                value={displayContent}
                onChange={handleContentChange}
                onSelect={(event) => handleSelection(event.currentTarget)}
                onMouseUp={(event) => handleSelection(event.currentTarget)}
                onBlur={handleContentBlur}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={() => {
                  isComposingRef.current = false;
                  // Resize after IME commits the final text.
                  requestAnimationFrame(() => adjustTextareaHeight());
                }}
                disabled={readOnly || isStreaming}
                aria-label={t("editor:placeholder.contentPlaceholder")}
                placeholder={
                  isStreaming && !displayContent ? "" : t("editor:placeholder.contentPlaceholder")
                }
                className={`editor-content font-[family-name:var(--font-body)] w-full overflow-y-hidden bg-transparent text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] outline-none border-none resize-none leading-relaxed text-base ${isStreaming ? "cursor-default" : ""}`}
                style={{ minHeight: `${SIMPLE_EDITOR_MIN_HEIGHT_PX}px`, fontSize: `${zoom}em` }}
              />
              {/* Calm editorial waiting state before the first streamed character arrives */}
              {isStreaming && !displayContent && <EditorStreamingCanvasWaiting />}
              {/* Mirror div for calculating selection position */}
              <div ref={mirrorRef} aria-hidden="true" />
              {/* Typing cursor for streaming */}
              {isStreaming && displayContent && (
                <span
                  className="inline-block w-0.5 h-5 bg-[hsl(var(--accent-primary))] ml-0.5 animate-pulse absolute"
                  style={{ transform: "translateY(-1px)" }}
                />
              )}
            </div>
          </div>
        </div>
      )}

      {/* Status bar - hide during review mode */}
      {!isReviewMode && (
        <EditorStatusBar
          fileId={fileId}
          projectId={projectId}
          displayContent={displayContent}
          zoom={zoom}
          readOnly={readOnly}
          isStreaming={isStreaming}
          isSaving={isSaving}
          isDirty={isDirty}
          lastSaved={lastSaved}
          selectedText={selectedText}
          isLimitedAiQuota={isLimitedAiQuota}
          isFlushingNaturalPolish={isFlushingNaturalPolish}
          isNaturalPolishBusy={isNaturalPolishBusy}
          hasPolishableContent={hasPolishableContent}
          onResetZoom={resetZoom}
          onOpenVersionHistory={() => setShowVersionHistory(true)}
          onStartNaturalPolish={() => void startNaturalPolish()}
          onSave={() => void handleSave()}
        />
      )}

      {/* Version History Modal */}
      {isVersionHistoryMounted && fileId && (
        <Suspense fallback={null}>
          <FileVersionHistory
            open={isVersionHistoryOpen}
            fileId={fileId}
            fileTitle={title}
            hasUnsavedChanges={isDirty}
            onClose={() => setShowVersionHistory(false)}
            onBeforeRollback={prepareHistoryRollback}
            onRollback={handleRollback}
          />
        </Suspense>
      )}

      {/* Selection Toolbar (mounted during symmetric exit in browsers, unmounted immediately in tests) */}
      {isSelectionToolbarMounted && (
        <SelectionToolbar
          open={isSelectionToolbarOpen}
          text={selectedText}
          position={selectionPosition}
          onAdd={handleAddQuote}
          onClose={closeSelectionToolbar}
        />
      )}
    </div>
  );
};
