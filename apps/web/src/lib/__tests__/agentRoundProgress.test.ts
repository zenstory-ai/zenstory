import { describe, it, expect } from 'vitest'
import { PROSE_MIN_CHARS, createProseCounter, isRealProse, isWriteToolResult } from '../agentRoundProgress'

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

  it('counts parallel chapter writes but not parallel lookups', () => {
    expect(isWriteToolResult('parallel_execute', 'success', {
      tasks: [{ type: 'write_chapter', status: 'completed' }],
    })).toBe(true)
    expect(isWriteToolResult('parallel_execute', 'success', {
      tasks: [{ type: 'query_files', status: 'completed' }, { type: 'write_chapter', status: 'failed' }],
    })).toBe(false)
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
