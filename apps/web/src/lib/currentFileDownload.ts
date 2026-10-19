import { fileApi } from "./api";
import { flushOpenEditor } from "./editorSaveTracker";

/** The open editor could not store the author's typed text, so the download would be stale. */
export class UnsavedEditorError extends Error {
  constructor() {
    super("unsaved editor text");
    this.name = "UnsavedEditorError";
  }
}

/** Characters most file systems reject in a file name (control characters are dropped too). */
const UNSAFE_FILENAME_CHARS = /[\\/:*?"<>|]/g;

export function currentFileDownloadName(title: string | null | undefined): string {
  const printable = Array.from(title || "").map((char) => (char.charCodeAt(0) < 32 ? " " : char)).join("");
  const base = printable.replace(UNSAFE_FILENAME_CHARS, " ").replace(/\s+/g, " ").trim();
  return `${base || "untitled"}.txt`;
}

/**
 * Downloads one file's saved text as UTF-8 TXT (#276) — the program copies the text, no model is
 * involved. When the editor has this file open its unsaved text is saved first; if that save
 * does not go through, nothing is downloaded rather than handing out an older copy.
 */
export async function downloadCurrentFile(fileId: string): Promise<{ filename: string }> {
  const pending = flushOpenEditor();
  if (pending) {
    const flushed = await pending;
    if (flushed.fileId === fileId && !flushed.saved) throw new UnsavedEditorError();
  }
  const file = await fileApi.get(fileId);
  const filename = currentFileDownloadName(file.title);
  const blob = new Blob([file.content ?? ""], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
  return { filename };
}
