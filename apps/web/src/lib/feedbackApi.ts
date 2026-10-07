import { ApiError, getAccessToken, getApiBase, resolveOwnedAuthSession, tryRefreshToken } from "./apiClient";
import { resolveApiErrorMessage } from "./errorHandler";

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
    if (response.status === 401 && entryAccess && entryRefresh) {
      let ownedSession = resolveOwnedAuthSession(entryAccess, entryRefresh);
      if (ownedSession?.accessToken && ownedSession.refreshToken) {
        if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
          const refreshed = await tryRefreshToken();
          ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
        }
        if (ownedSession?.accessToken && ownedSession.refreshToken) {
          response = await fetchOnce(ownedSession.accessToken);
        }
      }
    }

    if (!response.ok) {
      let errorMessage = "ERR_INTERNAL_SERVER_ERROR";
      try {
        const errorData = await response.json();
        errorMessage = resolveApiErrorMessage(errorData, errorMessage);
      } catch {
        // ignore parse failure and keep fallback message
      }
      throw new ApiError(response.status, errorMessage);
    }

    return response.json();
  },
};
