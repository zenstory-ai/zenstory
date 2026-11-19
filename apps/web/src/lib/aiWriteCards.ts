/**
 * Reading the AI write cards (edit_file, write_chapter) of a chat the same way in the card and
 * in the chat panel: their undo anchor, and which of them can only be undone after a later one.
 */
import type { FileEditUndoTarget, ToolCall } from '../types';
import { undoneEditKey } from './undoneEdits';

const UNDO_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/;

export const isValidUndoTimestamp = (value: unknown): value is string => {
  if (typeof value !== 'string') return false;
  const match = value.match(UNDO_TIMESTAMP);
  if (!match) return false;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    return false;
  }

  const isLeapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, isLeapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (day < 1 || day > daysInMonth[month - 1]) return false;

  const timezone = match[8];
  if (timezone !== 'Z') {
    const offsetHour = Number(timezone.slice(1, 3));
    const offsetMinute = Number(timezone.slice(4, 6));
    if (offsetHour > 23 || offsetMinute > 59) return false;
  }
  return true;
};

/**
 * Microseconds since the epoch of a valid undo token. The server advances a file's token by at
 * least one microsecond on every write, so `Date` (milliseconds) would tie two quick writes.
 */
const undoTimestampMicros = (value: string): number => {
  const match = value.match(UNDO_TIMESTAMP)!;
  const [, year, month, day, hour, minute, second, fraction = '', timezone] = match;
  let millis = Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second));
  if (timezone !== 'Z') {
    const sign = timezone.startsWith('-') ? -1 : 1;
    millis -= sign * (Number(timezone.slice(1, 3)) * 60 + Number(timezone.slice(4, 6))) * 60_000;
  }
  return millis * 1000 + Number(fraction.padEnd(6, '0'));
};

const SHA256_HEX = /^[0-9a-f]{64}$/;

/** The immutable undo anchor the server attached to an edit_file / write_chapter card, or null. */
export const readFileEditUndoTarget = (
  data: Record<string, unknown>,
  chatMessageId: string | undefined,
  toolUseId: string | undefined,
): FileEditUndoTarget | null => {
  const fileId = typeof data.id === 'string' ? data.id.trim() : '';
  const undo = data.undo && typeof data.undo === 'object' && !Array.isArray(data.undo)
    ? data.undo as Record<string, unknown>
    : null;
  const beforeVersionNumber = undo?.before_version_number;
  const expectedAfterUpdatedAt = undo?.expected_after_updated_at;
  if (
    !fileId
    || typeof beforeVersionNumber !== 'number'
    || !Number.isSafeInteger(beforeVersionNumber)
    || beforeVersionNumber <= 0
    || !isValidUndoTimestamp(expectedAfterUpdatedAt)
  ) {
    return null;
  }
  const contentSha256 = undo?.expected_after_content_sha256;
  return {
    fileId,
    beforeVersionNumber,
    expectedAfterUpdatedAt,
    ...(typeof contentSha256 === 'string' && SHA256_HEX.test(contentSha256)
      ? { expectedAfterContentSha256: contentSha256 }
      : {}),
    ...(chatMessageId && toolUseId ? { chatMessageId, toolUseId } : {}),
  };
};

/**
 * Undo is offered only once the card belongs to a saved message: without the message and tool call
 * ids the server cannot record the undo on the card, so it would come back as not undone after a reload.
 */
export const isPersistedUndoTarget = (target: FileEditUndoTarget | null): target is FileEditUndoTarget =>
  Boolean(target?.chatMessageId && target.toolUseId);

/** What a write_chapter result did to its chapter, from the server's `action` and saved-body fields. */
export type WriteChapterOutcome = 'created' | 'exists' | 'replacing' | 'replaced' | 'unchanged' | 'not_saved';

/**
 * `action` 'created' / 'exists' / 'replaced' (StreamAdapter re-sends a replacement's card with
 * `body_saved` and `mutation_applied` once its text is saved or refused). Unknown actions return null.
 */
