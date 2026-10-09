import { useContext, useEffect, useRef } from "react";
import { QueryClientContext } from "@tanstack/react-query";
import { subscriptionQueryKeys } from "../lib/subscriptionApi";

/**
 * When the author leaves mid-round, the server settles the interrupted round in the
 * background (and refunds it when nothing was written yet) after the chat is gone, usually
 * within a few hundred milliseconds. Re-read the quota shortly after so the dashboard /
 * project badges show the settled count without a full reload (most of the app's queries do
 * not refetch on mount). A late re-read covers a slow refund or a proxy that notices the
 * dropped connection late; the home page also re-reads the quota when it mounts
 * (useAiMessageQuota), and the badge's own 60 s poll is the last backstop.
 */
export const QUOTA_REFRESH_AFTER_LEAVE_DELAYS_MS = [700, 1800, 5000] as const;

export function useQuotaRefreshAfterLeave(isStreaming: boolean, projectId: string | null) {
  // Context (not useQueryClient) so the chat still renders without a provider in tests.
  const queryClient = useContext(QueryClientContext);
  const streamingRef = useRef(isStreaming);
  useEffect(() => {
    streamingRef.current = isStreaming;
  }, [isStreaming]);

  useEffect(() => {
    return () => {
      // Unmount or project switch while a round is still generating: the stream is dropped.
      if (!streamingRef.current || !queryClient) return;
      for (const delay of QUOTA_REFRESH_AFTER_LEAVE_DELAYS_MS) {
        setTimeout(() => {
          void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota(), refetchType: "all" });
          void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quotaLite(), refetchType: "all" });
        }, delay);
      }
    };
  }, [queryClient, projectId]);
}
