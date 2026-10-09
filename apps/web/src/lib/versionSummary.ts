/**
 * 版本历史里的 change_summary 大多是后端/前端写死的系统文案（有英文有中文，
 * 快照恢复还带内部 id）。这里把已知的系统文案换成当前界面语言的说法，
 * 与类型徽标重复的直接不显示；认不出来的（用户自己写的备注）原样显示。
 */

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** 与类型徽标（创建/编辑/AI 编辑）说的是同一件事，不再重复一行。 */
const REDUNDANT_SUMMARIES = new Set([
  'File updated', // api/files.py 默认值
  'Initial version', // file_version_service 首个版本
  '创建文件', // agent create_file
  'AI 更新文件内容', // agent 写入文件内容
  'AI 编辑', // agent edit_file，无具体操作
]);

const FIXED_SUMMARY_KEYS: Record<string, string> = {
  'Snapshot baseline version': 'summaries.snapshotBaseline',
  'Snapshot synchronized live content': 'summaries.snapshotSync',
  'Created via Agent API': 'summaries.createdViaApi',
  'Updated via Agent API': 'summaries.updatedViaApi',
  'AI edit (reviewed)': 'summaries.aiReviewed',
};

/** agent edit_file 写入的操作名（apps/server/agent/tools/file_ops/edit.py）。 */
const AI_EDIT_OP_KEYS: Record<string, string> = {
  替换: 'summaries.ops.replace',
  追加: 'summaries.ops.append',
  前置: 'summaries.ops.prepend',
  插入: 'summaries.ops.insert',
  删除: 'summaries.ops.delete',
};

const RESTORED_TO_VERSION = /^Restored to version (\d+)$/;
// 快照恢复会带上快照 id（UUID 或其他内部 id），id 不给作者看。
const RESTORED_FROM_SNAPSHOT = /^Restored from snapshot(?:\s+\S+)?$/;
const AI_EDIT_WITH_OPS = /^AI 编辑[:：]\s*(.+?)(?:\s*等\s*(\d+)\s*处修改)?$/;

const describeAiEditOps = (opsText: string, total: string | undefined, t: Translate): string | null => {
  const opKeys = opsText.split(/[,，、]\s*/).map((op) => AI_EDIT_OP_KEYS[op.trim()]);
  // 有认不出的操作名就不显示这一行：它仍是系统文案，徽标已经写了「AI 编辑」。
  if (opKeys.length === 0 || opKeys.some((key) => !key)) return null;
  const ops = opKeys.map((key) => t(key)).join(t('summaries.opsSeparator'));
  return total
    ? t('summaries.aiEditOpsMore', { ops, total: Number(total) })
    : t('summaries.aiEditOps', { ops });
};

/**
 * 返回要在版本条目下显示的说明；返回 null 表示不显示。
 * `t` 需要绑定 versions 命名空间。
 */
export function describeVersionSummary(summary: string | null | undefined, t: Translate): string | null {
  const text = summary?.trim();
  if (!text) return null;
  if (REDUNDANT_SUMMARIES.has(text)) return null;

  const fixedKey = FIXED_SUMMARY_KEYS[text];
  if (fixedKey) return t(fixedKey);

  const restoredTo = RESTORED_TO_VERSION.exec(text);
  if (restoredTo) return t('summaries.restoredToVersion', { version: Number(restoredTo[1]) });

  if (RESTORED_FROM_SNAPSHOT.test(text)) return t('summaries.restoredFromSnapshot');

  const aiEdit = AI_EDIT_WITH_OPS.exec(text);
  if (aiEdit) return describeAiEditOps(aiEdit[1], aiEdit[2], t);

  return text;
}
