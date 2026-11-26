import { describe, it, expect } from 'vitest'
import {
  PROSE_MIN_CHARS,
  createBodyWriteProgress,
  createProseCounter,
  getActiveStreamStatusText,
  isInternalOrderingRefusal,
  isWriteToolResult,
  normalizeStreamStatusText,
} from '../agentRoundProgress'

describe('agentRoundProgress (same output rules as the server bills a stopped round by)', () => {
  it('does not count an empty chapter that create_file just made', () => {
    expect(isWriteToolResult('create_file', 'success', { id: 'ch1', title: '第1章', content: '' })).toBe(false)
    expect(isWriteToolResult('create_file', 'success', { id: 'ch1', content: '雨还在下。' })).toBe(true)
    expect(isWriteToolResult('create_file', 'success', { id: 'ch1', content: '旧正文', reused_existing: true })).toBe(false)
  })

  it('counts write_chapter only once its re-sent replacement card saved new text (server _write_effect)', () => {
    const armed = {
      id: 'ch3', title: '第3章 雨夜', file_type: 'draft', chapter: 3, action: 'replaced',
      awaiting_body: true, previous_length: 2400, previous_word_count: 2400,
    }
    expect(isWriteToolResult('write_chapter', 'success', armed)).toBe(false)
    expect(isWriteToolResult('write_chapter', 'success', {
      id: 'ch4', title: '第4章', file_type: 'draft', content: '', chapter: 4, action: 'created', awaiting_body: true,
    })).toBe(false)
    expect(isWriteToolResult('write_chapter', 'success', { id: 'ch1', chapter: 1, action: 'exists' })).toBe(false)
    expect(isWriteToolResult('write_chapter', 'success', {
      ...armed, awaiting_body: undefined, mutation_applied: false, body_saved: false,
    })).toBe(false)
    expect(isWriteToolResult('write_chapter', 'success', {
      ...armed, awaiting_body: undefined, mutation_applied: true, body_saved: true, new_word_count: 1800,
    })).toBe(true)
    expect(isWriteToolResult('write_chapter', 'error', { ...armed, mutation_applied: true })).toBe(false)
  })

  it('counts an edit only when it changed the text', () => {
    expect(isWriteToolResult('edit_file', 'success', { mutation_applied: false, edits_applied: 0 })).toBe(false)
    expect(isWriteToolResult('edit_file', 'success', { mutation_applied: true, edits_applied: 1 })).toBe(true)
    expect(isWriteToolResult('edit_file', 'error', { mutation_applied: true })).toBe(false)
  })

  it('counts batch_replace_files only on non-dry-run results that modified files, and never read_files/search_files', () => {
    expect(isWriteToolResult('batch_replace_files', 'success', { dry_run: true, files_matched: 3, total_matches: 9, files_modified: 0 })).toBe(false)
    expect(isWriteToolResult('batch_replace_files', 'success', { dry_run: true, matched_files: 3, modified_files: 0, total_replacements: 9, files: [{ id: 'f1' }] })).toBe(false)
    expect(isWriteToolResult('batch_replace_files', 'success', { dry_run: false, files_matched: 0, files_modified: 0, modified_files: [] })).toBe(false)
    expect(isWriteToolResult('batch_replace_files', 'success', { dry_run: false, matched_files: 2, modified_files: 2, mutation_applied: true, files: [{ id: 'f1' }, { id: 'f2' }] })).toBe(true)
    expect(isWriteToolResult('batch_replace_files', 'success', { dry_run: false, files_matched: 2, files_modified: 2, modified_files: [{ id: 'f1' }, { id: 'f2' }] })).toBe(true)
    expect(isWriteToolResult('batch_replace_files', 'error', { dry_run: false, files_modified: 2 })).toBe(false)
    expect(isWriteToolResult('read_files', 'success', { files: [{ id: 'f1' }] })).toBe(false)
    expect(isWriteToolResult('search_files', 'success', { total_matches: 4, files: [{ id: 'f1' }] })).toBe(false)
  })

  it('counts a rename only when the title actually changed', () => {
    expect(isWriteToolResult('rename_file', 'success', { id: 'ch1', title: '新名', mutation_applied: true })).toBe(true)
    expect(isWriteToolResult('rename_file', 'success', { id: 'ch1', title: '旧名', mutation_applied: false })).toBe(false)
  })

  it('counts update_project only when the project info an author sees changed', () => {
    expect(isWriteToolResult('update_project', 'success', { updated_fields: ['summary'] })).toBe(true)
    expect(isWriteToolResult('update_project', 'success', { updated_fields: [], project_name_updated: true })).toBe(true)
    expect(isWriteToolResult('update_project', 'success', { data: { updated_fields: ['notes'] } })).toBe(true)
    expect(isWriteToolResult('update_project', 'success', { plan: { tasks: [] } })).toBe(false)
    expect(isWriteToolResult('update_project', 'success', { updated_fields: ['current_phase'] })).toBe(false)
    expect(isWriteToolResult('update_project', 'success', {
      updated_fields: [], project_name_updated: false, title_skipped: 'author_named',
    })).toBe(false)
    expect(isWriteToolResult('update_project', 'error', { updated_fields: ['summary'] })).toBe(false)
  })

  it('restarts the prose count at a segment boundary inside one streamed segment, like the server', () => {
    const counter = createProseCounter()
    expect(counter.update('seg-1', '甲'.repeat(40))).toBe(false)
    counter.boundary() // agent switch / handoff / file created / parallel start / tool result
    expect(counter.update('seg-1', '甲'.repeat(40) + '乙'.repeat(30))).toBe(false)
    expect(counter.update('seg-1', '甲'.repeat(40) + '乙'.repeat(PROSE_MIN_CHARS))).toBe(true)

    // A new segment starts from zero; a new round forgets everything.
    expect(counter.update('seg-2', '丙'.repeat(30))).toBe(false)
    counter.reset()
    expect(counter.update('seg-2', '丙'.repeat(PROSE_MIN_CHARS))).toBe(true)
  })
})

