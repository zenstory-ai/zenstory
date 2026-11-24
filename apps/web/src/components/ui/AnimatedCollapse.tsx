import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { canAnimatePresence, useAnimatedPresence } from "../../hooks/useAnimatedPresence";

interface AnimatedCollapseProps {
  /** Whether the collapsible content is expanded. */
  open: boolean;
  /** Use the snappy 160ms curve (ideal for frequent tree folder navigation). */
  fast?: boolean;
  /** Optional class on the outer grid wrapper. */
  className?: string;
  /** Optional class on the inner overflow-hidden clip container. */
  innerClassName?: string;
  /** Optional DOM id on the outer wrapper (for aria-controls). */
  id?: string;
  /** Optional ARIA role on the outer wrapper (e.g. "group" for tree children). */
  role?: string;
  children: React.ReactNode;
}

/**
 * Smooth open/close height choreography powered by CSS Grid `grid-template-rows: 0fr <-> 1fr`
 * paired with `useAnimatedPresence`.
 *
 * - Keeps collapsed children unmounted when closed (preserving keyboard navigation,
 *   lazy rendering, and DOM absence assertions).
 * - Animates height + opacity smoothly on both expand and collapse in real browsers.
 * - Releases `overflow: hidden` once settled open (`data-settled="true"`) so outward
 *   keyboard focus rings (`focus-visible:ring-2` / `outline-offset`) are never clipped.
 * - Immediately interruptible on rapid double-toggles; instant under reduced motion.
 */
export const AnimatedCollapse: React.FC<AnimatedCollapseProps> = ({
  open,
  fast = false,
  className = "",
  innerClassName = "",
  id,
  role,
  children,
}) => {
  const durationMs = fast ? 160 : 240;
  const { isMounted, isExiting } = useAnimatedPresence(open, durationMs);
  const [expanded, setExpanded] = useState(() => open);
  const [settled, setSettled] = useState(() => open);
  const [prevOpen, setPrevOpen] = useState(open);

  if (open !== prevOpen) {
    setPrevOpen(open);
    if (!open) {
      setExpanded(false);
      setSettled(false);
    } else if (!canAnimatePresence()) {
      setExpanded(true);
      setSettled(true);
    } else {
      setSettled(false);
    }
  }

  useEffect(() => {
    if (!open || expanded) return;
    if (!canAnimatePresence()) {
      setExpanded(true); // eslint-disable-line react-hooks/set-state-in-effect
      setSettled(true);
      return;
    }
    let frame2 = 0;
    const frame1 = requestAnimationFrame(() => {
      frame2 = requestAnimationFrame(() => {
        setExpanded(true);
      });
    });
    return () => {
      cancelAnimationFrame(frame1);
      if (frame2) cancelAnimationFrame(frame2);
    };
  }, [open, expanded]);

  useEffect(() => {
    if (!open || !expanded || settled) return;
    if (!canAnimatePresence()) {
      setSettled(true); // eslint-disable-line react-hooks/set-state-in-effect
      return;
    }
    const timer = window.setTimeout(() => {
      setSettled(true);
    }, durationMs + 40);
    return () => window.clearTimeout(timer);
  }, [open, expanded, settled, durationMs]);

  if (!isMounted) return null;

  const isOpenActive = expanded && !isExiting;

  return (
    <div
      id={id}
      role={role}
      data-open={isOpenActive ? "true" : "false"}
      data-settled={isOpenActive && settled ? "true" : undefined}
      data-exiting={isExiting ? "true" : undefined}
      aria-hidden={isExiting ? true : undefined}
      inert={isExiting ? true : undefined}
      onTransitionEnd={(e) => {
        if (e.target === e.currentTarget && e.propertyName === "grid-template-rows" && isOpenActive) {
          setSettled(true);
        }
      }}
      className={`motion-collapse ${fast ? "motion-collapse-fast" : ""} ${isExiting ? "pointer-events-none" : ""} ${className}`}
    >
      <div className={`motion-collapse-inner ${innerClassName}`}>{children}</div>
    </div>
  );
};

