import { useCallback, useState } from "react";
import type { MessageSegment } from "./useAgentStream";

/**
 * What the agent is doing right now, in author terms. Shown under the chat while
 * a round runs so the first seconds before any text do not look stuck.
 */
export type StreamActivityKind =
  | "understanding"
  | "planning"
  | "writing"
  | "reviewing"
  | "reading"
  | "creatingFile"
  | "editingFile"
  | "replying";

export interface StreamActivity {
  kind: StreamActivityKind;
  /** File title for creatingFile / editingFile, when the stream told us. */
  title?: string;
}

const AGENT_ACTIVITY: Record<string, StreamActivityKind> = {
  planner: "planning",
  hook_designer: "planning",
  writer: "writing",
  quality_reviewer: "reviewing",
};

const READ_TOOLS = new Set(["query_files", "hybrid_search", "parallel_execute", "load_skill", "read_skill_resource"]);
const EDIT_TOOLS = new Set(["edit_file", "update_file", "delete_file"]);

function sameActivity(a: StreamActivity | null, b: StreamActivity): boolean {
  return a !== null && a.kind === b.kind && a.title === b.title;
}

/** Track the current activity and when the round started; callers forward stream callbacks. */
export function useStreamActivity() {
  const [activity, setActivity] = useState<StreamActivity | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const update = useCallback((next: StreamActivity) => {
    setActivity((prev) => (sameActivity(prev, next) ? prev : next));
  }, []);

  const begin = useCallback(() => {
    setStartedAt(Date.now());
    setActivity({ kind: "understanding" });
  }, []);

  const onAgentSelected = useCallback((agentType: string) => {
    const kind = AGENT_ACTIVITY[agentType];
    if (kind) update({ kind });
  }, [update]);

  const onToolCall = useCallback((toolName: string, args: Record<string, unknown>) => {
    if (toolName === "create_file") {
      const title = typeof args.title === "string" && args.title.trim() ? args.title.trim() : undefined;
      update({ kind: "creatingFile", title });
    } else if (EDIT_TOOLS.has(toolName)) {
      update({ kind: "editingFile" });
    } else if (READ_TOOLS.has(toolName)) {
      update({ kind: "reading" });
    }
  }, [update]);

  const onFileCreated = useCallback((title: string) => {
    update({ kind: "creatingFile", title: title || undefined });
  }, [update]);

  const onFileEditStart = useCallback((title: string) => {
    update({ kind: "editingFile", title: title || undefined });
  }, [update]);

  const onSegmentStart = useCallback((segment: MessageSegment) => {
    if (segment.type === "content") update({ kind: "replying" });
  }, [update]);

  return {
    activity,
    startedAt,
    begin,
    onAgentSelected,
    onToolCall,
    onFileCreated,
    onFileEditStart,
    onSegmentStart,
  };
}
