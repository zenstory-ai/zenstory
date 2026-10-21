import { trackEvent } from "./analytics";
import { logger } from "./logger";

/**
 * Page translation (Chrome, Edge, in-page translate extensions) replaces the
 * text nodes React rendered with `<font>` wrappers and may move inline
 * elements. React later removes or inserts relative to a node that is no
 * longer where it left it, the DOM throws NotFoundError, and an error boundary
 * replaces the whole editor or chat panel. This guard skips such a removal and
 * inserts at the end instead, so the panel keeps working; it reports each kind
 * of conflict once per page so the cause stays visible.
 * See https://github.com/facebook/react/issues/11538.
 */

function isPageTranslated(): boolean {
  const html = document.documentElement;
  return html.classList.contains("translated-ltr")
    || html.classList.contains("translated-rtl")
    || document.querySelector("font") !== null;
}

export function installDomMutationGuard(): void {
  if (typeof Node === "undefined") return;
  const reported = new Set<string>();
  const report = (operation: "removeChild" | "insertBefore") => {
    if (reported.has(operation)) return;
    reported.add(operation);
    const translated = isPageTranslated();
    logger.warn("Skipped a DOM operation on a node moved outside React", { operation, translated });
    trackEvent("dom_mutation_conflict", { operation, translated });
  };

  const originalRemoveChild = Node.prototype.removeChild;
  Node.prototype.removeChild = function removeChild<T extends Node>(this: Node, child: T): T {
    if (child.parentNode !== this) {
      report("removeChild");
      return child;
    }
    return originalRemoveChild.call(this, child) as T;
  };

  const originalInsertBefore = Node.prototype.insertBefore;
  Node.prototype.insertBefore = function insertBefore<T extends Node>(this: Node, node: T, child: Node | null): T {
    if (child && child.parentNode !== this) {
      report("insertBefore");
      return originalInsertBefore.call(this, node, null) as T;
    }
    return originalInsertBefore.call(this, node, child) as T;
  };
}
