import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fileApi } from "../lib/api";
import { logger } from "../lib/logger";
import { toast } from "../lib/toast";
import { trackEvent } from "../lib/analytics";
import type { File as ProjectFile } from "../types";

/** What the draft upload accepts (the server splits each file by chapter headings). */
export const DRAFT_IMPORT_ACCEPT = ".txt,.md";

export interface DraftImportResult {
  /** Chapters created before the batch ended (all of them, or those before a failure or unmount). */
  files: ProjectFile[];
  /** An upload failed; its error was shown as a toast. */
  failed: boolean;
}

/**
 * Import an author's manuscript files into a project's draft folder: the file tree's 「+」,
 * the editor's empty state and the dashboard all use this. Uploading never calls a model.
 * Files go up one at a time; leaving the page stops the batch after the current file (no
 * toasts then). Whatever was created is always returned so the caller can show it.
 */
export function useDraftImport() {
  const { t } = useTranslation(["editor"]);
  const [isImporting, setIsImporting] = useState(false);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const importFiles = useCallback(async (
    projectId: string,
    files: FileList | readonly globalThis.File[],
    entry: string,
  ): Promise<DraftImportResult> => {
    const list = Array.from(files);
    const created: ProjectFile[] = [];
    if (list.length === 0) return { files: created, failed: false };
    setIsImporting(true);

    const errorCodeMap: Record<string, string> = {
      ERR_FILE_TOO_LARGE: t("editor:fileTree.errorFileTooLarge"),
      ERR_FILE_TYPE_INVALID: t("editor:fileTree.errorFileTypeInvalid"),
      ERR_FILE_CONTENT_TOO_LONG: t("editor:fileTree.errorFileContentTooLong"),
      ERR_VALIDATION_ERROR: t("editor:fileTree.errorValidation"),
    };
    const translateError = (raw: string) => {
      for (const [code, msg] of Object.entries(errorCodeMap)) {
        if (raw.includes(code)) {
          const fileName = raw.split(":")[0]?.trim();
          return fileName ? `${fileName}: ${msg}` : msg;
        }
      }
      return raw;
    };

    try {
      const allErrors: string[] = [];
      let chapters = 0;
      for (const file of list) {
        if (!mountedRef.current) return { files: created, failed: false };
        const result = await fileApi.uploadDraft(projectId, file);
        created.push(...(result.files ?? []));
        chapters += result.total ?? result.files?.length ?? 0;
        if (result.errors?.length) allErrors.push(...result.errors.map(translateError));
      }
      if (!mountedRef.current) return { files: created, failed: false };
      if (allErrors.length > 0) toast.error(allErrors.join("\n"));
      if (chapters > 0) {
        toast.success(t("editor:fileTree.draftUploadSuccess", { count: chapters }));
      } else if (allErrors.length === 0) {
        toast.info(t("editor:fileTree.draftUploadEmpty"));
      }
      trackEvent("draft_imported", { project_id: projectId, entry, file_count: list.length, chapter_count: chapters });
      return { files: created, failed: false };
    } catch (error) {
      logger.error("Failed to upload draft:", error);
      if (mountedRef.current) {
        toast.error(error instanceof Error ? error.message : t("editor:fileTree.uploadFailed"));
      }
      return { files: created, failed: true };
    } finally {
      if (mountedRef.current) setIsImporting(false);
    }
  }, [t]);

  return { importFiles, isImporting };
}