interface AnimatedDetailsProps {
  defaultOpen?: boolean;
  className?: string;
  summaryClassName?: string;
  summaryStyle?: React.CSSProperties;
  summary: React.ReactNode | ((isOpen: boolean) => React.ReactNode);
  contentClassName?: string;
  "data-testid"?: string;
  children: React.ReactNode;
}

/**
 * Native `<details>`/`<summary>` disclosure with smooth CSS Grid open/close height
 * choreography. Preserves `<details open>` DOM semantics, releases `overflow: hidden`
 * when settled open for outward focus rings, and closes instantly in tests/reduced-motion.
 */
export const AnimatedDetails: React.FC<AnimatedDetailsProps> = ({
  defaultOpen = false,
  className = "",
  summaryClassName = "",
  summaryStyle,
  summary,
  contentClassName = "",
  "data-testid": testId,
  children,
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const { isMounted, isExiting } = useAnimatedPresence(isOpen, 240);
  const [expanded, setExpanded] = useState(defaultOpen);
  const [settled, setSettled] = useState(defaultOpen);
  const [prevOpen, setPrevOpen] = useState(isOpen);
  const detailsRef = useRef<HTMLDetailsElement>(null);

  if (isOpen !== prevOpen) {
    setPrevOpen(isOpen);
    if (!isOpen) {
      setExpanded(false);
      setSettled(false);
    } else if (!canAnimatePresence()) {
      setExpanded(true);
      setSettled(true);
    } else {
      setSettled(false);
    }
  }

  useLayoutEffect(() => {
    const el = detailsRef.current;
    if (el && el.open !== isMounted) {
      el.open = isMounted;
    }
  }, [isMounted, isOpen]);

  useEffect(() => {
    if (!isOpen || expanded) return;
    if (!canAnimatePresence()) {
      setExpanded(true); // eslint-disable-line react-hooks/set-state-in-effect
      setSettled(true);
      return;
    }
    let frame2 = 0;
    const frame1 = requestAnimationFrame(() => {
      frame2 = requestAnimationFrame(() => {
        setExpanded(true);
      });
    });
    return () => {
      cancelAnimationFrame(frame1);
      if (frame2) cancelAnimationFrame(frame2);
    };
  }, [isOpen, expanded]);

  useEffect(() => {
    if (!isOpen || !expanded || settled) return;
    if (!canAnimatePresence()) {
      setSettled(true); // eslint-disable-line react-hooks/set-state-in-effect
      return;
    }
    const timer = window.setTimeout(() => {
      setSettled(true);
    }, 280);
    return () => window.clearTimeout(timer);
  }, [isOpen, expanded, settled]);

  const isOpenActive = expanded && !isExiting;

  return (
    <details
      ref={detailsRef}
      data-testid={testId}
      className={className}
      open={isMounted || undefined}
    >
      <summary
        className={summaryClassName}
        style={summaryStyle}
        onClick={(e) => {
          e.preventDefault();
          setIsOpen((prev) => !prev);
        }}
      >
        {typeof summary === "function" ? summary(isOpen) : summary}
      </summary>
      {isMounted && (
        <div
          data-open={isOpenActive ? "true" : "false"}
          data-settled={isOpenActive && settled ? "true" : undefined}
          aria-hidden={isExiting ? true : undefined}
          inert={isExiting ? true : undefined}
          onTransitionEnd={(e) => {
            if (e.target === e.currentTarget && e.propertyName === "grid-template-rows" && isOpenActive) {
              setSettled(true);
            }
          }}
          className={`motion-collapse ${isExiting ? "pointer-events-none" : ""}`}
        >
          <div className={`motion-collapse-inner ${contentClassName}`}>{children}</div>
        </div>
      )}
    </details>
  );
};

export default AnimatedCollapse;
