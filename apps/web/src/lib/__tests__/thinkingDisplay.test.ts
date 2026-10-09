import { describe, expect, it } from 'vitest'
import { sanitizeThinkingForDisplay } from '../thinkingDisplay'

const labels: Record<string, string> = {
  'chat:tool.create_file': '创建文件',
  'chat:tool.edit_file': '编辑文件',
  'chat:tool.query_files': '查找文件',
  'chat:tool.update_project': '更新项目信息',
  'chat:tool.load_skill': '启用技能',
  'chat:tool.read_skill_resource': '查看技能资料',
  'chat:tool.hybrid_search': '搜索相关内容',
  'chat:tool.delete_file': '删除文件',
  'chat:workflow.agents.planner': '大纲规划师',
  'chat:workflow.agents.writer': '内容创作者',
  'chat:workflow.agents.hook_designer': '爽点设计师',
  'chat:workflow.agents.quality_reviewer': '质量审稿人',
}
const t = (key: string) => labels[key] ?? key

describe('sanitizeThinkingForDisplay', () => {
  it('replaces underscore tool and agent identifiers with author-facing names', () => {
    const raw = '先用 query_files 看看大纲，再 `edit_file` 改第二章，然后交给 quality_reviewer 和 hook_designer。'
    expect(sanitizeThinkingForDisplay(raw, t)).toBe(
      '先用 查找文件 看看大纲，再 编辑文件 改第二章，然后交给 质量审稿人 和 爽点设计师。',
    )
  })

  it('falls back to "AI" for internal names without an author-facing label', () => {
    const raw = 'I should call handoff_to_agent, or request_clarification if unsure.'
    expect(sanitizeThinkingForDisplay(raw, t)).toBe('I should call AI, or AI if unsure.')
  })

  it('replaces backticked planner and writer but keeps the plain English word', () => {
    const raw = 'Hand this to `writer` after `planner`. The writer of this story wants a twist.'
    expect(sanitizeThinkingForDisplay(raw, t)).toBe(
      'Hand this to 内容创作者 after 大纲规划师. The writer of this story wants a twist.',
    )
  })

  it('does not touch identifiers embedded in longer snake_case words', () => {
    expect(sanitizeThinkingForDisplay('my_create_file_helper stays', t)).toBe('my_create_file_helper stays')
  })

  it('masks UUIDs', () => {
    const raw = '文件 id 是 3f2b1c9e-8a7d-4e6f-9b0a-1c2d3e4f5a6b，先读它。'
    expect(sanitizeThinkingForDisplay(raw, t)).toBe('文件 id 是 …，先读它。')
  })

  it('strips English and Chinese control markers', () => {
    const raw = '写完了 [TASK_COMPLETE] [任务完成]\n还要问一句 [需要澄清] [NEEDS_CLARIFICATION]'
    expect(sanitizeThinkingForDisplay(raw, t)).toBe('写完了\n还要问一句')
  })

  it('drops whole lines that carry raw <file> blocks', () => {
    const raw = '准备写入：\n<file id="x" title="第一章">\n正文第一行\n</file>\n写完再检查。'
    expect(sanitizeThinkingForDisplay(raw, t)).toBe('准备写入：\n正文第一行\n写完再检查。')
  })

  it('leaves ordinary reasoning untouched', () => {
    const raw = 'The user wants a darker tone.\n\nKeep the opening scene short.'
    expect(sanitizeThinkingForDisplay(raw, t)).toBe(raw)
  })
})