describe('createBodyWriteProgress (the chat waiting line while a file gets its text)', () => {
  const awaiting = (id: string, title: string) => ({ data: { id, title, file_type: 'draft', content: '', awaiting_body: true } })

  it('names the file from create_file until its text streams in, then counts the words written so far', () => {
    const progress = createBodyWriteProgress()
    expect(progress.snapshot()).toBeNull()

    progress.toolResult('create_file', 'success', awaiting('ch-1', '第一章 雨夜'))
    expect(progress.snapshot()).toEqual({ title: '第一章 雨夜', words: 0 })

    progress.chunk('ch-1', '雨还在下。Ra')
    progress.chunk('ch-1', 'in 落在窗上。')
    expect(progress.snapshot()).toEqual({ title: '第一章 雨夜', words: 9 })

    progress.end('ch-1')
    expect(progress.snapshot()).toBeNull()
  })

  it('names the chapter write_chapter is writing, new or replaced, until its text streams in', () => {
    const progress = createBodyWriteProgress()
    progress.toolResult('write_chapter', 'success', {
      id: 'ch-2', title: '第2章 旧城', file_type: 'draft', chapter: 2, action: 'replaced',
      awaiting_body: true, previous_length: 2400, previous_word_count: 2400,
    })
    expect(progress.snapshot()).toEqual({ title: '第2章 旧城', words: 0 })

    progress.chunk('ch-2', '城门开了。')
    progress.end('ch-2')
    expect(progress.snapshot()).toBeNull()
    // The re-sent saved card for the same call does not reopen the wait.
    progress.toolResult('write_chapter', 'success', {
      id: 'ch-2', title: '第2章 旧城', file_type: 'draft', chapter: 2, action: 'replaced',
      mutation_applied: true, body_saved: true, new_word_count: 5,
    })
    expect(progress.snapshot()).toBeNull()

    progress.toolResult('write_chapter', 'error', {
      data: { id: 'ch-4', title: '第4章', file_type: 'draft', awaiting_body: true },
    })
    expect(progress.snapshot()).toBeNull()
  })

  it('stops naming the waiting file once the model deletes it', () => {
    const progress = createBodyWriteProgress()
    progress.toolResult('create_file', 'success', awaiting('ch-3', '第三章'))
    progress.toolResult('edit_file', 'error', { error_type: 'file_body_pending' })

    // Deleting another file leaves the waiting line alone.
    progress.toolCall('delete_file', { id: 'notes' })
    progress.toolResult('delete_file', 'success', { data: true })
    expect(progress.snapshot()).toEqual({ title: '第三章', words: 0 })

    // A failed delete of the waiting file leaves it too.
    progress.toolCall('delete_file', { id: 'ch-3' })
    progress.toolResult('delete_file', 'error', { error: 'not found' })
    expect(progress.snapshot()).toEqual({ title: '第三章', words: 0 })

    progress.toolCall('delete_file', { id: 'ch-3' })
    progress.toolResult('delete_file', 'success', { data: true })
    expect(progress.snapshot()).toBeNull()
  })

  it('keeps waiting through an internal refusal and an empty end, so the correction rerun shows the same stage', () => {
    const progress = createBodyWriteProgress()
    progress.toolResult('create_file', 'success', awaiting('ch-1', '第一章'))
    // The model called another tool before writing: the server refused it (file_body_pending).
    progress.toolResult('edit_file', 'error', { error_type: 'file_body_pending' })
    // The run ended without the text; the capture reset sends an empty end.
    progress.end('ch-1')
    expect(progress.snapshot()).toEqual({ title: '第一章', words: 0 })

    // The rerun streams the text into the same file.
    progress.chunk('ch-1', '林舟推开门。')
    expect(progress.snapshot()).toEqual({ title: '第一章', words: 5 })
  })

  it('ignores files created with their text, folders and failed creates, and starts over each round', () => {
    const progress = createBodyWriteProgress()
    progress.toolResult('create_file', 'success', { data: { id: 'ch-2', title: '第二章', content: '整章正文', file_type: 'draft' } })
    progress.toolResult('create_file', 'success', { data: { id: 'f-1', title: '第一卷', file_type: 'folder', awaiting_body: true } })
    progress.toolResult('create_file', 'error', awaiting('ch-3', '第三章'))
    expect(progress.snapshot()).toBeNull()

    progress.fileCreated('ch-4', '第四章')
    progress.chunk('ch-4', '风停了。')
    expect(progress.snapshot()).toEqual({ title: '第四章', words: 3 })
    progress.reset()
    expect(progress.snapshot()).toBeNull()
  })
})

