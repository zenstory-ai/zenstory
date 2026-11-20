import { authenticatedFetch, readApiError } from "./apiClient";

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

    const response = await authenticatedFetch(
      "/api/v1/feedback",
      {
        method: "POST",
        body: formData,
      },
      {
        includeLanguage: true,
        clearOnUnrecoverable401: true,
      },
    );

    if (!response.ok) {
      throw await readApiError(response);
    }

    return response.json();
  },
};
