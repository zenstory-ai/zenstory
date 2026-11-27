/**
 * Pure parser converting an SSE `done` frame payload into StreamCompletionMeta.
 *
 * @module hooks/streaming/completionMeta
 */

import { parseWrittenFiles } from "../../lib/chatRoundEnd";
import type { StreamDoneData } from "../../lib/agentApi";
import type { StreamCompletionMeta } from "./types";

export function buildCompletionMeta(data: StreamDoneData): StreamCompletionMeta {
  const completionMeta: StreamCompletionMeta = {
    assistantMessageId:
      typeof data.assistant_message_id === "string" && data.assistant_message_id.trim()
        ? data.assistant_message_id
        : undefined,
    sessionId:
      typeof data.session_id === "string" && data.session_id.trim()
        ? data.session_id
        : undefined,
    confirmedFileMutation:
      typeof data.file_mutated === "boolean" ? data.file_mutated : undefined,
  };

  if (data.stop_reason === "user_stopped") {
    completionMeta.stoppedByAuthor = true;
    if (typeof data.charged === "boolean") {
      completionMeta.charged = data.charged;
    }
    if (typeof data.produced_output === "boolean") {
      completionMeta.producedOutput = data.produced_output;
    }
    const writtenFiles = parseWrittenFiles(data.written_files);
    if (writtenFiles.length > 0) {
      completionMeta.writtenFiles = writtenFiles;
    }
    const unwrittenFiles = parseWrittenFiles(data.unwritten_files);
    if (unwrittenFiles.length > 0) {
      completionMeta.unwrittenFiles = unwrittenFiles;
    }
    return completionMeta;
  }

  const unwrittenFiles = parseWrittenFiles(data.unwritten_files);
  const writtenFiles = parseWrittenFiles(data.written_files);
  const savedOutput = typeof data.saved_output === "boolean"
    ? data.saved_output
    : writtenFiles.length > 0;

  if (data.delivery_incomplete === true || unwrittenFiles.length > 0) {
    completionMeta.deliveryOutcome = {
      reason: "incomplete",
      charged: true,
      savedOutput,
      removedFiles: [],
      writtenFiles,
      ...(data.no_file_changes === true ? { noFileChanges: true } : {}),
      ...(data.unexecuted_tool_text === true ? { unexecutedToolText: true } : {}),
      ...(unwrittenFiles.length > 0 ? { unwrittenFiles } : {}),
    };
  } else if (writtenFiles.length > 0 || data.no_file_changes === true) {
    // The write receipt of a normally completed round (#267): only what the
    // server confirmed, never what the reply text says it did.
    completionMeta.deliveryOutcome = {
      reason: "completed",
      charged: true,
      savedOutput,
      removedFiles: [],
      writtenFiles,
      ...(data.no_file_changes === true ? { noFileChanges: true } : {}),
    };
  }

  return completionMeta;
}
