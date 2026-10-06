import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { captureExceptionMock } = vi.hoisted(() => ({ captureExceptionMock: vi.fn() }));

vi.mock("../../lib/analytics", () => ({
  captureException: captureExceptionMock,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

import { ErrorBoundary } from "../ErrorBoundary";

let shouldThrow = true;

function Flaky() {
  if (shouldThrow) {
    throw new Error("render exploded");
  }
  return <div>panel content</div>;
}

describe("ErrorBoundary", () => {
  const reloadSpy = vi.fn();
  const originalLocation = window.location;

  beforeEach(() => {
    shouldThrow = true;
    captureExceptionMock.mockReset();
    reloadSpy.mockReset();
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, reload: reloadSpy },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
    vi.restoreAllMocks();
  });

  it("shows a page fallback with reload and reports the error", () => {
    render(
      <ErrorBoundary area="app" variant="page">
        <Flaky />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("重试")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载页面" }));
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(captureExceptionMock).toHaveBeenCalledWith(
      expect.objectContaining({ message: "render exploded" }),
      expect.objectContaining({ feature_area: "app", boundary: "page" }),
    );
  });

  it("isolates a panel failure and can retry it", () => {
    render(
      <div>
        <ErrorBoundary area="chat" className="ph-no-capture">
          <Flaky />
        </ErrorBoundary>
        <div>editor still here</div>
      </div>,
    );

    expect(screen.getByText("editor still here")).toBeInTheDocument();
    expect(captureExceptionMock).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ feature_area: "chat", boundary: "panel" }),
    );

    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(screen.getByText("panel content")).toBeInTheDocument();
  });

  it("wraps children in a layout-neutral ph-no-capture container", () => {
    shouldThrow = false;
    render(
      <ErrorBoundary area="editor" className="ph-no-capture">
        <Flaky />
      </ErrorBoundary>,
    );

    const wrapper = screen.getByText("panel content").parentElement;
    expect(wrapper).toHaveClass("ph-no-capture");
    expect(wrapper).toHaveClass("contents");
  });
});
