/**
 * Display-only cleanup for the model's reasoning ("AI 思考过程").
 *
 * The reasoning stream is written for the model itself: it names tools and
 * agents by their internal identifiers, quotes file ids and raw `<file>` blocks,
 * and repeats control markers. Authors only need the gist, so the panel shows a
 * cleaned copy. Nothing here changes what the server stores or what is sent
 * back to the model.
 */
import { stripAgentControlMarkers } from './utils'

type Translate = (key: string) => string

/** Internal identifiers that contain an underscore, mapped to their label key (null = "AI"). */
const UNDERSCORE_IDENTIFIER_KEYS: Record<string, string | null> = {
  create_file: 'chat:tool.create_file',
  update_file: 'chat:tool.update_file',
  edit_file: 'chat:tool.edit_file',
  delete_file: 'chat:tool.delete_file',
  query_files: 'chat:tool.query_files',
  hybrid_search: 'chat:tool.hybrid_search',
  update_project: 'chat:tool.update_project',
  parallel_execute: 'chat:tool.parallel_execute',
  load_skill: 'chat:tool.load_skill',
  read_skill_resource: 'chat:tool.read_skill_resource',
  hook_designer: 'chat:workflow.agents.hook_designer',
  quality_reviewer: 'chat:workflow.agents.quality_reviewer',
  handoff_to_agent: null,
  request_clarification: null,
}

/**
 * Agent names that are also ordinary English words; only replaced when the
 * model quotes them as identifiers (in backticks).
 */
const BACKTICK_ONLY_IDENTIFIER_KEYS: Record<string, string> = {
  planner: 'chat:workflow.agents.planner',
  writer: 'chat:workflow.agents.writer',
}

const FALLBACK_LABEL = 'AI'

const UNDERSCORE_IDENTIFIER_PATTERN = new RegExp(
  `(^|[^A-Za-z0-9_])\`?(${Object.keys(UNDERSCORE_IDENTIFIER_KEYS).join('|')})\`?(?![A-Za-z0-9_])`,
  'g',
)
const BACKTICK_IDENTIFIER_PATTERN = new RegExp(
  `\`(${Object.keys(BACKTICK_ONLY_IDENTIFIER_KEYS).join('|')})\``,
  'g',
)
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi
const FILE_BLOCK_LINE_PATTERN = /<file\b|<\/file>/i

const labelFor = (key: string | null | undefined, t: Translate): string => {
  if (!key) return FALLBACK_LABEL
  const label = t(key)
  return label && label !== key ? label : FALLBACK_LABEL
}

/**
 * Returns the reasoning text as it should appear in the thinking panel.
 *
 * - Internal tool/agent identifiers become their localized names (or "AI").
 * - UUIDs become "…".
 * - Control markers such as `[TASK_COMPLETE]` / `[任务完成]` / `[需要澄清]` are removed.
 * - Lines carrying raw `<file …>` / `</file>` tags are dropped.
 */
export function sanitizeThinkingForDisplay(text: string, t: Translate): string {
  if (!text) return text

  const keptLines = text
    .split('\n')
    .filter((line) => !FILE_BLOCK_LINE_PATTERN.test(line))
    .join('\n')

  return stripAgentControlMarkers(keptLines)
    .replace(UNDERSCORE_IDENTIFIER_PATTERN, (_match, prefix: string, name: string) =>
      `${prefix}${labelFor(UNDERSCORE_IDENTIFIER_KEYS[name], t)}`,
    )
    .replace(BACKTICK_IDENTIFIER_PATTERN, (_match, name: string) =>
      labelFor(BACKTICK_ONLY_IDENTIFIER_KEYS[name], t),
    )
    .replace(UUID_PATTERN, '…')
    .replace(/[ \t]+$/gm, '')
    .trim()
}
