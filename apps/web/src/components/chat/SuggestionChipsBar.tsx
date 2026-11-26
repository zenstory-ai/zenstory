import React from "react";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SwipeableSuggestionChip } from "./SwipeableSuggestionChip";

interface SuggestionChipsBarProps {
  isLoading: boolean;
  suggestions: string[];
  isMobile: boolean;
  onSelectSuggestion: (suggestion: string) => void;
  onDismissSuggestion: (suggestion: string) => void;
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

export const SuggestionChipsBar: React.FC<SuggestionChipsBarProps> = ({
  isLoading,
  suggestions,
  isMobile,
  onSelectSuggestion,
  onDismissSuggestion,
  onRefresh,
  isRefreshing = false,
}) => {
  const { t } = useTranslation(["chat", "common"]);

  return (
    <div className="shrink-0 flex min-w-0 items-center gap-2">
      {/* Only the chips scroll sideways: long ones (a reply's own options) must not push the
          refresh button off a phone screen. */}
      <div
        className={`flex min-w-0 flex-1 items-center overflow-x-auto pb-0.5 pr-5 scrollbar-none [mask-image:linear-gradient(to_right,black_calc(100%-24px),transparent)] ${isMobile ? "gap-2" : "gap-1.5"}`}
      >
        {isLoading ? (
          <>
            <span className="sr-only">{t("common:loading")}</span>
            {[0, 1, 2].map((i) => (
              <span
                key={`suggestion-loading-${i}`}
                className={`shrink-0 rounded-full bg-[hsl(var(--bg-tertiary))] animate-pulse ${isMobile ? "w-20 h-7" : "w-24 h-6"}`}
              />
            ))}
          </>
        ) : (
          suggestions.map((suggestion, index) => (
            <SwipeableSuggestionChip
              key={`${suggestion}-${index}`}
              suggestion={suggestion}
              onClick={() => onSelectSuggestion(suggestion)}
              onDismiss={() => onDismissSuggestion(suggestion)}
              isMobile={isMobile}
            />
          ))
        )}
      </div>
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          className="shrink-0 inline-flex items-center justify-center min-h-11 min-w-11 md:min-h-7 md:min-w-7 -my-1.5 md:my-0 p-1.5 text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded-full transition-colors disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
          title={t("chat:input.refresh")}
          aria-label={t("chat:input.refresh")}
          aria-busy={isRefreshing}
        >
          <RefreshCw size={13} className={isRefreshing ? "animate-spin" : ""} />
        </button>
      )}
    </div>
  );
};
