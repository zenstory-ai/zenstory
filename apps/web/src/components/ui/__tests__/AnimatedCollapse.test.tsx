import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnimatedCollapse, AnimatedDetails } from "../AnimatedCollapse";

describe("AnimatedCollapse and AnimatedDetails", () => {
  const originalGetAnimations = document.getAnimations;
  const originalMatchMedia = window.matchMedia;
  const originalRAF = window.requestAnimationFrame;
  const originalCAF = window.cancelAnimationFrame;

  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, "getAnimations", {
      writable: true,
      configurable: true,
      value: vi.fn(() => []),
    });
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    window.requestAnimationFrame = (cb: FrameRequestCallback) =>
      window.setTimeout(() => cb(performance.now()), 16);
    window.cancelAnimationFrame = (id: number) => window.clearTimeout(id);
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(document, "getAnimations", {
      writable: true,
      configurable: true,
      value: originalGetAnimations,
    });
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: originalMatchMedia,
    });
    window.requestAnimationFrame = originalRAF;
    window.cancelAnimationFrame = originalCAF;
  });

  it("animates open and close smoothly and stays interruptible on rapid toggles", () => {
    const { container, rerender } = render(
      <AnimatedCollapse open={false} fast>
        <p>Folder children</p>
      </AnimatedCollapse>,
    );

    expect(screen.queryByText("Folder children")).not.toBeInTheDocument();

    // Open
    rerender(
      <AnimatedCollapse open={true} fast>
        <p>Folder children</p>
      </AnimatedCollapse>,
    );
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper).toHaveClass("motion-collapse", "motion-collapse-fast");
    expect(wrapper).toHaveAttribute("data-open", "false");

    act(() => {
      vi.advanceTimersByTime(32);
    });
    expect(wrapper).toHaveAttribute("data-open", "true");

    // Begin close
    rerender(
      <AnimatedCollapse open={false} fast>
        <p>Folder children</p>
      </AnimatedCollapse>,
    );
    expect(wrapper).toHaveAttribute("data-open", "false");
    expect(wrapper).toHaveAttribute("data-exiting", "true");
    expect(wrapper).toHaveClass("pointer-events-none");
    expect(screen.getByText("Folder children")).toBeInTheDocument();

    // Interrupt mid-close by re-opening
    act(() => {
      vi.advanceTimersByTime(80);
    });
    rerender(
      <AnimatedCollapse open={true} fast>
        <p>Folder children</p>
      </AnimatedCollapse>,
    );
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByText("Folder children")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveAttribute("data-open", "true");

    // Complete full close
    rerender(
      <AnimatedCollapse open={false} fast>
        <p>Folder children</p>
      </AnimatedCollapse>,
    );
    act(() => {
      vi.advanceTimersByTime(160);
    });
    expect(screen.queryByText("Folder children")).not.toBeInTheDocument();
  });

  it("preserves native details open semantics while animating disclosure height", () => {
    render(
      <AnimatedDetails data-testid="demo-details" summary="Toggle details">
        <p>Disclosure body</p>
      </AnimatedDetails>,
    );

    const details = screen.getByTestId("demo-details");
    expect(details).not.toHaveAttribute("open");
    expect(screen.queryByText("Disclosure body")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Toggle details"));
    expect(details).toHaveAttribute("open");
    expect(screen.getByText("Disclosure body")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Toggle details"));
    // Held open during 240ms collapse
    expect(details).toHaveAttribute("open");

    act(() => {
      vi.advanceTimersByTime(240);
    });
    expect(details).not.toHaveAttribute("open");
    expect(screen.queryByText("Disclosure body")).not.toBeInTheDocument();
  });
});
