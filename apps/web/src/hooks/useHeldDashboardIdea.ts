import { useEffect } from "react";

const HELD_IDEA_KEY_PREFIX = "zenstory_held_dashboard_idea:";

function heldIdeaKey(userId: string | null | undefined): string {
  return `${HELD_IDEA_KEY_PREFIX}${userId || "anonymous"}`;
}

/** The idea an author typed on the home page while today's AI messages were used up. */
export function readHeldDashboardIdea(userId: string | null | undefined): string {
  try {
    return localStorage.getItem(heldIdeaKey(userId)) ?? "";
  } catch {
    return "";
  }
}

/**
 * Keeps the home-page idea across reloads while it cannot be sent yet.
 *
 * While today's AI messages are used up (`holding`), whatever is in the idea box is
 * saved. A saved idea keeps following the author's edits after the reset too, so a
 * reload never brings back an older version; once the box is emptied (sent after the
 * reset, or cleared by the author) the saved copy goes away. Restoring is done by
 * seeding the box's initial state with `readHeldDashboardIdea`.
 */
export function useHeldDashboardIdea(
  userId: string | null | undefined,
  idea: string,
  holding: boolean,
): void {
  useEffect(() => {
    const key = heldIdeaKey(userId);
    try {
      if (!idea.trim()) {
        localStorage.removeItem(key);
      } else if (holding || localStorage.getItem(key) !== null) {
        localStorage.setItem(key, idea);
      }
    } catch {
      // Storage unavailable (private mode): the idea still stays in the box for this visit.
    }
  }, [userId, idea, holding]);
}
