import { useState, useCallback } from "react";
import { exportApi, type DraftExportResult } from "../lib/api";

/**
 * Return type for the useExport hook
 */
export interface UseExportResult {
  /**
   * Async function to trigger draft export for the current project. Resolves
   * with what the server actually exported (undefined without a project).
   */
  exportDrafts: (options?: { includeOutline?: boolean }) => Promise<DraftExportResult | undefined>;
  /** Indicates if an export operation is currently in progress */
  loading: boolean;
  /** Error message if export failed, null otherwise. Auto-clears after 3 seconds */
  error: string | null;
}

/**
 * Hook for exporting project drafts to downloadable files.
 *
 * Provides a simple interface to trigger draft exports with built-in
 * loading state management and error handling. Errors are automatically
 * cleared after 3 seconds.
 *
 * @param projectId - The ID of the project to export, or null if no project is selected
 * @returns Object containing export function, loading state, and error state
 *
 * @example
 * ```tsx
 * function ExportButton({ projectId }: { projectId: string }) {
 *   const { exportDrafts, loading, error } = useExport(projectId);
 *
 *   return (
 *     <div>
 *       <button
 *         onClick={exportDrafts}
 *         disabled={loading || !projectId}
 *       >
 *         {loading ? 'Exporting...' : 'Export Drafts'}
 *       </button>
 *       {error && <div className="error">{error}</div>}
 *     </div>
 *   );
 * }
 * ```
 *
 * @example
 * ```tsx
 * // With error handling in parent component
 * const { exportDrafts, loading } = useExport(projectId);
 *
 * const handleExport = async () => {
 *   try {
 *     await exportDrafts();
 *     toast.success('Export completed!');
 *   } catch (err) {
 *     toast.error('Export failed');
 *   }
 * };
 * ```
 */
export const useExport = (projectId: string | null): UseExportResult => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const exportDrafts = useCallback(async (options?: { includeOutline?: boolean }) => {
    if (!projectId) return undefined;

    setLoading(true);
    setError(null);

    try {
      return await (options ? exportApi.exportDrafts(projectId, options) : exportApi.exportDrafts(projectId));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Export failed";
      setError(message);
      // Auto-clear error after 3 seconds
      setTimeout(() => setError(null), 3000);
      throw err; // Re-throw for callers to handle
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  return { exportDrafts, loading, error };
};

export default useExport;
