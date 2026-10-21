import { useEffect, useState } from "react";
import { act, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const { trackEventMock } = vi.hoisted(() => ({ trackEventMock: vi.fn() }));

vi.mock("../analytics", () => ({ trackEvent: trackEventMock }));

import { installDomMutationGuard } from "../domMutationGuard";

const originalRemoveChild = Node.prototype.removeChild;
const originalInsertBefore = Node.prototype.insertBefore;

/** What Chrome page translation does: each text node becomes a <font> wrapper. */
function translate(root: Element) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const textNodes: Text[] = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode as Text);
  for (const node of textNodes) {
    const font = document.createElement("font");
    font.textContent = node.nodeValue;
    node.replaceWith(font);
  }
}

let setRunning: (running: boolean) => void = () => undefined;

/** A status line that swaps its icon next to a bare text label, like the editor toolbar. */
function StatusLine() {
  const [running, set] = useState(true);
  useEffect(() => {
    setRunning = set;
  }, []);
  return (
    <span data-testid="status">
      {running ? <b>spinner</b> : <i>done</i>}
      {"Polishing"}
      {running && <em>…</em>}
    </span>
  );
}

describe("installDomMutationGuard", () => {
  beforeAll(() => installDomMutationGuard());

  afterAll(() => {
    Node.prototype.removeChild = originalRemoveChild;
    Node.prototype.insertBefore = originalInsertBefore;
  });

  beforeEach(() => trackEventMock.mockReset());

  it("keeps React rendering after page translation moved its text nodes", () => {
    render(<StatusLine />);
    translate(screen.getByTestId("status"));

    expect(() => act(() => setRunning(false))).not.toThrow();

    expect(screen.getByTestId("status").querySelector("i")).not.toBeNull();
    expect(trackEventMock).toHaveBeenCalledWith("dom_mutation_conflict", { operation: "insertBefore", translated: true });
  });

  it("leaves ordinary DOM operations untouched", () => {
    const parent = document.createElement("div");
    const first = parent.appendChild(document.createElement("p"));
    const second = document.createElement("p");

    parent.insertBefore(second, first);
    parent.removeChild(first);

    expect([...parent.childNodes]).toEqual([second]);
    expect(trackEventMock).not.toHaveBeenCalled();
  });
});
