import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useSlidingIndicator } from "../useSlidingIndicator";

interface MockRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

class MockResizeObserver {
  static instances: MockResizeObserver[] = [];
  callback: ResizeObserverCallback;
  observed = new Set<Element>();
  disconnected = false;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    MockResizeObserver.instances.push(this);
  }

  observe(target: Element) {
    this.observed.add(target);
    // Per W3C ResizeObserver spec, observing a target schedules an initial callback.
    queueMicrotask(() => {
      if (!this.disconnected && this.observed.has(target)) {
        this.callback([], this as unknown as ResizeObserver);
      }
    });
  }

  unobserve(target: Element) {
    this.observed.delete(target);
  }

  disconnect() {
    this.disconnected = true;
    this.observed.clear();
  }

  trigger() {
    if (!this.disconnected) {
      this.callback([], this as unknown as ResizeObserver);
    }
  }
}

function setElementBox(el: HTMLElement, rect: MockRect) {
  Object.defineProperty(el, "offsetLeft", { configurable: true, get: () => rect.left });
  Object.defineProperty(el, "offsetTop", { configurable: true, get: () => rect.top });
  Object.defineProperty(el, "offsetWidth", { configurable: true, get: () => rect.width });
  Object.defineProperty(el, "offsetHeight", { configurable: true, get: () => rect.height });
}

const ITEMS = [
  { id: "a", rect: { left: 4, top: 4, width: 80, height: 32 } },
  { id: "b", rect: { left: 92, top: 4, width: 104, height: 32 } },
  { id: "c", rect: { left: 204, top: 4, width: 96, height: 32 } },
];

function SegmentedHarness({
  activeKey,
  locale = "zh",
  mounted = true,
  rects = ITEMS,
}: {
  activeKey: string;
  locale?: string;
  mounted?: boolean;
  rects?: typeof ITEMS;
}) {
  const { containerRef, indicatorRef } = useSlidingIndicator<HTMLDivElement>(activeKey, [locale]);

  if (!mounted) return null;

  return (
    <div ref={containerRef} data-testid="seg-container">
      <span ref={indicatorRef} data-testid="seg-indicator" data-animate="false" />
      {rects.map((item) => (
        <button
          key={item.id}
          type="button"
          data-segment-active={activeKey === item.id ? "true" : undefined}
          ref={(el) => {
            if (el) setElementBox(el, item.rect);
          }}
        >
          {item.id}
        </button>
      ))}
    </div>
  );
}

describe("useSlidingIndicator", () => {
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    MockResizeObserver.instances = [];
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    vi.stubGlobal("ResizeObserver", originalResizeObserver);
  });

  it("snaps on initial mount and glides on activeKey change without ResizeObserver aborting it", async () => {
    const { getByTestId, rerender } = render(<SegmentedHarness activeKey="a" />);

    const container = getByTestId("seg-container");
    const pill = getByTestId("seg-indicator");

    expect(container.dataset.indicatorReady).toBe("true");
    expect(pill.dataset.animate).toBe("false");
    expect(pill.style.transform).toBe("translate3d(4px, 4px, 0)");
    expect(pill.style.width).toBe("80px");
    expect(MockResizeObserver.instances).toHaveLength(1);

    // Let initial ResizeObserver delivery flush
    await act(async () => {
      await Promise.resolve();
    });
    expect(pill.dataset.animate).toBe("false");

    // Switch active tab: must glide AND must NOT recreate ResizeObserver
    rerender(<SegmentedHarness activeKey="b" />);
    expect(MockResizeObserver.instances).toHaveLength(1);
    expect(pill.dataset.animate).toBe("true");
    expect(pill.style.transform).toBe("translate3d(92px, 4px, 0)");
    expect(pill.style.width).toBe("104px");

    // Even if ResizeObserver fires with unchanged geometry, data-animate stays "true"
    act(() => {
      MockResizeObserver.instances[0].trigger();
    });
    expect(pill.dataset.animate).toBe("true");
  });

  it("snaps silently when layoutDeps change (e.g. locale switch) with the same activeKey", () => {
    const { getByTestId, rerender } = render(<SegmentedHarness activeKey="b" locale="zh" />);
    const pill = getByTestId("seg-indicator");
    expect(pill.dataset.animate).toBe("false");

    // Switch activeKey to enable animation first
    rerender(<SegmentedHarness activeKey="c" locale="zh" />);
    expect(pill.dataset.animate).toBe("true");

    // Locale switch widens button "c" while activeKey stays "c": must snap cleanly
    const widerItems = ITEMS.map((item) =>
      item.id === "c" ? { ...item, rect: { ...item.rect, width: 140 } } : item,
    );
    rerender(<SegmentedHarness activeKey="c" locale="en" rects={widerItems} />);
    expect(pill.dataset.animate).toBe("false");
    expect(pill.style.width).toBe("140px");
  });

  it("snaps without flying in from y=0 when indicator DOM unmounts and remounts (e.g. Modal reopen)", () => {
    const { getByTestId, rerender } = render(<SegmentedHarness activeKey="b" mounted={true} />);
    expect(getByTestId("seg-indicator").dataset.animate).toBe("false");

    // Move to "c" while mounted
    rerender(<SegmentedHarness activeKey="c" mounted={true} />);
    expect(getByTestId("seg-indicator").dataset.animate).toBe("true");

    // Close Modal (unmounts container & pill DOM while hook owner stays mounted)
    rerender(<SegmentedHarness activeKey="c" mounted={false} />);

    // Reopen Modal on a different or same tab: fresh DOM node must snap silently
    rerender(<SegmentedHarness activeKey="b" mounted={true} />);
    const reopenedPill = getByTestId("seg-indicator");
    expect(reopenedPill.dataset.animate).toBe("false");
    expect(reopenedPill.style.transform).toBe("translate3d(92px, 4px, 0)");
  });

  it("snaps when ResizeObserver reports an actual geometry change", () => {
    const { getByTestId, rerender } = render(<SegmentedHarness activeKey="a" />);
    rerender(<SegmentedHarness activeKey="b" />);
    const pill = getByTestId("seg-indicator");
    expect(pill.dataset.animate).toBe("true");

    // Simulate container resize changing button "b" coordinates
    const activeBtn = getByTestId("seg-container").querySelector<HTMLElement>('[data-segment-active="true"]')!;
    setElementBox(activeBtn, { left: 60, top: 4, width: 90, height: 32 });

    act(() => {
      MockResizeObserver.instances[0].trigger();
    });

    expect(pill.dataset.animate).toBe("false");
    expect(pill.style.transform).toBe("translate3d(60px, 4px, 0)");
    expect(pill.style.width).toBe("90px");
  });

  it("hides the indicator cleanly when layout is zero-sized (jsdom/happy-dom default)", () => {
    const zeroItems = ITEMS.map((item) => ({
      ...item,
      rect: { left: 0, top: 0, width: 0, height: 0 },
    }));
    const { getByTestId } = render(<SegmentedHarness activeKey="a" rects={zeroItems} />);
    expect(getByTestId("seg-indicator").style.opacity).toBe("0");
    expect(getByTestId("seg-container").dataset.indicatorReady).toBeUndefined();
  });
});