export const readWriteChapterOutcome = (data: Record<string, unknown>): WriteChapterOutcome | null => {
  if (data.action === 'created') return 'created';
  if (data.action === 'exists') return 'exists';
  if (data.action !== 'replaced') return null;
  if (data.body_saved === false) return 'not_saved';
  if (data.body_saved !== true) return 'replacing';
  return data.mutation_applied === true ? 'replaced' : 'unchanged';
};

/** The tool result payload a card renders (`result.data`, or the result itself). */
const cardData = (result: Record<string, unknown> | undefined): Record<string, unknown> | null => {
  const data = result?.data ?? result;
  return data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : null;
};

/** The undo anchor of a successful AI write card that offers Undo, or null. */
export const readAiWriteUndoTarget = (call: ToolCall): FileEditUndoTarget | null => {
  if (call.status !== 'success') return null;
  const data = cardData(call.result);
  if (!data) return null;
  if (call.tool_name === 'edit_file') return readFileEditUndoTarget(data, undefined, undefined);
  if (call.tool_name === 'write_chapter' && readWriteChapterOutcome(data) === 'replaced') {
    return readFileEditUndoTarget(data, undefined, undefined);
  }
  return null;
};

/** The parts of a chat message that can carry tool cards. */
export interface ToolCardSource {
  toolCalls?: ToolCall[];
  toolResults?: ToolCall[];
  displayItems?: Array<{ type: string; toolCalls?: ToolCall[] }>;
}

/** One AI write card that offers Undo. */
export interface UndoCardRef {
  key: string;
  fileId: string;
  expectedAfterUpdatedAt: string;
  undone: boolean;
}

/**
 * Every AI write card with an undo anchor in these messages (and the live round's items).
 * A card counts as undone when the server recorded it (`undo.undone`) or the author undid it here.
 */
export const collectUndoCardRefs = (
  sources: ReadonlyArray<ToolCardSource>,
  undoneEdits: ReadonlySet<string>,
): UndoCardRef[] => {
  const refs = new Map<string, UndoCardRef>();
  const visit = (call: ToolCall) => {
    const target = readAiWriteUndoTarget(call);
    if (!target) return;
    const key = undoneEditKey(target);
    const recorded = (cardData(call.result)?.undo as Record<string, unknown> | undefined)?.undone === true;
    const undone = recorded || undoneEdits.has(key) || refs.get(key)?.undone === true;
    refs.set(key, { key, fileId: target.fileId, expectedAfterUpdatedAt: target.expectedAfterUpdatedAt, undone });
  };
  for (const source of sources) {
    source.toolCalls?.forEach(visit);
    source.toolResults?.forEach(visit);
    for (const item of source.displayItems ?? []) {
      if (item.type === 'tool_calls') item.toolCalls?.forEach(visit);
    }
  }
  return [...refs.values()];
};

/**
 * Keys of AI write cards whose file has a later AI write card (by post-edit token) that is not
 * undone. Undoing such a card first would restore the file over the later change, so the server
 * refuses it as stale; it becomes possible once every later change to that file is undone.
 * A later write without an undo anchor is not listed, so it never asks for an impossible undo.
 */
export const undoBlockedByLaterWrite = (cards: ReadonlyArray<UndoCardRef>): ReadonlySet<string> => {
  const latestLiveByFile = new Map<string, number>();
  for (const card of cards) {
    if (card.undone) continue;
    const at = undoTimestampMicros(card.expectedAfterUpdatedAt);
    const previous = latestLiveByFile.get(card.fileId);
    if (previous === undefined || at > previous) latestLiveByFile.set(card.fileId, at);
  }
  const blocked = new Set<string>();
  for (const card of cards) {
    if (card.undone) continue;
    const latest = latestLiveByFile.get(card.fileId);
    if (latest !== undefined && latest > undoTimestampMicros(card.expectedAfterUpdatedAt)) blocked.add(card.key);
  }
  return blocked;
};
