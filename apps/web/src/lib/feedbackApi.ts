import { apiErrorFromPayload, clearAuthStorage, getAccessToken, getApiBase, resolveOwnedAuthSession, tryRefreshToken } from "./apiClient";

export type FeedbackSourcePage = "dashboard" | "editor";

export interface SubmitFeedbackPayload {
  issueText: string;
  sourcePage: FeedbackSourcePage;
  sourceRoute?: string;
  screenshot?: globalThis.File | null;
  debugContext?: {
    trace_id?: string;
    request_id?: string;
    agent_run_id?: string;
    project_id?: string;
    agent_session_id?: string | null;
  } | null;
}

export interface FeedbackSubmitResponse {
  id: string;
  message: string;
  created_at: string;
}

export const feedbackApi = {
  submit: async (payload: SubmitFeedbackPayload): Promise<FeedbackSubmitResponse> => {
    const entryAccess = getAccessToken();
    const entryRefresh = localStorage.getItem("refresh_token");
    const formData = new FormData();
    formData.append("issue_text", payload.issueText);
    formData.append("source_page", payload.sourcePage);
    if (payload.sourceRoute) {
      formData.append("source_route", payload.sourceRoute);
    }
    if (payload.debugContext) {
      const ctx = payload.debugContext;
      if (ctx.trace_id) formData.append("trace_id", ctx.trace_id);
      if (ctx.request_id) formData.append("request_id", ctx.request_id);
      if (ctx.agent_run_id) formData.append("agent_run_id", ctx.agent_run_id);
      if (ctx.project_id) formData.append("project_id", ctx.project_id);
      if (ctx.agent_session_id) formData.append("agent_session_id", ctx.agent_session_id);
    }
    if (payload.screenshot) {
      formData.append("screenshot", payload.screenshot);
    }

    const fetchOnce = async (accessToken: string | null): Promise<Response> => {
      const language = localStorage.getItem("zenstory-language") || "zh";
      return fetch(`${getApiBase()}/api/v1/feedback`, {
        method: "POST",
        headers: {
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
          "Accept-Language": language,
        },
        body: formData,
      });
    };

    let response = await fetchOnce(entryAccess);
    if (response.status === 401) {
      const currentAccessToken = localStorage.getItem("access_token");
      const currentRefreshToken = localStorage.getItem("refresh_token");
      const stillOwnsRequestSession =
        currentAccessToken === entryAccess &&
        currentRefreshToken === entryRefresh;
      const belongsToRotatedSession =
        resolveOwnedAuthSession(entryAccess, entryRefresh) !== null;

      if (stillOwnsRequestSession || belongsToRotatedSession) {
        if (!entryAccess || !entryRefresh) {
          if (stillOwnsRequestSession) {
            clearAuthStorage("missing_token");
          }
        } else {
          let ownedSession = resolveOwnedAuthSession(entryAccess, entryRefresh);
          if (ownedSession?.accessToken && ownedSession.refreshToken) {
            if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
              const refreshed = await tryRefreshToken();
              ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
            }
            if (ownedSession?.accessToken && ownedSession.refreshToken) {
              const { accessToken: retryAccess, refreshToken: retryRefresh } = ownedSession;
              response = await fetchOnce(retryAccess);
              if (
                response.status === 401 &&
                localStorage.getItem("access_token") === retryAccess &&
                localStorage.getItem("refresh_token") === retryRefresh
              ) {
                clearAuthStorage("retry_still_unauthorized");
              }
            }
          }
        }
      }
    }

    if (!response.ok) {
      let errorData: unknown = null;
      try {
        errorData = await response.json();
      } catch {
        // Non-JSON gateway or proxy error body; fall back to status-based ApiError
      }
      throw apiErrorFromPayload(response.status, errorData);
    }

    return response.json();
  },
};
