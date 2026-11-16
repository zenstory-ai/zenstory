import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import React from "react";

import type { PendingEdit } from "../../types";
import { DiffReviewSplitView } from "../DiffReviewSplitView";

// Resolve against the real editor translation resources so titles/labels
// match what users actually see. The component calls `t(key, options)` with
// keys namespaced as `editor:<key>`.
vi.mock("react-i18next", async () => {
  const editorMessages = (
    await import("../../../public/locales/zh/editor.json")
  ).default as Record<string, string>;

  return {
    useTranslation: () => ({
      t: (key: string, options?: Record<string, unknown>) => {
        const bareKey = key.includes(":") ? key.split(":").pop()! : key;
        const template = editorMessages[bareKey] ?? key;
        if (!options) return template;
        return Object.entries(options).reduce(
          (acc, [k, v]) => acc.replaceAll(`{{${k}}}`, String(v)),
          template
        );
      },
    }),
  };
});

vi.mock("../InlineDiffEditor", () => ({
  InlineDiffEditor: React.forwardRef(function MockInlineDiffEditor(
    props: {
      activeEditId?: string | null;
      onSelectEdit?: (editId: string) => void;
      pendingEdits: PendingEdit[];
    },
    ref: React.ForwardedRef<{ scrollToEdit: (editId: string) => void }>
  ) {
    React.useImperativeHandle(ref, () => ({
      scrollToEdit: (_editId: string) => undefined,
    }));

    return (
      <div data-testid="inline-diff" data-active-edit-id={props.activeEditId ?? ""}>
        {props.pendingEdits.map((edit) => (
          <button key={edit.id} onClick={() => props.onSelectEdit?.(edit.id)}>
            {edit.id}
          </button>
        ))}
      </div>
    );
  }),
}));

const pendingEdits: PendingEdit[] = [
  {
    id: "edit-1",
    op: "replace",
    oldText: "旧内容",
    newText: "新内容",
    status: "pending",
  },
  {
    id: "edit-2",
    op: "insert_after",
    oldText: "",
    newText: "补充文本",
    status: "accepted",
  },
];

