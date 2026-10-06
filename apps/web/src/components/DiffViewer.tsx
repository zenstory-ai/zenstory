/**
 * @fileoverview DiffViewer component - Visual comparison of file version differences.
 *
 * This component provides a rich diff visualization interface for comparing
 * file versions, supporting multiple view modes:
 * - Unified view: Single column with +/- indicators (code-style diff)
 * - Split view: Side-by-side old vs new version comparison
 * - Inline view: Prose-friendly inline highlighting for narrative content
 *
 * Features:
 * - Toggle line numbers display
 * - Show/hide unchanged lines for focused review
 * - Statistics display (lines added/removed, word changes)
 * - Responsive design with theme support
 *
 * @module components/DiffViewer
 */
import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Minus, Eye, Code, AlignLeft } from "lucide-react";
import type { DiffLine, VersionComparison } from "../types";

/**
 * Props for the DiffViewer component.
 */
interface DiffViewerProps {
  /** Version comparison data containing diff lines and statistics */
  comparison: VersionComparison;
  /** Whether to display line numbers (default: true) */
  showLineNumbers?: boolean;
}

/**
 * Available diff view modes.
 * - unified: Single column with +/- indicators
 * - split: Side-by-side old vs new comparison
 * - inline: Prose-friendly inline highlighting
 */
type ViewMode = "unified" | "split" | "inline";

/**
 * DiffViewer component for visualizing file version differences.
 *
 * Renders a diff viewer with toolbar controls for switching between view modes
 * (unified, split, inline) and filtering unchanged lines. Displays statistics
 * showing lines added, removed, and word count changes.
 *
 * @param props - Component props
 * @param props.comparison - Version comparison data with diff lines and stats
 * @param props.showLineNumbers - Whether to show line numbers (default: true)
 * @returns The rendered diff viewer component
 *
 * @example
 * ```tsx
 * <DiffViewer
 *   comparison={versionComparison}
 *   showLineNumbers={true}
 * />
 * ```
 */
