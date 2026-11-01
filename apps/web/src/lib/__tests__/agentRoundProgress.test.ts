import { describe, it, expect } from 'vitest'
import { PROSE_MIN_CHARS, createBodyWriteProgress, createProseCounter, isRealProse, isInternalOrderingRefusal, isWriteToolResult } from '../agentRoundProgress'

describe('agentRoundProgress (same output rules as the server bills a stopped round by)', () => {
  it('does not count an empty chapter that create_file just made', () => {
    expect(isWriteToolResult('create_file', 'success', { id: 'ch1', title: '第1章', content: '' })).toBe(false)
    expect(isWriteToolResult('create_file', 'success', { id: 'ch1', content: '雨还在下。' })).toBe(true)
    expect(isWriteToolResult('create_file', 'success', { id: 'ch1', content: '旧正文', reused_existing: true })).toBe(false)
  })

  it('counts an edit only when it changed the text', () => {
    expect(isWriteToolResult('edit_file', 'success', { mutation_applied: false, edits_applied: 0 })).toBe(false)
    expect(isWriteToolResult('edit_file', 'success', { mutation_applied: true, edits_applied: 1 })).toBe(true)
    expect(isWriteToolResult('edit_file', 'error', { mutation_applied: true })).toBe(false)
  })

  it('counts a rename only when the title actually changed', () => {
    expect(isWriteToolResult('rename_file', 'success', { id: 'ch1', title: '新名', mutation_applied: true })).toBe(true)
    expect(isWriteToolResult('rename_file', 'success', { id: 'ch1', title: '旧名', mutation_applied: false })).toBe(false)
  })

  it('counts parallel chapter writes but not parallel lookups', () => {
    expect(isWriteToolResult('parallel_execute', 'success', {
      tasks: [{ type: 'write_chapter', status: 'completed' }],
    })).toBe(true)
    expect(isWriteToolResult('parallel_execute', 'success', {
      tasks: [{ type: 'query_files', status: 'completed' }, { type: 'write_chapter', status: 'failed' }],
    })).toBe(false)
  })

  it('counts a partially applied parallel edit as a write', () => {
    expect(isWriteToolResult('parallel_execute', 'success', {
      tasks: [{ type: 'edit_file', status: 'partial' }],
    })).toBe(true)
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

  it('treats a one-line narration before a tool call as not yet prose', () => {
    expect(isRealProse('我先看一遍全书大纲。')).toBe(false)
    expect(isRealProse('正在查看已有的全书大纲，确认现有设定后再梳理主线。')).toBe(false)
    expect(isRealProse('字'.repeat(PROSE_MIN_CHARS))).toBe(true)
    expect(isRealProse(' \n'.repeat(PROSE_MIN_CHARS))).toBe(false)
    // English counts words, so a one-sentence narration stays below the bar like its Chinese twin.
    expect(isRealProse('Let me check the outline and the character sheet before I start drafting chapter three.')).toBe(false)
    expect(isRealProse("Chapter three drags: the first two scenes explain the bookshop's history, and the hero only finds the untitled ledger in scene three. Readers wait too long, so move the ledger to the opening page and let the history surface while she reads it.")).toBe(true)
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
  it('recognises the server\'s write-order refusals, top-level or nested, and nothing else', () => {
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'file_body_pending', error: 'internal' })).toBe(true)
    expect(isInternalOrderingRefusal({ data: { error_type: 'pending_empty_file_unwritten' } })).toBe(true)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'stale_content' })).toBe(false)
    expect(isInternalOrderingRefusal({ status: 'error', error_type: 'clarify_first' })).toBe(false)
    expect(isInternalOrderingRefusal(undefined)).toBe(false)
  })
})
