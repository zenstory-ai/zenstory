/**
 * Human-readable labels for the machine-written summaries stored on file
 * versions and project snapshots.
 *
 * The server records stable English (and a few legacy Chinese) markers such
 * as "Before restoring version 3" or "Before rollback to snapshot <uuid>".
 * Showing them verbatim put English and raw UUIDs in front of writers, so the
 * history panels translate the known markers and scrub ids from anything else.
 */

type Translate = (key: string, options?: Record<string, unknown>) => string;

const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const RESTORED_TO_VERSION = /^Restored to version (\d+)$/;
const BEFORE_RESTORE = /^(?:Before restoring version \d+|Before rollback to snapshot(?: \S+)?)$/;
const RESTORED_FROM_SNAPSHOT = /^Restored from snapshot(?: \S+)?$/;
const AI_RUN_CHECKPOINT =
  /^(?:AI 对话完成 - 文件已修改|AI (?:chat|conversation) (?:finished|completed?) - files? (?:modified|changed))$/i;

export function formatVersionSummary(summary: string | null | undefined, t: Translate): string {
  const text = (summary ?? '').trim();
  if (!text) return '';

  const restoredTo = RESTORED_TO_VERSION.exec(text);
  if (restoredTo) {
    return t('versions:summary.restoredTo', {
      version: Number(restoredTo[1]),
      defaultValue: '恢复到版本 {{version}}',
    });
  }
  if (BEFORE_RESTORE.test(text)) {
    return t('versions:summary.beforeRestore', { defaultValue: '恢复前自动备份' });
  }
  if (RESTORED_FROM_SNAPSHOT.test(text)) {
    return t('versions:summary.restoredFromSnapshot', { defaultValue: '从项目快照恢复' });
  }
  if (text === 'Before AI edit') {
    return t('versions:summary.beforeAiEdit', { defaultValue: 'AI 修改前自动备份' });
  }
  if (text === 'AI edit (reviewed)') {
    return t('versions:summary.aiEditReviewed', { defaultValue: 'AI 修改（已审阅）' });
  }
  if (text === 'File updated') {
    return t('versions:summary.manualEdit', { defaultValue: '手动编辑' });
  }
  if (text === 'Initial version' || text === '创建文件') {
    return t('versions:summary.initial', { defaultValue: '初始版本' });
  }
  if (text === 'Snapshot baseline version' || text === 'Snapshot synchronized live content') {
    return t('versions:summary.snapshotBaseline', { defaultValue: '拍项目快照时自动保存' });
  }
  if (AI_RUN_CHECKPOINT.test(text)) {
    return t('versions:summary.aiRunCheckpoint', { defaultValue: 'AI 修改后自动存档' });
  }

  return text.replace(UUID_PATTERN, '…');
}
