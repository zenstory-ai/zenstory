/**
 * Human-readable labels for the machine-written summaries stored on file
 * versions and project snapshots.
 *
 * The server and the agent record stable English (and some Chinese) markers
 * such as "Before restoring version 3", "Before rollback to snapshot <uuid>"
 * or "AI 编辑: 替换、追加". Showing them verbatim put English, Chinese and raw
 * UUIDs in front of writers regardless of the UI language. The history panels
 * translate the known markers, hide the ones that only repeat the version's
 * type badge, and show anything else (user notes) with ids scrubbed.
 *
 * Keys live under `versions:summary.*`; every inline `defaultValue` equals the
 * zh locale text.
 */

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** Say the same thing as the type badge (创建 / 编辑 / AI 编辑), so no extra line. */
const REDUNDANT_SUMMARIES = new Set([
  'File updated', // api/files.py default
  'Initial version', // file_version_service first version
  '创建文件', // agent create_file
  'AI 更新文件内容', // agent writes file content
  'AI 编辑', // agent edit_file without listed operations
]);

const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
const RESTORED_TO_VERSION = /^Restored to version (\d+)$/;
// Snapshot markers carry an internal snapshot id (UUID or otherwise); writers never see it.
const BEFORE_RESTORE = /^(?:Before restoring version \d+|Before rollback to snapshot(?:\s+\S+)?)$/;
const RESTORED_FROM_SNAPSHOT = /^Restored from snapshot(?:\s+\S+)?$/;
const AI_RUN_CHECKPOINT =
  /^(?:AI 对话完成 - 文件已修改|AI (?:chat|conversation) (?:finished|completed?) - files? (?:modified|changed))$/i;
const AI_EDIT_WITH_OPS = /^AI 编辑[:：]\s*(.+?)(?:\s*等\s*(\d+)\s*处修改)?$/;

/**
 * agent edit_file writes "AI 编辑: 替换、替换、替换 等 5 处修改". Listing the operation
 * names (替换、替换、替换) told writers nothing; the number of places changed does.
 */
const describeAiEditCount = (opsText: string, total: string | undefined, t: Translate): string | null => {
  const listed = opsText.split(/[,，、]\s*/).filter((op) => op.trim()).length;
  const count = total ? Number(total) : listed;
  if (!Number.isFinite(count) || count <= 0) return null;
  return t('versions:summary.aiEditCount', { count, defaultValue: 'AI 改了 {{count}} 处' });
};

/**
 * Returns the line to show under a version or snapshot entry, or null when
 * there is nothing worth showing (empty, or it repeats the type badge).
 */
export function describeVersionSummary(summary: string | null | undefined, t: Translate): string | null {
  const text = (summary ?? '').trim();
  if (!text) return null;
  if (REDUNDANT_SUMMARIES.has(text)) return null;

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
  if (text === 'Snapshot baseline version' || text === 'Snapshot synchronized live content') {
    return t('versions:summary.snapshotBaseline', { defaultValue: '拍项目快照时自动保存' });
  }
  if (text === 'Created via Agent API') {
    return t('versions:summary.createdViaApi', { defaultValue: '通过 Agent API 创建' });
  }
  if (text === 'Updated via Agent API') {
    return t('versions:summary.updatedViaApi', { defaultValue: '通过 Agent API 更新' });
  }
  if (AI_RUN_CHECKPOINT.test(text)) {
    return t('versions:summary.aiRunCheckpoint', { defaultValue: 'AI 修改后自动存档' });
  }

  const aiEdit = AI_EDIT_WITH_OPS.exec(text);
  if (aiEdit) return describeAiEditCount(aiEdit[1], aiEdit[2], t);

  return text.replace(UUID_PATTERN, '…');
}
