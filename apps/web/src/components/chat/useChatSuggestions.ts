import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { trackEvent } from "../../lib/analytics";
import {
  getRandomSuggestions,
  getSuggestionsToDisplay,
  type SuggestionDisplayState,
} from "./suggestionHelpers";

interface UseChatSuggestionsOptions {
  aiSuggestions: string[];
  hideSuggestion?: (suggestion: string) => boolean;
  hideSuggestions: boolean;
  suggestionDisplayState: SuggestionDisplayState;
  onRefreshSuggestions?: () => void;
  input: string;
  inputDisabled: boolean;
  effectiveSendDisabled: boolean;
  layout: "auto" | "fill";
  selectedSkillsCount: number;
}

export function useChatSuggestions({
  aiSuggestions,
  hideSuggestion,
  hideSuggestions,
  suggestionDisplayState,
  onRefreshSuggestions,
  input,
  inputDisabled,
  effectiveSendDisabled,
  layout,
  selectedSkillsCount,
}: UseChatSuggestionsOptions) {
  const { t } = useTranslation(["chat"]);

  // The suggestion the author last put into the box; sent unchanged -> entry "suggestion".
  const pickedSuggestionRef = useRef<string | null>(null);
  const noteSuggestionPicked = useCallback(
    (suggestion: string, via: "click" | "tab") => {
      pickedSuggestionRef.current = suggestion;
      trackEvent("ai_suggestion_picked", {
        via,
        index: aiSuggestions.indexOf(suggestion),
        source: suggestionDisplayState,
      });
    },
    [aiSuggestions, suggestionDisplayState]
  );

  const consumePickedSuggestionMatch = useCallback((message: string): boolean => {
    const matched = pickedSuggestionRef.current?.trim() === message;
    pickedSuggestionRef.current = null;
    return matched;
  }, []);

  const allStaticSuggestions = useMemo(() => {
    const raw = t("chat:input.staticSuggestions", { returnObjects: true }) as unknown;
    if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
    return [];
  }, [t]);

  const [staticSuggestions, setStaticSuggestions] = useState<string[]>(() =>
    getRandomSuggestions(allStaticSuggestions, 3)
  );
  const [dismissedSuggestions, setDismissedSuggestions] = useState<Set<string>>(new Set());

  // Reset static suggestions when language changes
  useEffect(() => {
    setStaticSuggestions(getRandomSuggestions(allStaticSuggestions, 3));
  }, [allStaticSuggestions]);

  const baseDisplaySuggestions = getSuggestionsToDisplay(
    hideSuggestion ? staticSuggestions.filter((s) => !hideSuggestion(s)) : staticSuggestions,
    hideSuggestion ? aiSuggestions.filter((s) => !hideSuggestion(s)) : aiSuggestions,
    suggestionDisplayState
  );

  const displaySuggestions = useMemo(
    () => baseDisplaySuggestions.filter((s) => !dismissedSuggestions.has(s)),
    [baseDisplaySuggestions, dismissedSuggestions]
  );

  const isSuggestionLoading = suggestionDisplayState === "loading";
  const showSuggestionRow =
    !hideSuggestions &&
    !input &&
    !inputDisabled &&
    !effectiveSendDisabled &&
    !(layout === "fill" && selectedSkillsCount > 0) &&
    (isSuggestionLoading || suggestionDisplayState === "fallback" || displaySuggestions.length > 0);

  const dismissSuggestion = useCallback((suggestion: string) => {
    setDismissedSuggestions((prev) => {
      const next = new Set(prev);
      next.add(suggestion);
      return next;
    });
  }, []);

  const refreshSuggestions = useCallback(() => {
    setDismissedSuggestions(new Set());
    onRefreshSuggestions?.();
  }, [onRefreshSuggestions]);

  return {
    displaySuggestions,
    isSuggestionLoading,
    showSuggestionRow,
    noteSuggestionPicked,
    consumePickedSuggestionMatch,
    dismissSuggestion,
    refreshSuggestions,
  };
}
