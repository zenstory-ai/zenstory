import { useState, useCallback } from "react";
import { exportApi, type DraftExportResult } from "../lib/api";

export interface UseExportResult {
  exportDrafts: () => Promise<DraftExportResult | undefined>;
  loading: boolean;
}

export const useExport = (projectId: string | null): UseExportResult => {
  const [loading, setLoading] = useState(false);

  const exportDrafts = useCallback(async () => {
    if (!projectId) return undefined;

    setLoading(true);
    try {
      return await exportApi.exportDrafts(projectId);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  return { exportDrafts, loading };
};

export default useExport;
