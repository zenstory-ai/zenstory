import { countWords } from './documentChunker';

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
 * `write_chapter` counts only on the re-sent replacement card that saved new text
 * (`mutation_applied: true`), never when it only found or created the chapter;
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

  if (toolName === 'write_chapter') return payload?.mutation_applied === true;

  if (toolName === 'create_file') {
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
  return false;
}

/**
 * Refusals the server's write-order, same-turn rewrite, wall-clock wrap-up, or micro-edit
 * convergence guard returns before mutating anything: a created file has to get its text first
 * (`file_body_pending`, `pending_empty_file_unwritten`), a chapter whose full body was already
 * saved in this turn cannot be rewritten again via `write_chapter` during self-check
 * (`chapter_written_this_turn`), a tool call emitted after wall-clock wrap-up reminder is blocked
 * (`wall_clock_wrap_up`), or a serial micro-polish loop reached its cap (`edit_repeat_limit`).
 * They steer the model without indicating any failure of the author's work, whereas
 * `chapter_replace_pending` can also refuse an unexecuted edit/delete before a replacement is
 * saved and therefore stays visible.
 */
const INTERNAL_ORDERING_ERROR_TYPES = new Set([
  'file_body_pending',
  'pending_empty_file_unwritten',
  'chapter_written_this_turn',
  'wall_clock_wrap_up',
  'edit_repeat_limit',
]);

export function isInternalOrderingRefusal(result: Record<string, unknown> | undefined): boolean {
  const nested = result?.data && typeof result.data === 'object' ? (result.data as Record<string, unknown>) : undefined;
  const errorType = result?.error_type ?? nested?.error_type;
  return typeof errorType === 'string' && INTERNAL_ORDERING_ERROR_TYPES.has(errorType);
}

/** The file whose text is being written right now, for the chat's waiting line. */
export interface BodyWriteSnapshot {
  title: string;
  /** Editor word count of the text streamed so far (0 until it starts). */
  words: number;
}

/**
 * Follows the round's existing events to say which file is getting its text: a `create_file` or
 * `write_chapter` that left a file waiting (`awaiting_body`) waits for its `<file>` text; `file_content` chunks are counted
 * the way the editor counts words. An end without any text (the run stopped first, an empty-file
 * correction follows) keeps the wait, so the rerun shows the same stage.
 */
export interface BodyWriteProgress {
  toolCall(toolName: string, args?: Record<string, unknown>): void;
  toolResult(toolName: string, status: string, result?: Record<string, unknown>): void;
  fileCreated(fileId: string, title: string): void;
  chunk(fileId: string, chunk: string): void;
  end(fileId: string): void;
  snapshot(): BodyWriteSnapshot | null;
  reset(): void;
}

export function createBodyWriteProgress(): BodyWriteProgress {
  const titles = new Map<string, string>();
  let current: { fileId: string; title: string; text: string } | null = null;
  // delete_file returns only `true`, so the deleted id comes from the call's arguments.
  const pendingDeletes = new Set<string>();
  return {
    toolCall(toolName, args) {
      if (toolName === 'delete_file' && typeof args?.id === 'string' && args.id) pendingDeletes.add(args.id);
    },
    toolResult(toolName, status, result) {
      if (toolName === 'delete_file') {
        // Deleting the waiting file is the allowed way out of an overdue empty file: stop naming it.
        if (status === 'success' && current && pendingDeletes.has(current.fileId)) current = null;
        if (status === 'success' || pendingDeletes.size <= 1) pendingDeletes.clear();
        return;
      }
      if ((toolName !== 'create_file' && toolName !== 'write_chapter') || status !== 'success') return;
      const payload = (result?.data ?? result) as Record<string, unknown> | undefined;
      const id = typeof payload?.id === 'string' ? payload.id : '';
      const title = typeof payload?.title === 'string' ? payload.title : '';
      if (!id || payload?.file_type === 'folder') return;
      if (title) titles.set(id, title);
      if (payload?.awaiting_body === true) current = { fileId: id, title, text: '' };
    },
    fileCreated(fileId, title) {
      if (fileId && title) titles.set(fileId, title);
    },
    chunk(fileId, chunk) {
      if (!fileId) return;
      if (current?.fileId !== fileId) {
        if (!chunk.trim()) return;
        current = { fileId, title: titles.get(fileId) ?? '', text: '' };
      }
      current.text += chunk;
    },
    end(fileId) {
      if (current?.fileId === fileId && current.text.trim()) current = null;
    },
    snapshot() {
      if (!current || !current.title) return null;
      return { title: current.title, words: countWords(current.text) };
    },
    reset() {
      titles.clear();
      pendingDeletes.clear();
      current = null;
    },
  };
}

/**
 * Normalizes a live status line (e.g. tool-argument preparation progress emitted
 * as a `thinking` SSE event) into a clean single-line string for display.
 * Returns null when empty or whitespace-only so callers fall back cleanly.
 */
export function normalizeStreamStatusText(status: string | null | undefined): string | null {
  if (typeof status !== 'string') return null;
  const normalized = status.replace(/\s+/g, ' ').trim();
  return normalized.length > 0 ? normalized : null;
}

/**
 * Extracts the active live status text from the tail of a round's stream items.
 * Only returns a status when the latest non-empty item is a `thinking_status`
 * (so completed statuses are never re-shown once tools or prose follow them).
 */
export function getActiveStreamStatusText(
  items: ReadonlyArray<{ type: string; content?: string; transient?: boolean }> | null | undefined,
): string | null {
  if (!items || items.length === 0) return null;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item.type === 'thinking_status') {
      const normalized = normalizeStreamStatusText(item.content);
      if (normalized) return normalized;
      continue;
    }
    if (item.type === 'content' || item.type === 'thinking_content') {
      if (typeof item.content === 'string' && item.content.trim().length > 0) return null;
      continue;
    }
    return null;
  }
  return null;
}
