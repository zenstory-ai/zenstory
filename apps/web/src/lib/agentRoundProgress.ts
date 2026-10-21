const PARALLEL_WRITE_TASKS = new Set(['write_chapter', 'edit_file', 'delete_file']);
/** update_project fields an author can see; a task-board or current_phase update alone is not output. */
const PROJECT_INFO_FIELDS = new Set(['summary', 'writing_style', 'notes']);

/**
 * A reply segment (text between two tool calls) counts as real prose once it reaches this
 * many units (see proseUnits). A one-line narration before a tool call ("我先看一遍全书大纲。",
 * "Let me check the outline first.") stays below it. Same value as the server's
 * `stream_billing.PROSE_MIN_CHARS`, which decides whether a stopped / interrupted round is charged.
 */
export const PROSE_MIN_CHARS = 60;
/** Units per Latin-letter word, as in the server's `stream_billing.LATIN_WORD_UNITS`. */
const LATIN_WORD_UNITS = 1.5;
const LATIN_WORD = /[A-Za-zÀ-ÖØ-öø-ÿ]+/g;

/**
 * Prose length the way the server counts it (`stream_billing.prose_units`): each Latin word is
 * LATIN_WORD_UNITS, every other non-whitespace character (Han, digit, punctuation) is 1.
 */
export function proseUnits(text: string): number {
  let visible = 0;
  for (const ch of text) {
    if (!/\s/.test(ch)) visible += 1;
  }
  const words = text.match(LATIN_WORD) ?? [];
  const letters = words.reduce((sum, word) => sum + word.length, 0);
  return visible - letters + LATIN_WORD_UNITS * words.length;
}

/** Whether one streamed reply segment is real prose rather than a transitional line. */
export function isRealProse(segmentContent: string): boolean {
  return proseUnits(segmentContent) >= PROSE_MIN_CHARS;
}

/**
 * Counts reply text the way the server does (`StreamBillingTracker._prose_reached`): only
 * the text since the last segment boundary counts toward PROSE_MIN_CHARS. A boundary is a
 * tool call / result, a handoff, an agent switch, a created file or a parallel batch start
 * (the server's `_SEGMENT_BOUNDARY_EVENTS`); the stream may keep showing the text before
 * and after it as one bubble segment, so the counter keeps its own offset.
 */
export interface ProseCounter {
  /** A streamed segment's full content so far; true once the text since the last boundary is real prose. */
  update(segmentId: string, segmentContent: string): boolean;
  /** A segment boundary: text streamed after this counts from zero. */
  boundary(): void;
  /** A new round. */
  reset(): void;
}

export function createProseCounter(): ProseCounter {
  let segmentId: string | null = null;
  let seen = 0;
  let offset = 0;
  return {
    update(id, segmentContent) {
      if (id !== segmentId) {
        segmentId = id;
        offset = 0;
      }
      seen = proseUnits(segmentContent);
      return seen - offset >= PROSE_MIN_CHARS;
    },
    boundary() {
      offset = seen;
    },
    reset() {
      segmentId = null;
      seen = 0;
      offset = 0;
    },
  };
}

/**
 * Whether a tool result means this round left written text in a file (or deleted one),
 * so a stop note may say "已写入的内容已保存" and the round is charged when stopped.
 *
 * Mirrors the server (`StreamBillingTracker._write_effect`): an empty `create_file` (the
 * chapter body streams in afterwards) or an edit that changed nothing does not count;
 * `parallel_execute` counts only when one of its completed sub-tasks is a write;
 * `update_project` counts only when it renamed the work or changed its synopsis, style or
 * notes. Body text streamed into a file arrives as file_content, which callers count
 * separately.
 */
export function isWriteToolResult(
  toolName: string,
  status: string,
  result?: Record<string, unknown>,
): boolean {
  if (status !== 'success') return false;
  const payload = (result?.data ?? result) as Record<string, unknown> | undefined;

  if (toolName === 'create_file' || toolName === 'update_file') {
    const content = typeof payload?.content === 'string' ? payload.content : '';
    return content.trim().length > 0 && payload?.reused_existing !== true;
  }
  if (toolName === 'edit_file') {
    if (typeof payload?.mutation_applied === 'boolean') return payload.mutation_applied;
    if (typeof payload?.edits_applied === 'number') return payload.edits_applied > 0;
    return true;
  }
  if (toolName === 'delete_file') return true;
  if (toolName === 'rename_file') return payload?.mutation_applied === true;
  if (toolName === 'update_project') {
    const fields = Array.isArray(payload?.updated_fields) ? (payload.updated_fields as unknown[]) : [];
    return (
      payload?.project_name_updated === true ||
      fields.some((field) => typeof field === 'string' && PROJECT_INFO_FIELDS.has(field))
    );
  }
  if (toolName !== 'parallel_execute') return false;

  const tasks = Array.isArray(payload?.tasks) ? (payload.tasks as Array<Record<string, unknown>>) : [];
  return tasks.some(
    (task) => task?.status === 'completed' && typeof task.type === 'string' && PARALLEL_WRITE_TASKS.has(task.type),
  );
}
