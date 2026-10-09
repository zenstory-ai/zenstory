import { useCallback, useState } from "react";
import { isNextStepDismissed, rememberNextStepDismissed } from "../lib/nextStep";

/**
 * 「先不用」 on the chat next-step card, per project. Survives refresh and
 * re-entry through localStorage; the in-memory set re-renders right away and
 * covers browsers where storage is unavailable.
 */
export function useNextStepDismissal() {
  const [dismissedThisSession, setDismissedThisSession] = useState<ReadonlySet<string>>(() => new Set());

  const isDismissed = useCallback(
    (projectId: string) => dismissedThisSession.has(projectId) || isNextStepDismissed(projectId),
    [dismissedThisSession],
  );

  const dismiss = useCallback((projectId: string) => {
    rememberNextStepDismissed(projectId);
    setDismissedThisSession((prev) => new Set(prev).add(projectId));
  }, []);

  return { isDismissed, dismiss };
}
