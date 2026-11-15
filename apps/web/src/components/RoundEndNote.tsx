import type { ReactElement } from 'react';
import { useTranslation } from 'react-i18next';
import { CirclePause } from 'lucide-react';
import { formatRoundDelivery, type RoundStopOutcome } from '../lib/chatRoundEnd';

/**
 * The grey end-of-round line on a stopped / interrupted assistant message after a refresh,
 * e.g. 「已停止 · 未计入今日 AI 消息」 / 「已停止 · 已写入的内容已保存 · 本条计入今日 AI 消息」 / 「已中断」:
 * the same wording as the live stop note. Pro (no daily limit) never mentions the daily count.
 * When the server named the files this round wrote, the note says which ones instead of only
 * 「已写入的内容已保存」: 「已中断 · 已保存《第2章》3,120 字 · 本条计入今日 AI 消息」.
 */
export function RoundEndNote({ outcome, showDailyCount, nextStepsBlocked = false }: {
  outcome: RoundStopOutcome;
  showDailyCount: boolean;
  /** Today's AI messages are used up: the same facts without 「可以继续」 (the quota card says when sending comes back). */
  nextStepsBlocked?: boolean;
}): ReactElement {
  const { t, i18n } = useTranslation(['chat']);
  // An unfinished round that confirmed saving the files it named (a time pause, a hard timeout after
  // writes) lists them below like any other round: the receipt stands even when the reply's summary is
  // short. Without a confirmed save it keeps the plain "unfinished" line rather than an empty note.
  const listsSavedFiles = outcome.savedOutput && outcome.writtenFiles.length > 0;
  // A step only written out as text never ran: there is nothing to resume, so no 「可以继续」 either.
  const withoutContinue = nextStepsBlocked || outcome.unexecutedToolText === true;
  if (outcome.reason === 'incomplete' && !outcome.unwrittenFiles?.length && !listsSavedFiles) {
    let text: string;
    if (withoutContinue) {
      text = outcome.savedOutput
        ? t('chat:panel.roundIncompleteSavedPaused', { defaultValue: '已保存本轮完成的修改。' })
        : outcome.noFileChanges
          ? t('chat:panel.roundIncompleteNoChangesPaused', { defaultValue: '本轮尚未写入新内容。' })
          : t('chat:panel.roundIncompletePaused', { defaultValue: '本轮尚未完成。' });
    } else if (outcome.savedOutput) {
      text = t('chat:panel.roundIncompleteSaved', { defaultValue: '已保存本轮完成的修改，可以继续写后续内容。' });
    } else if (outcome.noFileChanges) {
      text = t('chat:panel.roundIncompleteNoChanges', { defaultValue: '本轮尚未写入新内容，可以继续完成。' });
    } else {
      text = t('chat:panel.roundIncomplete', { defaultValue: '本轮尚未完成，可以继续。' });
    }
    return (
      <div className="mt-2 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]" data-testid="round-end-note">
        <span>{text}</span>
      </div>
    );
  }
  const savedText = outcome.writtenFiles.length > 0 || outcome.unwrittenFiles?.length
    ? formatRoundDelivery(t, outcome.writtenFiles, outcome.unwrittenFiles ?? [], i18n?.language)
    : t('chat:panel.userStoppedSaved', { defaultValue: '已写入的内容已保存' });
  const finished = outcome.reason === 'incomplete' || outcome.reason === 'completed';
  const dailyCount = showDailyCount && !finished;
  const parts = outcome.noFileChanges ? [
    t('chat:panel.roundNoFileChanges', { defaultValue: '本轮没有改动文件' }),
  ] : [
    finished ? null : outcome.reason === 'user_stopped'
      ? t('chat:panel.userStopped', { defaultValue: '已停止' })
      : t('chat:panel.roundInterrupted', { defaultValue: '已中断' }),
    !outcome.charged && dailyCount
      ? t('chat:panel.roundNotCounted', { defaultValue: '未计入今日 AI 消息' })
      : null,
    (outcome.charged && outcome.savedOutput) || outcome.unwrittenFiles?.length
      || (outcome.reason === 'completed' && outcome.writtenFiles.length > 0)
      || (outcome.reason === 'incomplete' && listsSavedFiles) ? savedText : null,
    // The file receipt replaces the plain unfinished line, so keep saying the round is unfinished:
    // an empty-file correction card is hidden and, with today's messages used up, has no continue button.
    outcome.reason === 'incomplete' && listsSavedFiles && !outcome.unwrittenFiles?.length
      ? withoutContinue
        ? t('chat:panel.roundUnfinishedItem', { defaultValue: '本轮尚未完成' })
        : t('chat:panel.roundUnfinishedContinueItem', { defaultValue: '本轮尚未完成，可以继续' })
      : null,
    outcome.charged && dailyCount
      ? t('chat:panel.userStoppedCounted', { defaultValue: '本条计入今日 AI 消息' })
      : null,
  ].filter(Boolean);

  return (
    <div
      className="mt-2 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]"
      data-testid="round-end-note"
    >
      {!finished && <CirclePause size={14} className="shrink-0" />}
      <span>{parts.join(' · ')}</span>
    </div>
  );
}
