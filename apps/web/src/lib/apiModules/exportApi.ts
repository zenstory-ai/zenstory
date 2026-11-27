import { authenticatedFetch, readApiError } from "../apiClient";
import { downloadBlob } from "../downloadBlob";
import { getLocale } from "../i18n-helpers";
import { parseContentDispositionFilename } from "../contentDisposition";

export interface DraftExportResult {
  /** Filename from Content-Disposition, or null when the header was unreadable. */
  filename: string | null;
}

/**
 * Export API endpoints.
 *
 * Handles project export functionality for downloading content in various formats.
 * Exports are downloaded directly to the user's browser.
 */
export const exportApi = {
  /**
   * Export all drafts from a project as a TXT file.
   *
   * Fetches the export from the backend and triggers a browser download.
   * The filename is extracted from the Content-Disposition header or uses
   * a localized default ("Export.txt" / "导出.txt").
   *
   * Handles authentication automatically with token refresh on 401 errors.
   *
   * @throws {ApiError} ERR_EXPORT_NO_DRAFTS if project has no drafts
   */
  exportDrafts: async (projectId: string): Promise<DraftExportResult> => {
    const response = await authenticatedFetch(
      `/api/v1/projects/${projectId}/export/drafts`,
      { method: "GET" },
    );

    if (!response.ok) {
      throw await readApiError(response);
    }

    // Extract filename from Content-Disposition header
    const disposition = response.headers.get("Content-Disposition");
    const locale = getLocale();
    const exportFilename = locale === "en" ? "Export" : "导出";
    // The server names the file `{项目名}_正文.txt`; the generic name is only
    // for a response without a readable Content-Disposition.
    const serverFilename = parseContentDispositionFilename(disposition);
    const filename = serverFilename ?? `${exportFilename}.txt`;

    // Trigger browser download
    downloadBlob(await response.blob(), filename);

    return {
      filename: serverFilename,
    };
  },
};
