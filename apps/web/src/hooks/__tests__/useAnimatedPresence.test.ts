import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAnimatedPresence } from "../useAnimatedPresence";

describe("useAnimatedPresence", () => {
  const originalGetAnimations = document.getAnimations;
  const originalMatchMedia = window.matchMedia;

  const setReducedMotion = (reduced: boolean) => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: reduced && query === "(prefers-reduced-motion: reduce)",
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, "getAnimations", {
      writable: true,
      configurable: true,
      value: vi.fn(() => []),
    });
    setReducedMotion(false);
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
  });

  it("mounts immediately on open and holds mounted for exit duration before unmounting", () => {
    const { result, rerender } = renderHook(
      ({ open }) => useAnimatedPresence(open, 150),
      { initialProps: { open: false } },
    );

    expect(result.current).toEqual({ isMounted: false, isExiting: false, state: "entered" });

    rerender({ open: true });
    expect(result.current).toEqual({ isMounted: true, isExiting: false, state: "entered" });

    rerender({ open: false });
    expect(result.current).toEqual({ isMounted: true, isExiting: true, state: "exiting" });

    act(() => {
      vi.advanceTimersByTime(149);
    });
    expect(result.current).toEqual({ isMounted: true, isExiting: true, state: "exiting" });

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toEqual({ isMounted: false, isExiting: false, state: "entered" });
  });

  it("cancels exit immediately when re-opened mid-animation (100% interruptible)", () => {
    const { result, rerender } = renderHook(
      ({ open }) => useAnimatedPresence(open, 150),
      { initialProps: { open: true } },
    );

    // Begin exit
    rerender({ open: false });
    expect(result.current).toEqual({ isMounted: true, isExiting: true, state: "exiting" });

    // Re-open halfway through exit
    act(() => {
      vi.advanceTimersByTime(70);
    });
    rerender({ open: true });
    expect(result.current).toEqual({ isMounted: true, isExiting: false, state: "entered" });

    // Advancing past original exit deadline must NOT unmount the re-opened surface
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toEqual({ isMounted: true, isExiting: false, state: "entered" });
  });

  it("unmounts immediately (0ms) under prefers-reduced-motion: reduce", () => {
    setReducedMotion(true);

    const { result, rerender } = renderHook(
      ({ open }) => useAnimatedPresence(open, 150),
      { initialProps: { open: true } },
    );

    expect(result.current.isMounted).toBe(true);

    rerender({ open: false });
    expect(result.current).toEqual({ isMounted: false, isExiting: false, state: "entered" });
  });

  it("falls back to immediate unmount when Web Animations API is absent (headless DOM default)", () => {
    Object.defineProperty(document, "getAnimations", {
      writable: true,
      configurable: true,
      value: undefined,
    });

    const { result, rerender } = renderHook(
      ({ open }) => useAnimatedPresence(open, 150),
      { initialProps: { open: true } },
    );

    rerender({ open: false });
    expect(result.current).toEqual({ isMounted: false, isExiting: false, state: "entered" });
  });

  it("clears pending exit timers cleanly on unmount", () => {
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");
    const { rerender, unmount } = renderHook(
      ({ open }) => useAnimatedPresence(open, 150),
      { initialProps: { open: true } },
    );

    rerender({ open: false });
    unmount();

    expect(clearTimeoutSpy).toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(300);
    });
    clearTimeoutSpy.mockRestore();
  });
});
