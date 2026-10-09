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

const UUID_PATTERN = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;
// `id=…`, `file_id: …`, `project_id=…` — internal references, never useful to an author.
const ID_FIELD_PATTERN = /\b(?:[a-z]+_)*id\s*[=:：]\s*[\w-]+/gi;
// Length fields the model copies from tool results: `word_count=2434`, `content_length: 512`.
const LENGTH_FIELD_PATTERN = /\b(?:word_count|content_length|char_count)\s*[=:：]\s*(\d+)/gi;
// Bare tool names in brackets such as `[query_files]`.
const BRACKETED_TOOL_PATTERN = /[[【]\s*[a-z][a-z0-9]*(?:_[a-z0-9]+)+\s*[\]】]/g;
const REMOVED = '\uE000'; // private-use placeholder for a removed span
const CJK_PUNCTUATION = /[\u3000-\u303f\uff00-\uffef]/;
const CJK_IDEOGRAPH = /[\u4e00-\u9fff]/;

/**
 * Strip internal identifiers and raw tool fields from agent-written text
 * (handoff reasons, task descriptions) before it is shown to the author.
 * Lengths stay, rewritten in the author-facing unit.
 */
export function sanitizeAgentText(text: string | null | undefined, t: Translate): string {
  if (!text) return '';
  const marked = text
    .replace(LENGTH_FIELD_PATTERN, (_match, count: string) =>
      t('chat:workflow.charCount', { count: Number(count) }))
    .replace(ID_FIELD_PATTERN, REMOVED)
    .replace(UUID_PATTERN, REMOVED)
    .replace(BRACKETED_TOOL_PATTERN, REMOVED);
  if (!marked.includes(REMOVED)) return marked.trim();

  const joined = joinRemovals(marked)
    // Separators left at the edge of a bracket: `（，当前…）` → `（当前…）`.
    .replace(/([（(])\s*[，,、;；]\s*/g, '$1')
    .replace(/\s*[，,、;；]\s*([）)])/g, '$1')
    // Separators doubled by a removal: `全文，，512 字` → `全文，512 字`.
    .replace(/([，,、;；])\s*[，,、;；]/g, '$1')
    // Brackets emptied by the removals become removals themselves.
    .replace(/[（(]\s*[）)]/g, REMOVED);
  return joinRemovals(joined)
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/^[\s，,、;；:：]+/, '')
    .trim();
}

/**
 * Join the text around removed spans: nothing next to Chinese punctuation or
 * between two Chinese characters, otherwise one space where the original had
 * one (so `第2章 2434 字` and Latin words stay separated).
 */
function joinRemovals(text: string): string {
  return text.replace(/[ \t]*\uE000+[ \t]*/g, (match: string, offset: number, whole: string) => {
    const before = whole.slice(0, offset).slice(-1);
    const after = whole.slice(offset + match.length).slice(0, 1);
    if (!before || !after) return '';
    if (CJK_PUNCTUATION.test(before) || CJK_PUNCTUATION.test(after)) return '';
    if (CJK_IDEOGRAPH.test(before) && CJK_IDEOGRAPH.test(after)) return '';
    return /[ \t]/.test(match) ? ' ' : '';
  });
}

/** Author-facing text for a handoff event, or null when the target role is unknown. */
export function formatHandoffMessage(
  data: { target_agent?: string | null; reason?: string | null },
  t: Translate,
): string | null {
  const agent = getAgentDisplayName(data.target_agent, t);
  if (!agent) return null;
  const reason = sanitizeAgentText(data.reason, t);
  return reason
    ? t('chat:workflow.handoffMessage', { agent, reason })
    : t('chat:workflow.handoffMessageShort', { agent });
}