describe('isInternalOrderingRefusal', () => {
  it('recognises the server\'s write-order, same-turn chapter-rewrite, wall-clock wrap-up, and repeat-edit guards, and nothing else', () => {
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'file_body_pending', error: 'internal' })).toBe(true)
    expect(isInternalOrderingRefusal({ data: { error_type: 'pending_empty_file_unwritten' } })).toBe(true)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'chapter_written_this_turn', error: 'internal' })).toBe(true)
    expect(isInternalOrderingRefusal({ data: { error_type: 'chapter_written_this_turn' } })).toBe(true)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'wall_clock_wrap_up', error: 'internal' })).toBe(true)
    expect(isInternalOrderingRefusal({ data: { error_type: 'wall_clock_wrap_up', user_message: '本轮已进入收尾阶段，这一步留到下一轮继续。' } })).toBe(true)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'edit_repeat_limit', error: 'internal' })).toBe(true)
    expect(isInternalOrderingRefusal({ data: { error_type: 'edit_repeat_limit' } })).toBe(true)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'chapter_replace_pending' })).toBe(false)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'stale_content' })).toBe(false)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'clarify_first' })).toBe(false)
    expect(isInternalOrderingRefusal(undefined)).toBe(false)
  })
})

describe('stream status text helpers', () => {
  it('normalizes whitespace and rejects empty status strings', () => {
    expect(normalizeStreamStatusText('  正在准备编辑文件… \n （已生成 1,500 字）  ')).toBe('正在准备编辑文件… （已生成 1,500 字）')
    expect(normalizeStreamStatusText('   \n\t  ')).toBeNull()
    expect(normalizeStreamStatusText(null)).toBeNull()
    expect(normalizeStreamStatusText(undefined)).toBeNull()
  })

  it('extracts active thinking_status only when no newer tool call or visible text supersedes it', () => {
    expect(
      getActiveStreamStatusText([
        { type: 'thinking_content', content: '先分析一下' },
        { type: 'thinking_status', content: '正在准备编辑文件…', transient: true },
        { type: 'content', content: '   ' },
      ]),
    ).toBe('正在准备编辑文件…')

    expect(
      getActiveStreamStatusText([
        { type: 'thinking_status', content: '正在准备编辑文件…', transient: true },
        { type: 'tool_calls' },
      ]),
    ).toBeNull()

    expect(
      getActiveStreamStatusText([
        { type: 'thinking_status', content: '正在准备编辑文件…', transient: true },
        { type: 'content', content: '修改完成。' },
      ]),
    ).toBeNull()
  })
})
