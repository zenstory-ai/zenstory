import React from "react";
import { captureException } from "../lib/analytics";
import { logger } from "../lib/logger";
import { ErrorFallback } from "./ErrorFallback";

interface ErrorBoundaryProps {
  /** Reported as `feature_area` so errors can be grouped by panel. */
  area: string;
  /** "page" replaces the whole app; "panel" only the wrapped panel. */
  variant?: "page" | "panel";
  /** Extra classes for the wrapper element (it uses `display: contents`). */
  className?: string;
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Catches render errors so one failure does not unmount the whole React tree
 * (a blank page). The error is reported to error tracking with its area.
 */
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    logger.error(`[ErrorBoundary:${this.props.area}] render error`, error);
    captureException(error, {
      feature_area: this.props.area,
      boundary: this.props.variant ?? "panel",
      component_stack: info.componentStack?.slice(0, 2000) ?? undefined,
    });
  }

  private handleRetry = (): void => {
    this.setState({ error: null });
  };

  render(): React.ReactNode {
    const variant = this.props.variant ?? "panel";
    const content = this.state.error
      ? <ErrorFallback variant={variant} onRetry={this.handleRetry} />
      : this.props.children;

    if (!this.props.className) return content;
    return <div className={`contents ${this.props.className}`}>{content}</div>;
  }
}
