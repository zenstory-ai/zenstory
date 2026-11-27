import type {
  AgentApiKey,
  AgentApiKeyListResponse,
  CreateAgentApiKeyRequest,
  CreateAgentApiKeyResponse,
  RegenerateAgentApiKeyResponse,
} from "../../types";
import { api } from "../apiClient";

/** Agent API Keys APIs. */
export const agentApiKeysApi = {
  /** List all API keys for the current user. */
  list: async () => api.get<AgentApiKeyListResponse>("/api/v1/agent-api-keys"),

  /** Create a new API key. */
  create: async (data: CreateAgentApiKeyRequest) =>
    api.post<CreateAgentApiKeyResponse>("/api/v1/agent-api-keys", data),

  /** Update an existing API key. */
  update: async (
    id: string,
    data: {
      name?: string;
      description?: string;
      scopes?: string[];
      project_ids?: string[];
      is_active?: boolean;
    },
  ) => api.put<AgentApiKey>(`/api/v1/agent-api-keys/${id}`, data),

  /** Delete (revoke) an API key. */
  delete: async (id: string) =>
    api.delete<{ success: boolean; message: string }>(`/api/v1/agent-api-keys/${id}`),

  /** Regenerate an API key. */
  regenerate: async (id: string) =>
    api.post<RegenerateAgentApiKeyResponse>(`/api/v1/agent-api-keys/${id}/regenerate`),
};
