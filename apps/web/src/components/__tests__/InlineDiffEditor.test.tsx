import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingEdit } from "../../types";
import { InlineDiffEditor } from "../InlineDiffEditor";

// Resolve against the shipped zh editor copy so the label matches what authors hear.
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
          template,
        );
      },
    }),
  };
});

describe("InlineDiffEditor", () => {
  it("keeps mouse and keyboard location available without changing review decisions", () => {
    const select = vi.fn();
    const finish = vi.fn();
    render(<div onKeyDown={finish}><InlineDiffEditor
      originalContent="旧的一句。"
      modifiedContent="新的一句。"
      pendingEdits={[{ id: "edit-1", op: "replace", oldText: "旧的一句。", newText: "新的一句。", status: "pending" }]}
      activeEditId="edit-1"
      onSelectEdit={select}
    /></div>);
    const target = screen.getByRole("button", { name: "定位到第 1 处修改" });
    fireEvent.click(target);
    fireEvent.keyDown(target, { key: "Enter" });
    fireEvent.keyDown(target, { key: " " });
    expect(select.mock.calls).toEqual([["edit-1"], ["edit-1"], ["edit-1"]]);
    expect(finish).not.toHaveBeenCalled();
    expect(target).toHaveAttribute("data-status", "pending");
    expect(target).toHaveTextContent("旧的一句。新的一句。");
  });

  it("labels each locatable change in Chinese by its position, not its internal id", () => {
    const pendingEdits: PendingEdit[] = [
      { id: "edit-7f1c2a9e", op: "replace", oldText: "旧的一句。", newText: "新的一句。", status: "pending" },
    ];

    render(
      <InlineDiffEditor
        originalContent={"开头。\n\n旧的一句。\n\n结尾。"}
        modifiedContent={"开头。\n\n新的一句。\n\n结尾。"}
        pendingEdits={pendingEdits}
        activeEditId={null}
        onSelectEdit={() => {}}
      />,
    );

    const targets = screen.getAllByRole("button", { name: "定位到第 1 处修改" });
    expect(targets.length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Locate change|edit-7f1c2a9e/ })).not.toBeInTheDocument();
  });
});
