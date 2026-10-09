const WRITE_TOOLS = new Set(['create_file', 'update_file', 'edit_file', 'delete_file']);
const PARALLEL_WRITE_TASKS = new Set(['write_chapter', 'edit_file', 'delete_file']);

/**
 * Whether a tool result means this round changed a file, so a stop note may
 * say "已写入的内容已保存". `parallel_execute` counts only when one of its
 * completed sub-tasks is a write; a parallel batch of lookups changed nothing.
 */
export function isWriteToolResult(
  toolName: string,
  status: string,
  result?: Record<string, unknown>,
): boolean {
  if (status !== 'success') return false;
  if (WRITE_TOOLS.has(toolName)) return true;
  if (toolName !== 'parallel_execute') return false;

  const payload = (result?.data ?? result) as Record<string, unknown> | undefined;
  const tasks = Array.isArray(payload?.tasks) ? (payload.tasks as Array<Record<string, unknown>>) : [];
  return tasks.some(
    (task) => task?.status === 'completed' && typeof task.type === 'string' && PARALLEL_WRITE_TASKS.has(task.type),
  );
}
