/**
 * Chat session API client.
 *
 * Provides functions for managing chat sessions and messages with the backend.
 * This module handles:
 * - Recent message loading
 * - New session creation
 * - Assistant message feedback submission
 *
 * Note: This file intentionally uses the shared apiClient to:
 * - Automatically attach Authorization header
 * - Refresh token on 401 and retry once
 */

import { api } from "./apiClient";
import { getLocale } from "./i18n-helpers";

/**
 * Represents a single message in a chat session.
 *
 * Messages can have different roles (user, assistant, system, tool) and may
 * contain tool execution data when the AI calls backend tools.
 */
export interface ChatMessage {
  /** Unique identifier for the message */
  id: string;
  /** ID of the chat session this message belongs to */
  session_id: string;
  /** Role of the message sender */
  role: "user" | "assistant" | "system" | "tool";
  /** Text content of the message */
  content: string;
  /** Serialized tool calls data (JSON string) if AI invoked tools */
  tool_calls?: string | null;
  /** ID of the tool call this message is responding to (for tool role) */
  tool_call_id?: string | null;
  /** Additional metadata as JSON string */
  metadata?: string | null;
  /** UTC timestamp when the message was created */
  created_at: string;
}

/** Feedback vote direction for assistant messages. */
export type MessageFeedbackVote = "up" | "down";

/** Request payload for submitting message feedback. */
export interface MessageFeedbackPayload {
  /** Required vote value */
  vote: MessageFeedbackVote;
  /** Optional preset reason key */
  preset?: string;
  /** Optional free-form comment */
  comment?: string;
}

/** Persisted feedback content stored with a chat message. */
export interface MessageFeedbackData {
  vote: MessageFeedbackVote;
  preset?: string | null;
  comment?: string | null;
  updated_at?: string | null;
}

/** Response returned by message feedback API. */
export interface MessageFeedbackResponse {
  message_id: string;
  feedback: MessageFeedbackData;
  updated_at: string;
}

/**
 * Represents a chat session associated with a project.
 *
 * Each project has one active chat session at a time. Creating a new session
 * deactivates the previous one.
 */
export interface ChatSession {
  /** Unique identifier for the session */
  id: string;
  /** ID of the user who owns this session */
  user_id: string;
  /** ID of the project this session belongs to */
  project_id: string;
  /** Display title for the chat session */
  title: string;
  /** Whether this is the currently active session for the project */
  is_active: boolean;
  /** Total number of messages in this session */
  message_count: number;
  /** UTC timestamp when the session was created */
  created_at: string;
  /** UTC timestamp when the session was last updated */
  updated_at: string;
}

/**
 * Get recent messages for a project's chat session.
 *
 * Retrieves the most recent messages, useful for loading chat history
 * on component mount. Supports cancellation via AbortSignal.
 *
 * @param projectId - UUID of the project to get messages for
 * @param limit - Maximum number of recent messages to retrieve (default: 20)
 * @param signal - Optional AbortSignal for request cancellation
 * @param messageId - Optional backend message ID to retrieve from the active session
 * @returns Promise resolving to array of recent chat messages
 */
export async function getRecentMessages(
  projectId: string,
  limit: number = 20,
  signal?: AbortSignal,
  messageId?: string,
): Promise<ChatMessage[]> {
  const params = new URLSearchParams({
    limit: limit.toString(),
  });
  if (messageId) {
    params.set("message_id", messageId);
  }
  return api.get<ChatMessage[]>(`/api/v1/chat/session/${projectId}/recent?${params}`, signal ? { signal } : undefined);
}

/**
 * Create a new chat session for a project.
 *
 * Creates a fresh session and deactivates the current one. The new session
 * will have a default title ("New Chat" or "新对话") if none is provided,
 * localized based on the current language setting.
 *
 * @param projectId - UUID of the project to create session for
 * @param title - Optional title for the new session
 * @returns Promise resolving to the newly created chat session
 */
export async function createNewSession(
  projectId: string,
  title?: string,
): Promise<ChatSession> {
  const locale = getLocale();
  const defaultTitle = locale === 'en' ? 'New Chat' : '新对话';
  const finalTitle = title || defaultTitle;
  const params = new URLSearchParams({ title: finalTitle });
  return api.post<ChatSession>(`/api/v1/chat/session/${projectId}/new?${params}`);
}

/**
 * Submit thumbs-up / thumbs-down feedback for a chat assistant message.
 *
 * @param messageId - Backend chat message ID
 * @param payload - Feedback payload
 * @returns Promise resolving to updated feedback result
 */
export async function submitMessageFeedback(
  messageId: string,
  payload: MessageFeedbackPayload,
): Promise<MessageFeedbackResponse> {
  return api.post<MessageFeedbackResponse>(`/api/v1/chat/messages/${messageId}/feedback`, payload);
}
