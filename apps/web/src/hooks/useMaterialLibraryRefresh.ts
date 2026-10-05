import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MATERIAL_LIBRARY_SUMMARY_QUERY_KEY } from "./useMaterialLibrary";

const ACTIVE_STATUSES = new Set(["pending", "processing"]);
const FINISHED_STATUSES = new Set(["completed", "completed_with_errors"]);

interface MaterialStatusLike {
  id: string | number;
  status?: string | null;
}

/**
 * Invalidate the editor sidebar's library summary when a watched
 * decomposition finishes, so newly decomposed materials show up without a
 * page reload. Pass the polled materials (list page) or the single material
 * (detail page).
 */
export function useRefreshMaterialLibraryOnCompletion(
  materials: MaterialStatusLike[] | undefined,
): void {
  const queryClient = useQueryClient();
  const previousStatuses = useRef<Map<string, string | null | undefined>>(new Map());

  useEffect(() => {
    if (!materials) return;
    const previous = previousStatuses.current;
    const finished = materials.some(
      (material) =>
        ACTIVE_STATUSES.has(previous.get(String(material.id)) ?? "") &&
        FINISHED_STATUSES.has(material.status ?? ""),
    );
    previousStatuses.current = new Map(
      materials.map((material) => [String(material.id), material.status]),
    );
    if (finished) {
      void queryClient.invalidateQueries({ queryKey: MATERIAL_LIBRARY_SUMMARY_QUERY_KEY });
    }
  }, [materials, queryClient]);
}
