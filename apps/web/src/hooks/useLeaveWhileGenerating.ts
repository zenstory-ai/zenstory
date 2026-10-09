import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { UNSAFE_LocationContext, UNSAFE_NavigationContext } from "react-router-dom";

type Navigate = (to: unknown, ...rest: unknown[]) => void;

function targetPathname(to: unknown, currentPathname: string): string {
  if (typeof to === "string") return new URL(to, `http://app${currentPathname}`).pathname;
  if (to && typeof to === "object" && typeof (to as { pathname?: unknown }).pathname === "string") {
    return (to as { pathname: string }).pathname;
  }
  return currentPathname;
}

/**
 * While a round is generating, leaving the workbench unmounts the chat and drops
 * the stream, which ends the round. Ask first: in-app navigation to another page
 * opens an in-app confirmation, closing or reloading the tab gets the browser's
 * own prompt. Navigation within the same page (query/hash only) is never blocked.
 *
 * The app uses <BrowserRouter> (no data-router blockers), so in-app navigation is
 * intercepted at the router navigator's push/replace while `active`.
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

  useEffect(() => {
    if (!active) return;

    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);

    const originalPush = navigator?.push;
    const originalReplace = navigator?.replace;
    const guard = (original: Navigate): Navigate => (to, ...rest) => {
      if (targetPathname(to, pathnameRef.current) === pathnameRef.current) {
        original(to, ...rest);
        return;
      }
      setPendingLeave(() => () => original(to, ...rest));
    };
    if (navigator && originalPush && originalReplace) {
      // BrowserRouter has no blocker API; swap push/replace while active and restore on cleanup.
      // eslint-disable-next-line react-hooks/immutability
      navigator.push = guard(originalPush);
      navigator.replace = guard(originalReplace);
    }

    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      if (navigator && originalPush && originalReplace) {
        navigator.push = originalPush;
        navigator.replace = originalReplace;
      }
    };
  }, [active, navigator]);

  // The round ended while the dialog was open: nothing left to protect.
  useEffect(() => {
    if (!active) setPendingLeave(null);
  }, [active]);

  const confirmLeave = useCallback(() => {
    const leave = pendingLeave;
    setPendingLeave(null);
    leave?.();
  }, [pendingLeave]);

  const cancelLeave = useCallback(() => setPendingLeave(null), []);

  return { leavePending: pendingLeave !== null, confirmLeave, cancelLeave };
}
