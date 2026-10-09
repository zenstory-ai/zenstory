import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { UNSAFE_LocationContext, UNSAFE_NavigationContext } from "react-router-dom";

type Navigate = (to: unknown, ...rest: unknown[]) => void;

/** Marks the extra history entry pushed while a round is generating (see below). */
export const LEAVE_GUARD_HISTORY_KEY = "zsLeaveGuard";

function targetPathname(to: unknown, currentPathname: string): string {
  if (typeof to === "string") return new URL(to, `http://app${currentPathname}`).pathname;
  if (to && typeof to === "object" && typeof (to as { pathname?: unknown }).pathname === "string") {
    return (to as { pathname: string }).pathname;
  }
  return currentPathname;
}

function sentinelOnTop(): boolean {
  const state = window.history.state as Record<string, unknown> | null;
  return Boolean(state && state[LEAVE_GUARD_HISTORY_KEY] === true);
}

function pushSentinel() {
  if (sentinelOnTop()) return;
  // Same URL, router state copied (key / idx) so the router sees no navigation.
  const state = (window.history.state ?? {}) as Record<string, unknown>;
  window.history.pushState({ ...state, [LEAVE_GUARD_HISTORY_KEY]: true }, "", window.location.href);
}

/**
 * While a round is generating, leaving the workbench unmounts the chat and drops
 * the stream, which ends the round. Ask first:
 * - in-app navigation to another page opens an in-app confirmation;
 * - the browser Back button / mobile system back opens the same confirmation;
 * - closing or reloading the tab gets the browser's own prompt.
 * Navigation within the same page (query/hash only) is never blocked.
 *
 * The app uses <BrowserRouter> (no data-router blockers), so in-app navigation is
 * intercepted at the router navigator's push/replace while `active`. Back is caught
 * with a sentinel history entry (same URL) pushed while `active`: pressing Back pops
 * the sentinel (the page stays), we ask, and only "离开" goes back for real.
 *
 * If the round ends while the dialog is open, the pending navigation is kept so the
 * author's choice still counts (`roundEnded` lets the dialog say nothing will be cut off).
 */
export function useLeaveWhileGenerating(active: boolean) {
  // Contexts (not hooks like useLocation) so the chat still renders outside a router.
  const navigator = useContext(UNSAFE_NavigationContext)?.navigator as
    | { push?: Navigate; replace?: Navigate }
    | undefined;
  const pathname = useContext(UNSAFE_LocationContext)?.location.pathname ?? window.location.pathname;
  const pathnameRef = useRef(pathname);
  useEffect(() => {
    pathnameRef.current = pathname;
  }, [pathname]);
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!active) return;

    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);

    // Back / system back: the sentinel was popped, we are still on this page.
    pushSentinel();
    const onPopState = (event: PopStateEvent) => {
      const state = event.state as Record<string, unknown> | null;
      if (state && state[LEAVE_GUARD_HISTORY_KEY] === true) return; // forward onto the sentinel
      setPendingLeave(() => () => window.history.back());
    };
    window.addEventListener("popstate", onPopState);

    const originalPush = navigator?.push;
    const originalReplace = navigator?.replace;
    const guard = (original: Navigate, viaReplace?: Navigate): Navigate => (to, ...rest) => {
      if (targetPathname(to, pathnameRef.current) === pathnameRef.current) {
        original(to, ...rest);
        return;
      }
      setPendingLeave(() => () => {
        // Leaving for real: take the sentinel's place instead of stacking a page after it.
        if (viaReplace && sentinelOnTop()) viaReplace(to, ...rest);
        else original(to, ...rest);
      });
    };
    if (navigator && originalPush && originalReplace) {
      // BrowserRouter has no blocker API; swap push/replace while active and restore on cleanup.
      // eslint-disable-next-line react-hooks/immutability
      navigator.push = guard(originalPush, originalReplace);
      navigator.replace = guard(originalReplace);
    }

    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      window.removeEventListener("popstate", onPopState);
      if (navigator && originalPush && originalReplace) {
        navigator.push = originalPush;
        navigator.replace = originalReplace;
      }
      // Round over (or the chat went away) and the sentinel is still on top: drop it so
      // Back works normally again. Deferred so a cleanup immediately followed by a re-run
      // (dev StrictMode, navigator change) keeps the sentinel instead of popping it.
      setTimeout(() => {
        if ((activeRef.current && mountedRef.current) || !sentinelOnTop()) return;
        window.history.back();
      }, 0);
    };
  }, [active, navigator]);

  const confirmLeave = useCallback(() => {
    const leave = pendingLeave;
    setPendingLeave(null);
    leave?.();
  }, [pendingLeave]);

  const cancelLeave = useCallback(() => {
    setPendingLeave(null);
    // Stayed after pressing Back: guard the next Back press too.
    if (activeRef.current) pushSentinel();
  }, []);

  return {
    leavePending: pendingLeave !== null,
    /** The round finished while the dialog was open: leaving no longer cuts anything off. */
    roundEnded: pendingLeave !== null && !active,
    confirmLeave,
    cancelLeave,
  };
}
