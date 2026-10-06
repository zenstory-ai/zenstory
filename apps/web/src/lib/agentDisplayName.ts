type Translate = (key: string, options?: Record<string, unknown>) => string;

const AGENT_DISPLAY_KEYS: Record<string, string> = {
  planner: 'chat:workflow.agents.planner',
  hook_designer: 'chat:workflow.agents.hook_designer',
  writer: 'chat:workflow.agents.writer',
  quality_reviewer: 'chat:workflow.agents.quality_reviewer',
};

/**
 * Map an internal agent id (planner, writer, ...) to the author-facing role name.
 * Returns null for unknown ids so callers can hide the label instead of leaking the id.
 */
export function getAgentDisplayName(agentId: string | null | undefined, t: Translate): string | null {
  if (!agentId) return null;
  const key = AGENT_DISPLAY_KEYS[agentId.trim().toLowerCase()];
  return key ? t(key) : null;
}

/** Author-facing text for a handoff event, or null when the target role is unknown. */
export function formatHandoffMessage(
  data: { target_agent?: string | null; reason?: string | null },
  t: Translate,
): string | null {
  const agent = getAgentDisplayName(data.target_agent, t);
  if (!agent) return null;
  const reason = data.reason?.trim();
  return reason
    ? t('chat:workflow.handoffMessage', { agent, reason })
    : t('chat:workflow.handoffMessageShort', { agent });
}
