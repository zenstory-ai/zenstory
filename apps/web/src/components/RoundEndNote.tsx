import { useTranslation } from 'react-i18next';
import { CirclePause } from 'lucide-react';
import type { RoundStopOutcome } from '../lib/chatRoundEnd';

/**
 * The grey end-of-round line on a stopped / interrupted assistant message after a refresh,
 * e.g. 「已停止 · 未计入今日 AI 消息」 / 「已停止 · 已写入的内容已保存」 / 「已中断」.
 * Pro (no daily limit) never mentions the daily count.
 */
export function RoundEndNote({ outcome, showDailyCount }: { outcome: RoundStopOutcome; showDailyCount: boolean }) {
  const { t } = useTranslation(['chat']);
  const parts = [
    outcome.reason === 'user_stopped'
      ? t('chat:panel.userStopped', { defaultValue: '已停止' })
      : t('chat:panel.roundInterrupted', { defaultValue: '已中断' }),
    !outcome.charged && showDailyCount
      ? t('chat:panel.roundNotCounted', { defaultValue: '未计入今日 AI 消息' })
      : null,
    outcome.charged && outcome.savedOutput
      ? t('chat:panel.userStoppedSaved', { defaultValue: '已写入的内容已保存' })
      : null,
    outcome.removedFiles.length > 0
      ? t('chat:panel.roundRemovedFiles', {
        defaultValue: '空白的《{{titles}}》已移除',
        titles: outcome.removedFiles.join('》《'),
      })
      : null,
  ].filter(Boolean);

  return (
    <div
      className="mt-2 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]"
      data-testid="round-end-note"
    >
      <CirclePause size={14} className="shrink-0" />
      <span>{parts.join(' · ')}</span>
    </div>
  );
}
