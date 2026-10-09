/**
 * @fileoverview Thinking Content Component
 * @module components/ThinkingContent
 *
 * Displays AI reasoning process (thinking) in a collapsible panel.
 * Shows the model's intermediate reasoning steps before producing
 * the final response, helping users understand the AI's thought process.
 *
 * Features:
 * - Collapsible with expand/collapse toggle; collapsed by default
 * - Animated dots when streaming (indicates active thinking), also while collapsed
 * - Markdown rendering with GFM support (tables, lists, code blocks)
 * - Returns null when content is empty
 * - Only the author's own toggle is remembered in localStorage
 * - Internal tool/agent names, ids, control markers and raw file tags are
 *   cleaned for display (see lib/thinkingDisplay); stored reasoning is untouched
 * - Global visibility control via useThinkingVisibility hook
 * - i18n support (English/Chinese labels)
 *
 * @example
 * // Basic usage with static content
 * <ThinkingContent content="Analyzing the request..." />
 *
 * @example
 * // Streaming mode with animated dots
 * <ThinkingContent
 *   content="Let me think about this..."
 *   isStreaming={true}
 * />
 *
 * @see useThinkingVisibility - Hook for global thinking visibility control
 */

import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp } from "lucide-react";
import { LazyMarkdown } from "./LazyMarkdown";
import { useThinkingVisibility } from "../hooks/useThinkingVisibility";
import { sanitizeThinkingForDisplay } from "../lib/thinkingDisplay";

/**
 * Props for the ThinkingContent component.
 *
 * @interface ThinkingContentProps
 */
interface ThinkingContentProps {
  /**
   * The thinking/reasoning content to display.
   * Rendered as Markdown with GFM support.
   * If empty or whitespace-only, the component returns null.
   */
  content: string;

  /**
   * Whether the AI is currently streaming thinking content.
   * When true, shows animated dots next to the "Thinking" label.
   * @default false
   */
  isStreaming?: boolean;
}

/**
 * localStorage key for the author's explicit expand/collapse choice.
 *
 * `_v2` deliberately ignores the old `zenstory_thinking_expanded` key: the old
 * component wrote "true" on every mount, so a stored "true" there does not mean
 * the author ever chose to expand the panel.
 * @constant {string}
 */
const STORAGE_KEY = "zenstory_thinking_expanded_v2";

const readStoredExpanded = (): boolean => {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
};

/**
 * Renders a collapsible panel displaying AI thinking/reasoning content.
 *
 * - **Visibility**: Controlled globally via useThinkingVisibility hook
 *   (returns null if thinking is disabled in settings)
 * - **Empty state**: Returns null if the cleaned content is empty
 * - **Expand/collapse**: Collapsed by default; only a click persists the choice
 * - **Streaming indicator**: Shows animated dots when isStreaming is true
 * - **Markdown rendering**: Uses ReactMarkdown with GFM support
 *
 * @param {ThinkingContentProps} props - Component props
 * @param {string} props.content - The thinking content to display (Markdown)
 * @param {boolean} [props.isStreaming=false] - Whether AI is actively thinking
 * @returns {React.ReactElement | null} The thinking panel, or null if hidden/empty
 */
export function ThinkingContent({ content, isStreaming = false }: ThinkingContentProps) {
  const { t } = useTranslation('chat');
  // Global visibility control reserved for future UI toggle
  // Currently controlled by localStorage via useThinkingVisibility hook
  const { showThinking } = useThinkingVisibility();

  // Collapsed unless the author previously chose to expand it.
  // Note: Hooks must be called unconditionally before any early returns
  const [isExpanded, setIsExpanded] = useState(readStoredExpanded);

  // Persist only explicit toggles; mounting never writes, so the default stays collapsed.
  const handleToggle = useCallback(() => {
    setIsExpanded((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // Ignore localStorage errors
      }
      return next;
    });
  }, []);

  const displayContent = useMemo(
    () => sanitizeThinkingForDisplay(content ?? "", (key) => t(key)),
    [content, t],
  );

  // Don't render if global setting is disabled
  if (!showThinking) {
    return null;
  }

  // Return null if nothing is left to show
  if (!displayContent || displayContent.trim().length === 0) {
    return null;
  }

  return (
    <div className="max-w-full rounded-lg mb-2 opacity-60 hover:opacity-80 transition-opacity duration-700">
      {/* Header */}
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={isExpanded}
        className="w-full px-2 py-1.5 flex items-center justify-between rounded hover:bg-[hsl(var(--bg-tertiary)/0.5)] transition-colors"
        aria-label={isExpanded
          ? t('chat:thinking.collapse', '收起思考过程')
          : t('chat:thinking.expand', '展开思考过程')}
      >
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {t('chat:thinking.title')}
          </span>
          {isStreaming && (
            <div className="flex items-center gap-0.5">
              <span className="w-1 h-1 bg-[hsl(var(--text-secondary))] rounded-full animate-pulse"></span>
              <span className="w-1 h-1 bg-[hsl(var(--text-secondary))] rounded-full animate-pulse" style={{ animationDelay: "0.2s" }}></span>
              <span className="w-1 h-1 bg-[hsl(var(--text-secondary))] rounded-full animate-pulse" style={{ animationDelay: "0.4s" }}></span>
            </div>
          )}
        </div>
        <div className="flex items-center">
          {isExpanded ? (
            <ChevronUp className="w-3 h-3 text-[hsl(var(--text-secondary))]" />
          ) : (
            <ChevronDown className="w-3 h-3 text-[hsl(var(--text-secondary))]" />
          )}
        </div>
      </button>

      {/* Content */}
      {isExpanded && (
        <div className="px-2 py-1.5">
          <div className="prose prose-xs max-w-none text-[hsl(var(--text-secondary))] opacity-80">
            <LazyMarkdown>{displayContent}</LazyMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

export default ThinkingContent;