export const DiffViewer: React.FC<DiffViewerProps> = ({
  comparison,
  showLineNumbers = true,
}) => {
  const { t } = useTranslation(["editor", "common"]);
  const [viewMode, setViewMode] = useState<ViewMode>("unified");
  const [showUnchanged, setShowUnchanged] = useState(true);

  /**
   * Renders the unified diff view with single-column display.
   *
   * Shows all diff lines in a single column with +/- indicators.
   * Filters out unchanged lines when showUnchanged is false.
   *
   * @returns JSX element containing the unified diff display
   */
  const renderUnifiedView = () => {
    const lines = showUnchanged
      ? comparison.html_diff
      : comparison.html_diff.filter((line) => line.type !== "equal");

    return (
      <div className="font-mono text-sm">
        {lines.map((line, index) => (
          <div
            key={index}
            className={`flex ${
              line.type === "added"
                ? "diff-add"
                : line.type === "removed"
                ? "diff-remove"
                : "bg-[hsl(var(--bg-primary))]"
            }`}
          >
            {showLineNumbers && (
              <>
                <span className="w-12 px-2 py-0.5 text-right text-[hsl(var(--text-secondary))] bg-[hsl(var(--bg-tertiary))] border-r border-[hsl(var(--border-color))] select-none">
                  {line.old_line || ""}
                </span>
                <span className="w-12 px-2 py-0.5 text-right text-[hsl(var(--text-secondary))] bg-[hsl(var(--bg-tertiary))] border-r border-[hsl(var(--border-color))] select-none">
                  {line.new_line || ""}
                </span>
              </>
            )}
            <span
              className={`w-6 px-2 py-0.5 text-center select-none ${
                line.type === "added"
                  ? "text-[hsl(var(--diff-add-text))] bg-[hsl(var(--diff-add-bg))]"
                  : line.type === "removed"
                  ? "text-[hsl(var(--diff-remove-text))] bg-[hsl(var(--diff-remove-bg))]"
                  : "text-[hsl(var(--text-secondary))]"
              }`}
            >
              {line.type === "added" ? "+" : line.type === "removed" ? "-" : " "}
            </span>
            <span
              className={`flex-1 px-2 py-0.5 whitespace-pre-wrap break-all ${
                line.type === "added"
                  ? "text-[hsl(var(--diff-add-text))]"
                  : line.type === "removed"
                  ? "text-[hsl(var(--diff-remove-text))]"
                  : "text-[hsl(var(--text-primary))]"
              }`}
            >
              {line.content || " "}
            </span>
          </div>
        ))}
      </div>
    );
  };

  /**
   * Renders the split diff view with side-by-side comparison.
   *
   * Groups consecutive changes together and balances line arrays for
   * proper alignment. Old version displayed on left, new version on right.
   *
   * @returns JSX element containing the split diff display
   */
  const renderSplitView = () => {
    // Group consecutive changes together
    const oldLines: (DiffLine | null)[] = [];
    const newLines: (DiffLine | null)[] = [];

    let i = 0;
    while (i < comparison.html_diff.length) {
      const line = comparison.html_diff[i];

      if (line.type === "equal") {
        oldLines.push(line);
        newLines.push(line);
        i++;
      } else if (line.type === "removed") {
        // Collect all consecutive removed lines
        const removedStart = i;
        while (
          i < comparison.html_diff.length &&
          comparison.html_diff[i].type === "removed"
        ) {
          oldLines.push(comparison.html_diff[i]);
          i++;
        }
        // Collect all consecutive added lines
        const addedStart = i;
        while (
          i < comparison.html_diff.length &&
          comparison.html_diff[i].type === "added"
        ) {
          newLines.push(comparison.html_diff[i]);
          i++;
        }
        // Balance the arrays
        const removedCount = i - addedStart > 0 ? i - addedStart : 0;
        const addedCount = addedStart - removedStart;
        for (let j = 0; j < Math.max(0, addedCount - removedCount); j++) {
          newLines.push(null);
        }
        for (let j = 0; j < Math.max(0, removedCount - addedCount); j++) {
          oldLines.push(null);
        }
      } else if (line.type === "added") {
        oldLines.push(null);
        newLines.push(line);
        i++;
      }
    }

    // Filter if not showing unchanged
    const maxLen = Math.max(oldLines.length, newLines.length);
    const rows: { old: DiffLine | null; new: DiffLine | null }[] = [];
    for (let j = 0; j < maxLen; j++) {
      const oldLine = oldLines[j] || null;
      const newLine = newLines[j] || null;
      if (
        showUnchanged ||
        oldLine?.type !== "equal" ||
        newLine?.type !== "equal"
      ) {
        rows.push({ old: oldLine, new: newLine });
      }
    }

    return (
      <div className="font-mono text-sm flex">
        {/* Old version */}
        <div className="w-1/2 border-r border-[hsl(var(--border-color))]">
          <div className="px-2 py-1 bg-[hsl(var(--diff-remove-bg))] text-[hsl(var(--diff-remove-text))] text-xs font-medium border-b border-[hsl(var(--border-color))]">
            {t('editor:versionHistory.versionPrefix')} {comparison.version1.number}
          </div>
          {rows.map((row, index) => (
            <div
              key={index}
              className={`flex ${
                row.old?.type === "removed"
                  ? "diff-remove"
                  : row.old === null
                  ? "bg-[hsl(var(--bg-tertiary))]"
                  : "bg-[hsl(var(--bg-primary))]"
              }`}
            >
              {showLineNumbers && (
                <span className="w-10 px-2 py-0.5 text-right text-[hsl(var(--text-secondary))] bg-[hsl(var(--bg-tertiary))] border-r border-[hsl(var(--border-color))] select-none">
                  {row.old?.old_line || ""}
                </span>
              )}
              <span
                className={`flex-1 px-2 py-0.5 whitespace-pre-wrap break-all ${
                  row.old?.type === "removed"
                    ? "text-[hsl(var(--diff-remove-text))]"
                    : row.old === null
                    ? "text-[hsl(var(--text-secondary))]"
                    : "text-[hsl(var(--text-primary))]"
                }`}
              >
                {row.old?.content || " "}
              </span>
            </div>
          ))}
        </div>

        {/* New version */}
        <div className="w-1/2">
          <div className="px-2 py-1 bg-[hsl(var(--diff-add-bg))] text-[hsl(var(--diff-add-text))] text-xs font-medium border-b border-[hsl(var(--border-color))]">
            {t('editor:versionHistory.versionPrefix')} {comparison.version2.number}
          </div>
          {rows.map((row, index) => (
            <div
              key={index}
              className={`flex ${
                row.new?.type === "added"
                  ? "diff-add"
                  : row.new === null
                  ? "bg-[hsl(var(--bg-tertiary))]"
                  : "bg-[hsl(var(--bg-primary))]"
              }`}
            >
              {showLineNumbers && (
                <span className="w-10 px-2 py-0.5 text-right text-[hsl(var(--text-secondary))] bg-[hsl(var(--bg-tertiary))] border-r border-[hsl(var(--border-color))] select-none">
                  {row.new?.new_line || ""}
                </span>
              )}
              <span
                className={`flex-1 px-2 py-0.5 whitespace-pre-wrap break-all ${
                  row.new?.type === "added"
                    ? "text-[hsl(var(--diff-add-text))]"
                    : row.new === null
                    ? "text-[hsl(var(--text-secondary))]"
                    : "text-[hsl(var(--text-primary))]"
                }`}
              >
                {row.new?.content || " "}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  /**
   * Renders the inline diff view optimized for prose/narrative content.
   *
   * Displays changes inline with highlighted backgrounds, suitable for
   * reviewing novel or story content rather than code-style diffs.
   * Includes a legend showing removed (red) and added (green) indicators.
   *
   * @returns JSX element containing the inline diff display
   */
  const renderInlineView = () => {
    const lines = showUnchanged
      ? comparison.html_diff
      : comparison.html_diff.filter((line) => line.type !== "equal");

    // For prose/novel content, show inline word-level diff
    return (
      <div className="p-4 max-w-none">
        <div className="text-[hsl(var(--text-secondary))] text-xs mb-2 flex gap-4">
          <span className="inline-flex items-center gap-1">
            <span className="w-4 h-4 rounded diff-remove"></span>
{t('editor:diff.legendRemoved')}
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="w-4 h-4 rounded diff-add"></span>
{t('editor:diff.legendAdded')}
          </span>
        </div>
        <div className="leading-relaxed whitespace-pre-wrap text-[hsl(var(--text-primary))]">
          {lines.map((line, index) => {
            if (line.type === "equal") {
              return (
                <span key={index}>
                  {line.content}
                  {"\n"}
                </span>
              );
            }
            if (line.type === "removed") {
              return (
                <span
                  key={index}
                  className="diff-remove px-0.5 rounded"
                >
                  {line.content}
                  {"\n"}
                </span>
              );
            }
            if (line.type === "added") {
              return (
                <span key={index} className="diff-add px-0.5 rounded">
                  {line.content}
                  {"\n"}
                </span>
              );
            }
            return null;
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="px-3 py-2 border-b border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] flex items-center justify-between">
        <div className="flex items-center gap-3 text-sm">
          <span className="flex items-center gap-1 text-[hsl(var(--success))]">
            <Plus size={14} />
            {comparison.stats.lines_added} {t('editor:diff.linesAdded')}
          </span>
          <span className="flex items-center gap-1 text-[hsl(var(--error))]">
            <Minus size={14} />
            {comparison.stats.lines_removed} {t('editor:diff.linesRemoved')}
          </span>
          <span className="text-[hsl(var(--text-secondary))]">
            {t('editor:diff.wordChange')}: {comparison.stats.word_diff > 0 ? "+" : ""}
            {comparison.stats.word_diff}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-sm text-[hsl(var(--text-secondary))] cursor-pointer">
            <input
              type="checkbox"
              checked={showUnchanged}
              onChange={(e) => setShowUnchanged(e.target.checked)}
              className="rounded border-[hsl(var(--border-color))]"
            />
            {t('editor:diff.showUnchanged')}
          </label>

          <div className="flex items-center border border-[hsl(var(--border-color))] rounded overflow-hidden">
            <button
              onClick={() => setViewMode("unified")}
              className={`p-1.5 transition-colors ${
                viewMode === "unified"
                  ? "bg-[hsl(var(--accent-primary)/0.2)] text-[hsl(var(--accent-primary))]"
                  : "hover:bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))]"
              }`}
              title={t('editor:diff.unifiedView')}
            >
              <Code size={14} />
            </button>
            <button
              onClick={() => setViewMode("split")}
              className={`p-1.5 transition-colors ${
                viewMode === "split"
                  ? "bg-[hsl(var(--accent-primary)/0.2)] text-[hsl(var(--accent-primary))]"
                  : "hover:bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))]"
              }`}
              title={t('editor:diff.splitView')}
            >
              <Eye size={14} />
            </button>
            <button
              onClick={() => setViewMode("inline")}
              className={`p-1.5 transition-colors ${
                viewMode === "inline"
                  ? "bg-[hsl(var(--accent-primary)/0.2)] text-[hsl(var(--accent-primary))]"
                  : "hover:bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))]"
              }`}
              title={t('editor:diff.inlineView')}
            >
              <AlignLeft size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Diff Content */}
      <div className="flex-1 overflow-auto bg-[hsl(var(--bg-primary))]">
        {viewMode === "unified" && renderUnifiedView()}
        {viewMode === "split" && renderSplitView()}
        {viewMode === "inline" && renderInlineView()}
      </div>
    </div>
  );
};

export default DiffViewer;
