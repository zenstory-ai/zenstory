import React from "react";
import { Check, Clock, History, Loader2, Save, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { countWords } from "../../lib/documentChunker";
import { SavedAgoLabel } from "../SavedAgoLabel";

const isAppleSaveShortcutPlatform = (): boolean => {
  if (typeof navigator === "undefined") return false;
  const uaDataPlatform = (navigator as Navigator & { userAgentData?: { platform?: string } })
    .userAgentData?.platform;
  return /mac|iphone|ipad|ipod/i.test(uaDataPlatform || navigator.platform || "");
};

interface EditorStatusBarProps {
  fileId?: string;
  projectId?: string;
  displayContent: string;
  zoom: number;
  readOnly: boolean;
  isStreaming: boolean;
  isSaving: boolean;
  isDirty: boolean;
  lastSaved: Date | null;
  selectedText: string;
  isLimitedAiQuota: boolean;
  isFlushingNaturalPolish: boolean;
  isNaturalPolishBusy: boolean;
  hasPolishableContent: boolean;
  onResetZoom: () => void;
  onOpenVersionHistory: () => void;
  onStartNaturalPolish: () => void;
  onSave: () => void;
}

export const EditorStatusBar: React.FC<EditorStatusBarProps> = ({
  fileId,
  projectId,
  displayContent,
  zoom,
  readOnly,
  isStreaming,
  isSaving,
  isDirty,
  lastSaved,
  selectedText,
  isLimitedAiQuota,
  isFlushingNaturalPolish,
  isNaturalPolishBusy,
  hasPolishableContent,
  onResetZoom,
  onOpenVersionHistory,
  onStartNaturalPolish,
  onSave,
}) => {
  const { t } = useTranslation(["editor", "versions"]);
  const paragraphCount = displayContent.split("\n").filter((line) => line.trim()).length;

  return (
    <div className="shrink-0 px-3 sm:px-4 py-2 flex flex-wrap items-center justify-between gap-2 border-t border-[hsl(var(--separator-color)/0.7)] bg-[hsl(var(--bg-canvas))]">
      <div className="flex items-center gap-2.5 text-xs text-[hsl(var(--text-secondary))] whitespace-nowrap">
        <span
          data-testid="editor-word-count"
          title={t("editor:wordCountHint")}
          className="tabular-nums text-[hsl(var(--text-secondary))]"
        >
          {t("editor:wordCount")}{" "}
          <strong className="tabular-nums text-[hsl(var(--text-primary))]">
            {countWords(displayContent)}
          </strong>
        </span>
        {/* One paragraph per non-empty line: manuscripts indent each paragraph instead of leaving blank lines. */}
        <span data-testid="editor-paragraph-count" className="tabular-nums">
          {t("editor:paragraphCount")}{" "}
          <strong className="tabular-nums text-[hsl(var(--text-primary))]">
            {paragraphCount}
          </strong>
        </span>
        {/* Zoom indicator - only show when zoomed */}
        {zoom !== 1 && (
          <button
            type="button"
            onClick={onResetZoom}
            className="max-md:min-h-11 px-1.5 py-0.5 rounded-lg tabular-nums text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
            title={t("editor:resetZoom")}
          >
            {Math.round(zoom * 100)}%
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-1.5">
        {/* Version history button */}
        {fileId && (
          <button
            type="button"
            onClick={onOpenVersionHistory}
            className="max-md:min-h-11 px-1.5 py-1.5 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded-lg flex items-center gap-1 whitespace-nowrap transition-colors"
            title={t("versions:title")}
          >
            <History size={14} />
            {t("editor:history")}
          </button>
        )}

        {/* Natural polish (selection when highlighted, otherwise full open chapter via Agent) */}
        {fileId && (
          <button
            type="button"
            onClick={onStartNaturalPolish}
            disabled={!projectId || readOnly || isNaturalPolishBusy || !hasPolishableContent}
            className="max-md:min-h-11 px-1.5 py-1.5 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded-lg flex items-center gap-1 whitespace-nowrap transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
            title={
              !projectId
                ? t("editor:naturalPolishMissingContext")
                : !hasPolishableContent
                  ? t("editor:naturalPolishEmptyContent")
                  : selectedText
                    ? isLimitedAiQuota
                      ? t("editor:naturalPolishTooltipSelectionFree")
                      : t("editor:naturalPolishTooltipSelection")
                    : isLimitedAiQuota
                      ? t("editor:naturalPolishTooltipFree")
                      : t("editor:naturalPolishTooltip")
            }
          >
            {isFlushingNaturalPolish ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Sparkles size={14} />
            )}
            {isFlushingNaturalPolish
              ? t("editor:naturalPolishWorking")
              : selectedText
                ? t("editor:naturalPolishSelection")
                : t("editor:naturalPolish")}
          </button>
        )}

        {/* Save status */}
        {isSaving ? (
          <span className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1 whitespace-nowrap tabular-nums">
            <Clock size={12} className="animate-spin" />
            {t("editor:saving")}
          </span>
        ) : isDirty ? (
          <span className="text-xs text-[hsl(var(--warning-text))] whitespace-nowrap motion-banner-enter">
            {t("editor:unsaved")}
          </span>
        ) : lastSaved ? (
          <span className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1 whitespace-nowrap tabular-nums">
            <Check size={12} className="text-[hsl(var(--success-text))] motion-icon-pop" />
            <SavedAgoLabel savedAt={lastSaved} />
          </span>
        ) : null}

        {/* Save button */}
        <button
          type="button"
          onClick={onSave}
          disabled={isSaving || !isDirty || readOnly || isStreaming}
          title={
            isStreaming
              ? t("editor:saveUnavailableStreaming")
              : isSaving
                ? t("editor:saving")
                : isDirty
                  ? t("editor:saveWithShortcut", {
                      shortcut: isAppleSaveShortcutPlatform() ? "⌘S" : "Ctrl+S",
                    })
                  : t("editor:alreadySaved")
          }
          className="max-md:min-h-11 px-2.5 py-1.5 text-xs bg-[hsl(var(--primary))] text-[hsl(var(--primary-foreground))] rounded-lg hover:bg-[hsl(var(--primary-hover))] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 flex items-center gap-1.5 whitespace-nowrap transition-[transform,background-color,opacity] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.975] motion-reduce:active:scale-100"
        >
          <Save size={14} />
          {t("editor:save")}
        </button>
      </div>
    </div>
  );
};
