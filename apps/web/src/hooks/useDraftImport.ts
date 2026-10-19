import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fileApi } from "../lib/api";
import { logger } from "../lib/logger";
import { toast } from "../lib/toast";
import { trackEvent } from "../lib/analytics";
import type { File as ProjectFile } from "../types";

/** What the draft upload accepts (the server splits each file by chapter headings). */
export const DRAFT_IMPORT_ACCEPT = ".txt,.md";

/**
 * Import an author's manuscript files into a project's draft folder: the file tree's 「+」,
 * the editor's empty state, the chat's 「看看我的稿子」 card and the dashboard all use this.
 * Uploading never calls a model. Returns the created chapters (possibly empty), or null when
 * the upload failed; errors are shown as toasts.
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
  ): Promise<ProjectFile[] | null> => {
    const list = Array.from(files);
    if (list.length === 0) return [];
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
      const created: ProjectFile[] = [];
      let chapters = 0;
      for (const file of list) {
        if (!mountedRef.current) return null;
        const result = await fileApi.uploadDraft(projectId, file);
        created.push(...(result.files ?? []));
        chapters += result.total ?? result.files?.length ?? 0;
        if (result.errors?.length) allErrors.push(...result.errors.map(translateError));
      }
      if (!mountedRef.current) return null;
      if (allErrors.length > 0) toast.error(allErrors.join("\n"));
      if (chapters > 0) {
        toast.success(t("editor:fileTree.draftUploadSuccess", { count: chapters }));
      } else if (allErrors.length === 0) {
        toast.info(t("editor:fileTree.draftUploadEmpty"));
      }
      trackEvent("draft_imported", { project_id: projectId, entry, file_count: list.length, chapter_count: chapters });
      return created;
    } catch (error) {
      logger.error("Failed to upload draft:", error);
      if (mountedRef.current) {
        toast.error(error instanceof Error ? error.message : t("editor:fileTree.uploadFailed"));
      }
      return null;
    } finally {
      if (mountedRef.current) setIsImporting(false);
    }
  }, [t]);

  return { importFiles, isImporting };
}
