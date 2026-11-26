export type SuggestionDisplayState = "loading" | "ready" | "fallback";

export function getRandomSuggestions(pool: string[], count: number): string[] {
  if (!pool.length) return [];
  const shuffled = [...pool].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, Math.min(count, shuffled.length));
}

export function getSuggestionsToDisplay(
  staticSuggestions: string[],
  aiSuggestions: string[],
  suggestionDisplayState: SuggestionDisplayState
): string[] {
  if (suggestionDisplayState === "ready" && aiSuggestions.length > 0) {
    return aiSuggestions.slice(0, 3);
  }
  if (suggestionDisplayState === "fallback") {
    return staticSuggestions.slice(0, 3);
  }
  return [];
}
