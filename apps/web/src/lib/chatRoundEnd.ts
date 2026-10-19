import type { TFunction } from "i18next";
import type { StreamRenderItem } from "../hooks/useChatStreaming";

/** Progress lines that only mean "working on it"; they say nothing once the round is over. */
const TRANSIENT_ITEM_TYPES = new Set<StreamRenderItem["type"]>(["thinking_status", "context", "router_thinking"]);

/**
 * Whether a round that ended early (error, dropped connection, stop) left anything worth a
 * bubble. A failed first message that only got as far as 「正在组装上下文…」 should not
 * leave an empty bubble next to the error card.
 */
export function hasSubstantiveDisplayItems(items: StreamRenderItem[]): boolean {
  return items.some((item) => !TRANSIENT_ITEM_TYPES.has(item.type));
}

/** How a stopped / interrupted round ended, as the server recorded it on the assistant message. */
export interface RoundStopOutcome {
  reason: 'user_stopped' | 'client_disconnected' | 'error' | 'incomplete' | 'completed';
  /** Counted toward the daily AI messages (false: refunded). */
  charged: boolean;
  /** Something was written into a file before it ended. */
  savedOutput: boolean;
  /** Titles of blank files this round created and the server removed. */
  removedFiles: string[];
  /**
   * Files this round wrote text into, in order (`stop_outcome.written_files`). `words` is the
   * editor word count of what this round wrote, null when unknown (an edit). Empty for older
   * messages and for writes the server cannot name (parallel chapters, deletions).
   */
  writtenFiles: RoundWrittenFile[];
  unwrittenFiles?: RoundWrittenFile[];
  /**
   * A normally completed round where the author asked for body text and no file was written
   * (`stop_outcome.no_file_changes`). It is the receipt to check the reply's「已修改」against.
   */
  noFileChanges?: boolean;
}

export interface RoundWrittenFile {
  title: string;
  words: number | null;
  /** Only some of the edits in one edit_file call landed (`partial: true`). */
  partial?: boolean;
}

/**
 * Reads `written_files` ([{id, title, words?}]) as the server sends it: in
 * `stop_outcome.written_files` after a refresh, and in the stopped round's `done` frame live.
 */
export function parseWrittenFiles(raw: unknown): RoundWrittenFile[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const { title, words, partial } = item as { title?: unknown; words?: unknown; partial?: unknown };
    if (typeof title !== 'string' || !title) return [];
    return [{
      title,
      words: typeof words === 'number' && Number.isFinite(words) ? words : null,
      ...(partial === true ? { partial: true } : {}),
    }];
  });
}

/**
 * Reads `message_metadata.stop_outcome` (written by the server after a stop / disconnect
 * settles; see agent/core/round_outcome.py). Returns null for every other message.
 */
export function parseRoundStopOutcome(metadata: unknown): RoundStopOutcome | null {
  let parsed: unknown = metadata;
  if (typeof metadata === 'string') {
    try {
      parsed = JSON.parse(metadata);
    } catch {
      return null;
    }
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const outcome = (parsed as { stop_outcome?: unknown }).stop_outcome;
  if (!outcome || typeof outcome !== 'object') return null;
  const raw = outcome as Record<string, unknown>;
  if (!['user_stopped', 'client_disconnected', 'error', 'incomplete', 'completed'].includes(String(raw.reason))) return null;
  const unwrittenFiles = parseWrittenFiles(raw.unwritten_files);
  return {
    reason: raw.reason as RoundStopOutcome['reason'],
    charged: raw.charged !== false,
    savedOutput: raw.saved_output === true,
    removedFiles: Array.isArray(raw.removed_files)
      ? raw.removed_files.filter((title): title is string => typeof title === 'string' && Boolean(title))
      : [],
    writtenFiles: parseWrittenFiles(raw.written_files),
    ...(unwrittenFiles.length ? { unwrittenFiles } : {}),
    ...(raw.no_file_changes === true ? { noFileChanges: true } : {}),
  };
}

/** At most this many written files are named; the rest are counted. */
const NAMED_WRITTEN_FILES = 3;

/**
 * 「已保存《第2章》3,120 字、《人物》等 5 个文件」: the saved-files part of the stop note,
 * shared by the live stop note (ChatPanel) and the end-of-round line after a refresh
 * (RoundEndNote) so both say the same thing.
 */
function formatFileList(
  t: TFunction,
  files: RoundWrittenFile[],
  language: string | undefined,
): string {
  let numberFormat: Intl.NumberFormat;
  try {
    numberFormat = new Intl.NumberFormat(language || undefined);
  } catch {
    numberFormat = new Intl.NumberFormat();
  }
  const named = files.slice(0, NAMED_WRITTEN_FILES).map(({ title, words, partial }) => (
    partial
      ? t('chat:panel.roundSavedFilePartial', { defaultValue: '《{{title}}》（部分修改未生效）', title })
      : words
      ? t('chat:panel.roundSavedFileWords', {
        defaultValue: '《{{title}}》{{words}} 字',
        title,
        words: numberFormat.format(words),
      })
      : t('chat:panel.roundSavedFile', { defaultValue: '《{{title}}》', title })
  ));
  let list = named.join(t('chat:panel.roundSavedFilesJoiner', { defaultValue: '、' }));
  if (files.length > named.length) {
    list = t('chat:panel.roundSavedFilesMore', {
      defaultValue: '{{files}}等 {{total}} 个文件',
      files: list,
      total: files.length,
      rest: files.length - named.length,
    });
  }
  return list;
}

export function formatSavedFiles(t: TFunction, files: RoundWrittenFile[], language: string | undefined): string {
  return t('chat:panel.roundSavedFiles', { defaultValue: '已保存{{files}}', files: formatFileList(t, files, language) });
}

/** The same saved facts in a live error, an author stop and the persisted round. */
export function formatRoundDelivery(
  t: TFunction, written: RoundWrittenFile[], unwritten: RoundWrittenFile[], language: string | undefined,
): string {
  return [
    written.length ? formatSavedFiles(t, written, language) : null,
    unwritten.length ? t('chat:panel.roundUnwrittenFiles', {
      defaultValue: '未确认保存{{files}}', files: formatFileList(t, unwritten.map(file => ({ ...file, words: null })), language),
    }) : null,
  ].filter(Boolean).join(' · ');
}
