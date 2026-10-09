/**
 * Natural polish API (single-round HTTP request).
 *
 * Replaces the previous streaming agent request for "去 AI 味" rewrites.
 *
 * Contract:
 * - POST /api/v1/editor/natural-polish
 * - Supports AbortSignal cancellation
 * - Returns `{ text, unchanged }`. `unchanged: true` means the server found nothing
 *   worth changing and did not count this call toward today's AI messages.
 *   Older servers omit `unchanged`; that is treated as false.
 */

import { api } from "./apiClient";

export interface NaturalPolishParams {
  projectId: string;
  fileId: string;
  fileType?: string;
  selectedText: string;
}

export interface NaturalPolishResult {
  text: string;
  unchanged: boolean;
}

type NaturalPolishResponse = {
  text: string;
  unchanged?: boolean;
};

function extractResult(payload: unknown): NaturalPolishResult {
  if (typeof payload === "string") return { text: payload, unchanged: false };
  if (!payload || typeof payload !== "object") {
    throw new Error("Invalid natural polish response");
  }
  const data = payload as Record<string, unknown>;
  if (typeof data.text === "string") {
    return { text: data.text, unchanged: data.unchanged === true };
  }
  throw new Error("Invalid natural polish response");
}

export const naturalPolishApi = {
  naturalPolish: async (
    params: NaturalPolishParams,
    opts?: { signal?: AbortSignal },
  ): Promise<NaturalPolishResult> => {
    const payload = {
      project_id: params.projectId,
      selected_text: params.selectedText,
      metadata: {
        current_file_id: params.fileId,
        current_file_type: params.fileType,
        source: "editor_natural_polish",
      },
    };

    const result = await api.post<NaturalPolishResponse | string>(
      "/api/v1/editor/natural-polish",
      payload,
      { signal: opts?.signal },
    );
    return extractResult(result);
  },
};