describe("DiffReviewSplitView", () => {
  it("triggers accept/reject/reset callbacks from queue actions", () => {
    const onAcceptEdit = vi.fn();
    const onRejectEdit = vi.fn();
    const onResetEdit = vi.fn();

    render(
      <DiffReviewSplitView
        originalContent="old"
        modifiedContent="new"
        pendingEdits={pendingEdits}
        onAcceptEdit={onAcceptEdit}
        onRejectEdit={onRejectEdit}
        onResetEdit={onResetEdit}
      />
    );

    // First card is pending -> accept/reject available
    fireEvent.click(screen.getAllByTitle(/接受 \(Y\)|Accept \(Y\)/)[0]!);
    fireEvent.click(screen.getAllByTitle(/拒绝 \(N\)|Reject \(N\)/)[0]!);

    // Switch to accepted filter and reset that item.
    fireEvent.click(screen.getByTitle(/已接受 \(1\)|Accepted \(1\)/));

    const enabledResetBtn = screen
      .getAllByTitle(/撤销到待审 \(U\)|Reset to pending \(U\)/)
      .find((btn) => !btn.hasAttribute("disabled"));
    expect(enabledResetBtn).toBeDefined();
    fireEvent.click(enabledResetBtn!);

    expect(onAcceptEdit).toHaveBeenCalledWith("edit-1");
    expect(onRejectEdit).toHaveBeenCalledWith("edit-1");
    expect(onResetEdit).toHaveBeenCalledWith("edit-2");
  });

  it("shows every change when some start set aside, so a preselected side is not hidden", () => {
    const onAcceptEdit = vi.fn();
    render(
      <DiffReviewSplitView
        originalContent="old"
        modifiedContent="new"
        pendingEdits={[{ ...pendingEdits[0]!, status: "rejected" }, { ...pendingEdits[1]!, status: "pending" }]}
        onAcceptEdit={onAcceptEdit}
        onRejectEdit={vi.fn()}
        onResetEdit={vi.fn()}
      />
    );

    // Both cards are listed; the set-aside one can be taken back.
    expect(screen.getAllByTitle(/接受 \(Y\)|Accept \(Y\)/)).toHaveLength(2);
    fireEvent.click(screen.getAllByTitle(/接受 \(Y\)|Accept \(Y\)/)[0]!);
    expect(onAcceptEdit).toHaveBeenCalledWith("edit-1");
  });

  it("labels the locate button with the resolved locale text, not a raw template", () => {
    render(
      <DiffReviewSplitView
        originalContent="old"
        modifiedContent="new"
        pendingEdits={pendingEdits}
        onAcceptEdit={vi.fn()}
        onRejectEdit={vi.fn()}
        onResetEdit={vi.fn()}
      />
    );

    const locateButtons = screen.getAllByRole("button", { name: "定位到文档 (L)" });
    expect(locateButtons.length).toBeGreaterThan(0);
    for (const button of locateButtons) {
      expect(button.getAttribute("title")).toBe("定位到文档 (L)");
    }
    for (const button of screen.getAllByRole("button")) {
      expect(button.getAttribute("aria-label") ?? "").not.toContain("{{");
      expect(button.getAttribute("title") ?? "").not.toContain("{{");
    }
  });

  it("supports per-edit keyboard shortcuts (Y/N/U)", () => {
    const onAcceptEdit = vi.fn();
    const onRejectEdit = vi.fn();
    const onResetEdit = vi.fn();

    const { container } = render(
      <DiffReviewSplitView
        originalContent="old"
        modifiedContent="new"
        pendingEdits={pendingEdits}
        onAcceptEdit={onAcceptEdit}
        onRejectEdit={onRejectEdit}
        onResetEdit={onResetEdit}
      />
    );

    const root = container.querySelector("div[tabindex='0']");
    expect(root).toBeTruthy();

    fireEvent.keyDown(root!, { key: "y" });
    fireEvent.keyDown(root!, { key: "n" });
    fireEvent.keyDown(root!, { key: "u" });

    // Active edit defaults to the first pending edit.
    expect(onAcceptEdit).toHaveBeenCalledWith("edit-1");
    expect(onRejectEdit).toHaveBeenCalledWith("edit-1");
    expect(onResetEdit).toHaveBeenCalledWith("edit-1");

    // Selecting from inline diff should move active edit.
    fireEvent.click(screen.getByRole("button", { name: "edit-2" }));
    fireEvent.keyDown(root!, { key: "u" });
    expect(onResetEdit).toHaveBeenCalledWith("edit-2");
  });

  function renderTwoPending() {
    return render(
      <DiffReviewSplitView
        originalContent="old"
        modifiedContent="new"
        pendingEdits={[
          { ...pendingEdits[0]!, status: "pending" },
          { ...pendingEdits[1]!, status: "pending" },
        ]}
        onAcceptEdit={vi.fn()}
        onRejectEdit={vi.fn()}
        onResetEdit={vi.fn()}
      />
    );
  }

  function rect(top: number, height: number): DOMRect {
    return { top, bottom: top + height, height, left: 0, right: 100, width: 100, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
  }

  it("never calls scrollIntoView, so the review pane itself is not scrolled", () => {
    const scrollIntoViewMock = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoViewMock;
    try {
      renderTwoPending();
      fireEvent.click(screen.getByRole("button", { name: "edit-2" }));
      expect(scrollIntoViewMock).not.toHaveBeenCalled();
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("scrolls only the queue list to the active card when the list overflows", () => {
    const { container } = renderTwoPending();
    const queue = screen.getByTestId("review-queue-list");
    const root = container.querySelector('[tabindex="0"]') as HTMLElement;
    expect(queue).toBeTruthy();
    Object.defineProperty(queue, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(queue, "clientHeight", { configurable: true, value: 200 });
    queue.getBoundingClientRect = () => rect(100, 200);
    queue.scrollTop = 0;
    // Every card reports a rect below the visible queue area.
    for (const el of Array.from(queue.querySelectorAll("*"))) {
      (el as HTMLElement).getBoundingClientRect = () => rect(400, 80);
    }
    root.scrollTop = 0;

    fireEvent.click(screen.getByRole("button", { name: "edit-2" }));

    // bottom overflow = 480 - 300 = 180; top offset = 400 - 100 = 300 -> min is 180
    expect(queue.scrollTop).toBe(180);
    expect(root.scrollTop).toBe(0);
  });

  it("scrolls the queue list up when the active card sits above the visible area", () => {
    renderTwoPending();
    const queue = screen.getByTestId("review-queue-list");
    Object.defineProperty(queue, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(queue, "clientHeight", { configurable: true, value: 200 });
    queue.getBoundingClientRect = () => rect(100, 200);
    queue.scrollTop = 300;
    for (const el of Array.from(queue.querySelectorAll("*"))) {
      (el as HTMLElement).getBoundingClientRect = () => rect(40, 80);
    }

    fireEvent.click(screen.getByRole("button", { name: "edit-2" }));

    // card top 40 is 60px above the list top 100
    expect(queue.scrollTop).toBe(240);
  });

  it("leaves scrolling alone when the queue list does not overflow (phone layout)", () => {
    renderTwoPending();
    const queue = screen.getByTestId("review-queue-list");
    Object.defineProperty(queue, "scrollHeight", { configurable: true, value: 200 });
    Object.defineProperty(queue, "clientHeight", { configurable: true, value: 200 });
    queue.getBoundingClientRect = () => rect(100, 200);
    for (const el of Array.from(queue.querySelectorAll("*"))) {
      (el as HTMLElement).getBoundingClientRect = () => rect(400, 80);
    }
    fireEvent.click(screen.getByRole("button", { name: "edit-2" }));
    expect(queue.scrollTop).toBe(0);
  });
});
