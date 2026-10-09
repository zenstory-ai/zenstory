import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ReactNode } from "react";

import { ImportMaterialDialog } from "../ImportMaterialDialog";
import { fileApi } from "../../lib/api";
import { materialsApi } from "../../lib/materialsApi";

let currentProjectId = "project-1";

vi.mock("../../contexts/ProjectContext", () => ({
  useProject: () => ({
    currentProjectId,
  }),
}));

vi.mock("../../lib/api", () => ({
  fileApi: {
    getTree: vi.fn(),
  },
}));

vi.mock("../../lib/materialsApi", () => ({
  materialsApi: {
    importToProject: vi.fn(),
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("../ui/Modal", () => {
  const Modal = ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null;

  Modal.Header = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  Modal.Body = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  Modal.Footer = ({ children }: { children: ReactNode }) => <div>{children}</div>;

  return {
    default: Modal,
  };
});

describe("ImportMaterialDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentProjectId = "project-1";
    vi.mocked(fileApi.getTree).mockResolvedValue({
      tree: [
        { id: "folder-1", title: "Other Folder", file_type: "folder" },
      ],
    } as never);
  });

  it("resets target folder when dialog reopens without a recommended folder", async () => {
    const preview = {
      title: "Character Preview",
      markdown: "content",
      novel_title: "Novel",
      suggested_file_type: "character",
      suggested_folder_name: "Characters",
      suggested_file_name: "Hero-reference",
    };

    const { rerender } = render(
      <ImportMaterialDialog
        isOpen
        onClose={vi.fn()}
        preview={preview}
        novelId={1}
        entityType="characters"
        entityId={1}
        onSuccess={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(fileApi.getTree).toHaveBeenCalledTimes(1);
    });

    const folderSelect = screen.getByRole("combobox");
    // The folder options render after the tree request resolves; selecting earlier is a no-op.
    await waitFor(() => {
      expect(folderSelect.querySelector('option[value="folder-1"]')).not.toBeNull();
    });
    fireEvent.change(folderSelect, { target: { value: "folder-1" } });
    expect(folderSelect).toHaveValue("folder-1");

    rerender(
      <ImportMaterialDialog
        isOpen={false}
        onClose={vi.fn()}
        preview={preview}
        novelId={1}
        entityType="characters"
        entityId={1}
        onSuccess={vi.fn()}
      />
    );

    rerender(
      <ImportMaterialDialog
        isOpen
        onClose={vi.fn()}
        preview={preview}
        novelId={1}
        entityType="characters"
        entityId={1}
        onSuccess={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(fileApi.getTree).toHaveBeenCalledTimes(2);
      expect(screen.getByRole("combobox")).toHaveValue("");
    });
  });

  it("shows folder load failure and retries", async () => {
    vi.mocked(fileApi.getTree)
      .mockRejectedValueOnce(new Error("folders offline"))
      .mockResolvedValueOnce({ tree: [] } as never);
    render(
      <ImportMaterialDialog isOpen onClose={vi.fn()} preview={{
        title: "Preview", markdown: "content", novel_title: "Novel",
        suggested_file_type: "character", suggested_folder_name: "Characters", suggested_file_name: "Hero",
      }} novelId={1} entityType="characters" entityId={1} onSuccess={vi.fn()} />
    );
    expect(await screen.findByText("editor:fileTree.importDialog.folderLoadFailed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "common:retry" }));
    await waitFor(() => expect(fileApi.getTree).toHaveBeenCalledTimes(2));
  });

  it("blocks cancel while a paid import is pending", async () => {
    vi.mocked(materialsApi.importToProject).mockReturnValueOnce(new Promise(() => {}));
    const onClose = vi.fn();
    render(
      <ImportMaterialDialog isOpen onClose={onClose} preview={{
        title: "Preview", markdown: "content", novel_title: "Novel",
        suggested_file_type: "character", suggested_folder_name: "Characters", suggested_file_name: "Hero",
      }} novelId={1} entityType="characters" entityId={1} onSuccess={vi.fn()} />
    );
    fireEvent.click(screen.getByRole("button", { name: "editor:fileTree.importDialog.confirm" }));
    const cancel = screen.getByRole("button", { name: "editor:fileTree.importDialog.cancel" });
    expect(cancel).toBeDisabled();
    fireEvent.click(cancel);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("does not let project A folders overwrite project B folders", async () => {
    let resolveProjectA!: (value: unknown) => void;
    vi.mocked(fileApi.getTree)
      .mockReturnValueOnce(new Promise((resolve) => { resolveProjectA = resolve; }) as never)
      .mockResolvedValueOnce({
        tree: [{ id: "folder-b", title: "Project B Folder", file_type: "folder" }],
      } as never);
    const preview = {
      title: "Preview", markdown: "content", novel_title: "Novel",
      suggested_file_type: "character", suggested_folder_name: "Characters", suggested_file_name: "Hero",
    };
    const view = render(
      <ImportMaterialDialog isOpen onClose={vi.fn()} preview={preview}
        novelId={1} entityType="characters" entityId={1} onSuccess={vi.fn()} />
    );
    await waitFor(() => expect(fileApi.getTree).toHaveBeenCalledWith("project-1"));

    currentProjectId = "project-2";
    view.rerender(
      <ImportMaterialDialog isOpen onClose={vi.fn()} preview={preview}
        novelId={1} entityType="characters" entityId={1} onSuccess={vi.fn()} />
    );
    expect(await screen.findByRole("option", { name: "Project B Folder" })).toBeInTheDocument();
    resolveProjectA({ tree: [{ id: "folder-a", title: "Project A Folder", file_type: "folder" }] });
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole("option", { name: "Project A Folder" })).not.toBeInTheDocument();
  });
});
