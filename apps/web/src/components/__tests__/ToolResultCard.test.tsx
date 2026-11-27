import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolResultCard } from '../ToolResultCard'

// Mock i18n with proper translations
const mockT = vi.fn((key: string, options?: Record<string, unknown>) => {
  // Handle size translations with options
  if (key === 'chat:size.characters' && options?.length !== undefined) {
    return `${options.length} characters`
  }
  if (key === 'chat:size.kCharacters' && options?.length !== undefined) {
    return `${options.length}k`
  }
  if (key === 'chat:tool.query_description' && options?.label !== undefined) {
    return `Query ${options.label}`
  }
  if (key === 'chat:tool.created' && options?.type !== undefined) {
    return `Created ${options.type}`
  }
  if (key === 'chat:tool.files_found' && options?.count !== undefined) {
    return `Files found`
  }
  if (key === 'chat:tool.files_read' && options?.count !== undefined) {
    return `Read ${options.count} files`
  }
  if (key === 'chat:tool.read_files_count' && options?.count !== undefined) {
    return `${options.count} files`
  }
  if (key === 'chat:tool.search_matches_many_in_one' && options?.matches !== undefined) {
    return `Found ${options.matches} matches in 1 file`
  }
  if (key === 'chat:tool.search_matches_many_in_many' && options?.files !== undefined && options?.matches !== undefined) {
    return `Found ${options.matches} matches across ${options.files} files`
  }
  if (key === 'chat:tool.search_file_match_count' && options?.count !== undefined) {
    return `${options.count} matches`
  }
  if (key === 'chat:tool.batch_replace_scope_files' && options?.count !== undefined) {
    return `${options.count} files`
  }
  if (key === 'chat:tool.batch_replace_preview_many_in_one' && options?.matches !== undefined) {
    return `Preview: ${options.matches} matches in 1 file`
  }
  if (key === 'chat:tool.batch_replace_preview_many_in_many' && options?.files !== undefined && options?.matches !== undefined) {
    return `Preview: ${options.matches} matches across ${options.files} files`
  }
  if (key === 'chat:tool.batch_replace_preview_truncated_one_in_one' && options?.matched !== undefined) {
    return `Preview: 1 match in the first file (${options.matched} files matched in total)`
  }
  if (key === 'chat:tool.batch_replace_preview_truncated_many_in_one' && options?.matches !== undefined && options?.matched !== undefined) {
    return `Preview: ${options.matches} matches in the first file (${options.matched} files matched in total)`
  }
  if (key === 'chat:tool.batch_replace_preview_truncated_many_in_many' && options?.files !== undefined && options?.matches !== undefined && options?.matched !== undefined) {
    return `Preview: ${options.matches} matches in the first ${options.files} files (${options.matched} files matched in total)`
  }
  if (key === 'chat:tool.batch_replace_done_many_in_one' && options?.matches !== undefined) {
    return `Replaced ${options.matches} matches in 1 file`
  }
  if (key === 'chat:tool.batch_replace_done_many_in_many' && options?.files !== undefined && options?.matches !== undefined) {
    return `Replaced ${options.matches} matches across ${options.files} files`
  }
  if (key === 'chat:tool.batch_replace_failed_count' && options?.count !== undefined) {
    return `${options.count} files skipped`
  }
  if (key === 'chat:tool.batch_replace_remaining_files' && options?.count !== undefined) {
    return `${options.count} more files remaining`
  }
  if (key === 'chat:tool.batch_replace_file_count' && options?.count !== undefined) {
    return `${options.count} replacements`
  }
  if (key === 'chat:tool.batch_replace_view_files' && options?.count !== undefined) {
    return `View affected files (${options.count})`
  }
  if (key === 'chat:tool.parallelTaskLabel' && options?.label !== undefined) {
    return `${options.label}:`
  }
  if (key === 'chat:workflow.charCount' && options?.count !== undefined) {
    return `${options.count} chars`
  }
  if (key === 'chat:tool.failed' && options?.label !== undefined) {
    return `${options.label} failed`
  }
  if (key === 'chat:tool.failureHint.repeatRead' && options?.title !== undefined) {
    return `Already read ${options.title}`
  }
  if (key === 'chat:tool.failureHint.authorEdited' && options?.title !== undefined) {
    return `Kept edits in ${options.title}`
  }
  if (key === 'chat:tool.renamed' && options?.to !== undefined) {
    return `Renamed ${options.from} to ${options.to}`
  }
  if (key === 'chat:tool.chapterNumber' && options?.chapter !== undefined) {
    return `Chapter ${options.chapter}`
  }
  if (key === 'chat:tool.chapterCreated' && options?.chapter !== undefined) {
    return `Created chapter ${options.chapter}`
  }
  if (key === 'chat:tool.chapterReplaced' && options?.chapter !== undefined) {
    return `Replaced chapter ${options.chapter}`
  }
  if (key === 'chat:tool.chapterReplacedPrevious' && options?.size !== undefined) {
    return `Replaced the previous ${options.size}`
  }

  const translations: Record<string, string> = {
    'chat:tool.created': 'Created',
    'chat:tool.updated': 'Updated',
    'chat:tool.deleted': 'Deleted',
    'chat:tool.files_found': 'Files found',
    'chat:tool.failed': 'Tool failed',
    'chat:tool.no_files_found': 'No files found',
    'chat:tool.edit_success': 'Edit success',
    'chat:tool.edit_touched_author_edits': 'Changed text you edited yourself',
    'chat:tool.edit_restored_author_deletion': 'Put back a paragraph you deleted; you can undo this',
    'chat:tool.view_edit_details': 'View edit details',
    'chat:tool.status_updated': 'Status updated',
    'chat:tool.processing_ellipsis': "Processing…",
    'chat:tool.create_file': 'Create file',
    'chat:tool.update_file': 'Update file',
    'chat:tool.edit_file': 'Edit file',
    'chat:tool.write_chapter': 'Write chapter',
    'chat:tool.chapterCreatedUnnumbered': 'Created chapter',
    'chat:tool.chapterReplacedUnnumbered': 'Replaced chapter',
    'chat:tool.delete_file': 'Delete file',
    'chat:tool.query_files': 'Query files',
    'chat:tool.read_files': 'Read files',
    'chat:tool.search_files': 'Search files',
    'chat:tool.batch_replace_files': 'Batch replace',
    'chat:tool.no_files_read': 'No files read',
    'chat:tool.no_search_matches': 'No matches found',
    'chat:tool.search_matches_one_in_one': 'Found 1 match in 1 file',
    'chat:tool.batch_replace_preview_badge': 'Preview',
    'chat:tool.batch_replace_preview_pending': 'Previewing matches',
    'chat:tool.batch_replace_preview_none': 'Preview: no matches to replace',
    'chat:tool.batch_replace_preview_one_in_one': 'Preview: 1 match in 1 file',
    'chat:tool.batch_replace_no_changes': 'No matching text found; no files changed',
    'chat:tool.batch_replace_done_one_in_one': 'Replaced 1 match in 1 file',
    'chat:tool.update_project': 'Update project',
    'chat:tool.load_skill': 'Load skill',
    'chat:tool.read_skill_resource': 'Read skill resource',
    'chat:fileType.outline': 'Outline',
    'chat:fileType.draft': 'Draft',
    'chat:fileType.character': 'Character',
    'chat:fileType.lore': 'Lore',
    'chat:fileType.snippet': 'Snippet',
    'chat:fileType.script': 'Script',
    'chat:fileType.document': 'Document',
    'chat:fileType.default': 'File',
    'chat:edit.replace': 'Replace',
    'chat:edit.append': 'Append',
    'chat:edit.prepend': 'Prepend',
    'chat:edit.label_delete': 'Delete',
    'chat:edit.label_add': 'Add',
    'chat:edit.append_content': 'Append content',
    'chat:edit.prepend_content': 'Prepend content',
    'chat:edit.insert_content': 'Insert content',
    'chat:edit.insert_after': 'Insert after',
    'chat:edit.insert_before': 'Insert before',
    'chat:edit.insert_after_label': 'After',
    'chat:edit.insert_before_label': 'Before',
    'chat:edit.delete_content': 'Delete content',
    'common:undo': 'Undo',
    'common:retry': 'Retry',
    'common:copy': 'Copy',
    'common:view': 'View',
    'common:separator': ', ',
    'chat:fileDefault': 'Untitled',
    'chat:tool.query_files_default': 'Query files',
    'chat:project.field.summary': 'Summary',
    'chat:project.field.current_phase': 'Current phase',
    'chat:project.field.writing_style': 'Writing style',
    'chat:project.field.notes': 'Notes',
    'chat:tool.complete_suffix': ' complete',
    'chat:tool.undo_edit': 'Undo edit',
  }
  return translations[key] || key
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: mockT,
  }),
}))

// ToolResultCard imports the real i18n singleton (for i18n.language in the
// quote helper); stub it so the module's initReactI18next side effect does not
// run under the partial react-i18next mock.
const i18nStub = vi.hoisted(() => ({ language: 'zh' }))
vi.mock('../../lib/i18n', () => ({
  default: i18nStub,
}))

describe('ToolResultCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Tool Call (pending state)', () => {
    it('renders pending tool call', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="create_file"
          isPending={true}
          result={{ title: 'Test File', file_type: 'draft' }}
        />
      )
      expect(screen.getByText(/Create file/i)).toBeInTheDocument()
    })

    it('shows processing indicator for pending tools', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="create_file"
          isPending={true}
        />
      )
      expect(screen.getByText("Processing…")).toBeInTheDocument()
    })

    it('displays tool description for create_file', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="create_file"
          isPending={true}
          result={{ title: 'Chapter 1', file_type: 'draft' }}
        />
      )
      expect(screen.getByText(/Chapter 1/)).toBeInTheDocument()
    })

    it('names the chapter number and title for write_chapter', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="write_chapter"
          isPending={true}
          result={{ chapter: 3, title: '雨夜', file_type: 'draft' }}
        />
      )
      expect(screen.getByText('Write chapter')).toBeInTheDocument()
      expect(screen.getByText(/Chapter 3 「雨夜」/)).toBeInTheDocument()
      expect(screen.queryByText(/write_chapter/)).not.toBeInTheDocument()
    })

    it('displays query description for query_files', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="query_files"
          isPending={true}
          result={{ file_type: 'character' }}
        />
      )
      expect(screen.getByText('Query files')).toBeInTheDocument()
      expect(screen.getByText('Query Character')).toBeInTheDocument()
    })

    it('displays pending descriptions for read_files, search_files, and batch_replace_files', () => {
      const { rerender } = render(
        <ToolResultCard
          type="tool_call"
          toolName="read_files"
          isPending={true}
          result={{ ids: ['f1', 'f2'], section: '第三幕' }}
        />
      )
      expect(screen.getByText('Read files')).toBeInTheDocument()
      expect(screen.getByText(/2 files · 「第三幕」/)).toBeInTheDocument()
      expect(screen.queryByText(/read_files/)).not.toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_call"
          toolName="search_files"
          isPending={true}
          result={{ query: '沈长明', file_type: 'draft' }}
        />
      )
      expect(screen.getByText('Search files')).toBeInTheDocument()
      expect(screen.getByText(/Draft · 「沈长明」/)).toBeInTheDocument()
      expect(screen.queryByText(/search_files/)).not.toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_call"
          toolName="batch_replace_files"
          isPending={true}
          result={{ dry_run: true, file_ids: ['f1', 'f2', 'f3'] }}
        />
      )
      expect(screen.getByText('Batch replace')).toBeInTheDocument()
      expect(screen.getByText(/Previewing matches · 3 files/)).toBeInTheDocument()
      expect(screen.queryByText(/batch_replace_files/)).not.toBeInTheDocument()
    })
  })

  describe('Tool Result (success state)', () => {
    it.each(['create_file', 'update_file'])('opens the successful %s title with pointer and keyboard', async toolName => {
      const user = userEvent.setup()
      const onOpenFile = vi.fn()
      render(<ToolResultCard type="tool_result" toolName={toolName}
        result={{ data: { id: 'file-1', title: 'A long chapter title', file_type: 'draft' } }}
        onOpenFile={onOpenFile} />)
      const title = screen.getByRole('button', { name: /A long chapter title/ })
      await user.click(title)
      await user.keyboard('{Enter} ')
      expect(onOpenFile.mock.calls).toEqual(Array(3).fill(['file-1', 'draft', 'A long chapter title']))
    })

    it.each([undefined, '', '  ', 7])('keeps a title without a reliable ID as text (%s)', id => {
      render(<ToolResultCard type="tool_result" toolName="create_file"
        result={{ data: { id, title: 'Older record' } }} onOpenFile={vi.fn()} />)
      expect(screen.getByText('Older record')).toBeInTheDocument()
      expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })

    it.each([
      { type: 'tool_call' as const, isPending: true },
      { type: 'tool_call' as const, interrupted: true },
      { type: 'tool_result' as const, error: 'failed' },
      { type: 'tool_result' as const, isPending: true },
      { type: 'tool_result' as const, interrupted: true },
    ])('does not open a failed or unfinished result (%j)', props => {
      render(<ToolResultCard {...props} toolName="create_file"
        result={{ id: 'file-1', title: 'Unfinished' }} onOpenFile={vi.fn()} />)
      expect(screen.queryByRole('button')).not.toBeInTheDocument()
    })

    // Payload shapes copied from the server (mcp_tools._write_chapter_* and
    // StreamAdapter._complete_chapter_replace; see test_loop_write_chapter.py).
    const armedReplace = {
      id: 'ch-2', title: '第2章 旧城', file_type: 'draft', chapter: 2, action: 'replaced',
      awaiting_body: true, previous_length: 2400, previous_word_count: 2400,
      result: '覆盖已有的第2章《第2章 旧城》（原2400字）：下一次回复写的正文会整章替换它，标题沿用（要改标题用 rename_file）。',
    }
    const { awaiting_body: _awaiting, ...armedCard } = armedReplace
    const savedReplace = {
      ...armedCard, mutation_applied: true, body_saved: true, new_length: 1800, new_word_count: 1800,
      updated_at: '2026-11-16T08:00:00Z', result: '覆盖了已有的第2章（原2400字），现在1800字。',
      undo: { before_version_number: 4, expected_after_updated_at: '2026-11-16T08:00:00Z' },
    }

    it('says a write_chapter result created a new chapter and opens it', async () => {
      const user = userEvent.setup()
      const onOpenFile = vi.fn()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ data: {
            id: 'ch-3', title: '第3章 雨夜', file_type: 'draft', content: '', chapter: 3,
            action: 'created', awaiting_body: true, result: '新建了第3章《第3章 雨夜》。',
          } }}
          onOpenFile={onOpenFile}
        />
      )
      expect(screen.getByText('Created chapter 3')).toBeInTheDocument()
      expect(screen.queryByText(/Replac/)).not.toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: /第3章 雨夜/ }))
      expect(onOpenFile).toHaveBeenCalledWith('ch-3', 'draft', '第3章 雨夜')
    })

    it.each([
      ['create_file', { id: 'ch-new', title: '新章', file_type: 'draft', content: '正文' }],
      ['write_chapter', { id: 'ch-new', title: '新章', file_type: 'draft', chapter: 1, action: 'created', body_saved: true, awaiting_body: false, new_word_count: 2 }],
    ])('does not offer Undo on a newly created %s card', (toolName, data) => {
      const undo = {
        before_version_number: 1,
        expected_after_updated_at: '2026-11-19T08:00:00Z',
        expected_after_content_sha256: 'a'.repeat(64),
      }
      render(<ToolResultCard type="tool_result" toolName={toolName} result={{ data: { ...data, undo } }} onUndo={vi.fn()} />)

      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    })

    it('says which chapter a saved replacement overwrote, the old and new length, and offers undo', async () => {
      const user = userEvent.setup()
      const onUndo = vi.fn()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={savedReplace}
          onUndo={onUndo}
          toolUseId="wc-1"
          chatMessageId="msg-1"
        />
      )
      expect(screen.getByText('Replaced chapter 2')).toBeInTheDocument()
      expect(screen.getByText('Replaced the previous 2.4k')).toBeInTheDocument()
      expect(screen.getByText('1.8k')).toBeInTheDocument()
      expect(screen.queryByText(/Created/)).not.toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: /undo/i }))
      expect(onUndo).toHaveBeenCalledWith({
        fileId: 'ch-2',
        beforeVersionNumber: 4,
        expectedAfterUpdatedAt: '2026-11-16T08:00:00Z',
        chatMessageId: 'msg-1',
        toolUseId: 'wc-1',
      })
    })

    it('shows an undone chapter replacement as undone without another undo', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ ...savedReplace, undo: { ...savedReplace.undo, undone: true } }}
          onUndo={vi.fn()}
        />
      )
      expect(screen.getByText('chat:tool.edit_undone')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    })

    it.each(['write_chapter', 'edit_file'])(
      'disables undo on a %s card while a later AI write to its file is in place, and says to undo that first',
      async (toolName) => {
        const user = userEvent.setup()
        const { UndoBlockedContext, undoneEditKey } = await import('../../lib/undoneEdits')
        const onUndo = vi.fn()
        const result = toolName === 'write_chapter' ? savedReplace : { data: { id: 'ch-2', undo: savedReplace.undo, details: [] } }
        const key = undoneEditKey({ fileId: 'ch-2', beforeVersionNumber: 4, expectedAfterUpdatedAt: '2026-11-16T08:00:00Z' })
        const card = (blocked: ReadonlySet<string>) => (
          <UndoBlockedContext.Provider value={blocked}>
            <ToolResultCard
              type="tool_result"
              toolName={toolName}
              result={result}
              onUndo={onUndo}
              toolUseId="tool-1"
              chatMessageId="assistant-1"
            />
          </UndoBlockedContext.Provider>
        )
        const { rerender } = render(card(new Set([key])))
        const blockedButton = screen.getByRole('button', { name: /undo/i })
        expect(blockedButton).toBeDisabled()
        expect(blockedButton).toHaveAccessibleDescription('chat:tool.undoLaterFirst')
        await user.click(blockedButton)
        expect(onUndo).not.toHaveBeenCalled()

        // The later change was undone: this one can be undone now.
        rerender(card(new Set()))
        expect(screen.queryByText('chat:tool.undoLaterFirst')).not.toBeInTheDocument()
        await user.click(screen.getByRole('button', { name: /undo/i }))
        expect(onUndo).toHaveBeenCalledTimes(1)
      },
    )

    it('passes the content hash of a replacement with its undo target', async () => {
      const user = userEvent.setup()
      const onUndo = vi.fn()
      const hash = 'b'.repeat(64)
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ ...savedReplace, undo: { ...savedReplace.undo, expected_after_content_sha256: hash } }}
          onUndo={onUndo}
          toolUseId="wc-1"
          chatMessageId="msg-1"
        />
      )
      await user.click(screen.getByRole('button', { name: /undo/i }))
      expect(onUndo).toHaveBeenCalledWith(expect.objectContaining({ expectedAfterContentSha256: hash }))
    })

    it('says episode instead of chapter for a screenplay write_chapter card', () => {
      const { unmount } = render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ data: { id: 'ep-1', title: '第1集 开场', file_type: 'script', chapter: 1, action: 'created' } }}
        />
      )
      expect(mockT).toHaveBeenCalledWith('chat:tool.episodeCreated', { chapter: 1 })
      expect(mockT).not.toHaveBeenCalledWith('chat:tool.chapterCreated', expect.anything())
      unmount()

      mockT.mockClear()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ ...savedReplace, file_type: 'script' }}
        />
      )
      expect(mockT).toHaveBeenCalledWith('chat:tool.episodeReplaced', { chapter: 2 })
      expect(mockT).not.toHaveBeenCalledWith('chat:tool.chapterReplaced', expect.anything())
    })

    it('names the episode in a pending screenplay write_chapter call', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="write_chapter"
          isPending={true}
          result={{ chapter: 3, title: '雨夜', file_type: 'script' }}
        />
      )
      expect(mockT).toHaveBeenCalledWith('chat:tool.write_episode')
      expect(mockT).toHaveBeenCalledWith('chat:tool.episodeNumber', { chapter: 3 })
      expect(mockT).not.toHaveBeenCalledWith('chat:tool.chapterNumber', expect.anything())
    })

    it('does not claim an armed replacement is done or new', () => {
      render(<ToolResultCard type="tool_result" toolName="write_chapter" result={armedReplace} onUndo={vi.fn()} />)
      expect(screen.getByText('chat:tool.chapterReplacing')).toBeInTheDocument()
      expect(screen.queryByText(/Created|Replaced chapter/)).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    })

    it('says a refused replacement was not saved and the original is unchanged', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ ...armedCard, mutation_applied: false, body_saved: false }}
          onUndo={vi.fn()}
        />
      )
      expect(screen.getByText('chat:tool.chapterReplaceNotSaved')).toBeInTheDocument()
      expect(screen.queryByText(/Created|Replaced/)).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    })

    it('says an existing chapter was left unchanged for action "exists"', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ data: {
            id: 'ch-1', title: '第1章 雨夜', file_type: 'draft', chapter: 1, action: 'exists',
            result: '第1章《第1章 雨夜》已经有文件，没有改动。',
          } }}
        />
      )
      expect(screen.getByText('chat:tool.chapterExists')).toBeInTheDocument()
      expect(screen.queryByText(/Created|Replaced/)).not.toBeInTheDocument()
    })

    it('keeps a write_chapter result without a usable chapter number readable', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ ...savedReplace, chapter: 'one', previous_word_count: 0, undo: undefined }}
        />
      )
      expect(screen.getByText('Replaced chapter')).toBeInTheDocument()
      expect(screen.queryByText(/Replaced the previous/)).not.toBeInTheDocument()
    })

    it('renders create_file success', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{
            data: {
              title: 'New File',
              file_type: 'draft',
              content: 'File content here',
            },
          }}
        />
      )
      expect(screen.getByText(/Created Draft/)).toBeInTheDocument()
      expect(screen.getByText('New File')).toBeInTheDocument()
    })

    it('shows the word count for created files', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{
            data: {
              title: 'New File',
              file_type: 'draft',
              content: '字'.repeat(1500),
            },
          }}
        />
      )
      expect(screen.getByText(/1.5k/)).toBeInTheDocument()
    })

    it('prefers the saved body receipt count on both live and reloaded create cards', () => {
      const saved = {
        title: 'Streamed file', file_type: 'draft', content: '', body_saved: true,
        new_word_count: 1234,
      }
      const view = render(<ToolResultCard type="tool_result" toolName="create_file" result={{ data: saved }} />)
      expect(screen.getByText('1.2k')).toBeInTheDocument()

      view.unmount()
      render(<ToolResultCard type="tool_result" toolName="create_file" result={{ data: { ...saved } }} />)
      expect(screen.getByText('1.2k')).toBeInTheDocument()
    })

    it('counts words the way the editor does, not raw characters', () => {
      // 1200 Chinese characters plus punctuation and blank lines: 2400 characters.
      const content = '你好，世界！\n\n'.repeat(300)
      expect(content.length).toBe(2400)
      render(
        <ToolResultCard
          type="tool_result"
          toolName="update_file"
          result={{ data: { title: 'Chapter', content } }}
        />
      )
      expect(screen.getByText('1.2k')).toBeInTheDocument()
      expect(screen.queryByText('2.4k')).not.toBeInTheDocument()
    })

    it('renders update_file success', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="update_file"
          result={{
            data: {
              title: 'Updated File',
              content: 'Updated content',
            },
          }}
        />
      )
      expect(screen.getByText('Updated')).toBeInTheDocument()
      expect(screen.getByText(/Updated File/)).toBeInTheDocument()
    })

    it('renders delete_file success', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="delete_file"
          result={{}}
        />
      )
      expect(screen.getByText('Deleted')).toBeInTheDocument()
    })

    it('renders query_files success with files', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="query_files"
          result={{
            data: [
              { title: 'File 1', file_type: 'draft' },
              { title: 'File 2', file_type: 'character' },
            ],
          }}
        />
      )
      expect(screen.getByText('Files found')).toBeInTheDocument()
    })

    it('renders query_files success with no files', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="query_files"
          result={{ data: [] }}
        />
      )
      expect(screen.getByText('No files found')).toBeInTheDocument()
    })

    it('renders read_files success with clickable file rows and empty fallback', async () => {
      const user = userEvent.setup()
      const onOpenFile = vi.fn()
      const { rerender } = render(
        <ToolResultCard
          type="tool_result"
          toolName="read_files"
          onOpenFile={onOpenFile}
          result={{
            data: {
              files: [
                { id: 'f1', title: '第一章 雨夜', file_type: 'draft' },
                { id: 'f2', title: '林舟', file_type: 'character' },
              ],
            },
          }}
        />
      )
      expect(screen.getByText('Read 2 files')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: /第一章 雨夜/ }))
      expect(onOpenFile).toHaveBeenCalledWith('f1', 'draft', '第一章 雨夜')

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="read_files"
          result={{ data: { files: [] } }}
        />
      )
      expect(screen.getByText('No files read')).toBeInTheDocument()
    })

    it('renders search_files success in both match mode and filtered list mode', () => {
      const { rerender } = render(
        <ToolResultCard
          type="tool_result"
          toolName="search_files"
          result={{
            data: {
              query: '青铜镜',
              total_matches: 5,
              matched_files: 2,
              files: [
                { id: 'f1', title: '第一章', file_type: 'draft', match_count: 3 },
                { id: 'f2', title: '第五章', file_type: 'draft', match_count: 2 },
              ],
            },
          }}
        />
      )
      expect(screen.getByText('Found 5 matches across 2 files')).toBeInTheDocument()
      expect(screen.getByText('「青铜镜」')).toBeInTheDocument()
      expect(screen.getByText('3 matches')).toBeInTheDocument()
      expect(screen.getByText('2 matches')).toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="search_files"
          result={{ data: { query: '不存在', total_matches: 0, matched_files: 0, files: [] } }}
        />
      )
      expect(screen.getByText('No matches found')).toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="search_files"
          result={{ data: { files: [{ id: 'f3', title: '世界观', file_type: 'lore' }] } }}
        />
      )
      expect(screen.getByText('Files found')).toBeInTheDocument()
      expect(screen.getByText('世界观')).toBeInTheDocument()
    })

    it('renders batch_replace_files dry_run preview without undo controls or false modification state', () => {
      const onUndo = vi.fn()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          chatMessageId="msg-1"
          toolUseId="tu-1"
          onUndo={onUndo}
          result={{
            data: {
              dry_run: true,
              total_matches: 7,
              files_matched: 2,
              files_modified: 0,
              modified_files: [
                {
                  id: 'f1',
                  title: '第一章',
                  file_type: 'draft',
                  replacements: 4,
                  undo: { before_version_number: 1, expected_after_updated_at: '2026-11-26T00:00:00Z' },
                },
                { id: 'f2', title: '第二章', file_type: 'draft', replacements: 3 },
              ],
            },
          }}
        />
      )
      expect(screen.getByText('Preview: 7 matches across 2 files')).toBeInTheDocument()
      expect(screen.getByText('Preview')).toBeInTheDocument()
      expect(screen.getByText('4 matches')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument()
    })

    it('aligns previewed file count and replacement count when batch_replace_files dry_run is truncated', () => {
      const previewFiles = Array.from({ length: 30 }, (_, index) => ({
        id: `f-${index + 1}`,
        title: `第${index + 1}章`,
        file_type: 'draft',
        replacements: index < 3 ? 2 : 1,
      }))

      const { rerender } = render(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          result={{
            data: {
              dry_run: true,
              mutation_applied: false,
              matched_files: 45,
              modified_files: 0,
              total_replacements: 33,
              files: previewFiles,
              remaining_count: 15,
              truncated: true,
            },
          }}
        />
      )

      expect(
        screen.getByText('Preview: 33 matches in the first 30 files (45 files matched in total)'),
      ).toBeInTheDocument()
      expect(screen.queryByText('Preview: 33 matches across 45 files')).not.toBeInTheDocument()
      expect(screen.getByText('15 more files remaining')).toBeInTheDocument()
      expect(screen.getByText('View affected files (30)')).toBeInTheDocument()

      // Single previewed file truncated out of 5 matched files (singular & plural matches)
      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          result={{
            data: {
              dry_run: true,
              matched_files: 5,
              total_replacements: 1,
              files: [{ id: 'f-1', title: '第1章', file_type: 'draft', replacements: 1 }],
              remaining_count: 4,
              truncated: true,
            },
          }}
        />
      )
      expect(
        screen.getByText('Preview: 1 match in the first file (5 files matched in total)'),
      ).toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          result={{
            data: {
              dry_run: true,
              matched_files: 5,
              total_replacements: 3,
              files: [{ id: 'f-1', title: '第1章', file_type: 'draft', replacements: 3 }],
              remaining_count: 4,
              truncated: true,
            },
          }}
        />
      )
      expect(
        screen.getByText('Preview: 3 matches in the first file (5 files matched in total)'),
      ).toBeInTheDocument()
    })

    it('renders batch_replace_files real execution with counts, per-file undo, failed files, and remaining files', async () => {
      const user = userEvent.setup()
      const onUndo = vi.fn()
      const onOpenFile = vi.fn()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          chatMessageId="msg-2"
          toolUseId="tu-2"
          onUndo={onUndo}
          onOpenFile={onOpenFile}
          result={{
            data: {
              dry_run: false,
              total_matches: 4,
              files_matched: 3,
              files_modified: 2,
              remaining_files: 5,
              modified_files: [
                {
                  id: 'f1',
                  title: '第一章',
                  file_type: 'draft',
                  replacements: 3,
                  undo: { before_version_number: 2, expected_after_updated_at: '2026-11-26T01:00:00Z' },
                },
                { id: 'f2', title: '第二章', file_type: 'draft', replacements: 1 },
              ],
              failed_files: [
                { id: 'f3', title: '第三章', error_type: 'stale_content' },
              ],
            },
          }}
        />
      )
      expect(screen.getByText('Replaced 4 matches across 2 files')).toBeInTheDocument()
      expect(screen.getByText('1 files skipped')).toBeInTheDocument()
      expect(screen.getByText('5 more files remaining')).toBeInTheDocument()
      expect(screen.getByText('3 replacements')).toBeInTheDocument()
      expect(screen.getByText('第三章')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: /第一章/ }))
      expect(onOpenFile).toHaveBeenCalledWith('f1', 'draft', '第一章')

      await user.click(screen.getByRole('button', { name: 'Undo' }))
      expect(onUndo).toHaveBeenCalledWith({
        fileId: 'f1',
        beforeVersionNumber: 2,
        expectedAfterUpdatedAt: '2026-11-26T01:00:00Z',
        chatMessageId: 'msg-2',
        toolUseId: 'tu-2',
      })
    })

    it('renders batch_replace_files using Track A backend field names (files, modified_files count, total_replacements, skipped_files, remaining_count)', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="batch_replace_files"
          result={{
            data: {
              dry_run: false,
              mutation_applied: true,
              matched_files: 4,
              modified_files: 2,
              total_replacements: 6,
              files: [
                { id: 'f1', title: '第一章', file_type: 'draft', replacements: 4 },
                { id: 'f2', title: '第二章', file_type: 'draft', replacements: 2 },
              ],
              skipped_files: [
                { id: 'f3', title: '第三章', reason: 'stale_content' },
              ],
              remaining_files: [{ id: 'f4', title: '第四章', file_type: 'draft' }],
              remaining_count: 1,
            },
          }}
        />
      )
      expect(screen.getByText('Replaced 6 matches across 2 files')).toBeInTheDocument()
      expect(screen.getByText('1 files skipped')).toBeInTheDocument()
      expect(screen.getByText('1 more files remaining')).toBeInTheDocument()
      expect(screen.getByText('4 replacements')).toBeInTheDocument()
      expect(screen.getByText('第三章')).toBeInTheDocument()
      expect(screen.getByText(/chat:tool\.failureHint\.authorChanged/)).toBeInTheDocument()
    })

    it('renders pending search_files and batch_replace_files cards with plural args.file_types and legacy file_type', () => {
      const { rerender } = render(
        <ToolResultCard
          type="tool_call"
          toolName="search_files"
          isPending={true}
          result={{ query: '青铜镜', file_types: ['draft', 'outline'] }}
        />
      )
      expect(screen.getByText('Draft / Outline · 「青铜镜」')).toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_call"
          toolName="search_files"
          isPending={true}
          result={{ file_types: ['character'] }}
        />
      )
      expect(screen.getByText('Query Character')).toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_call"
          toolName="search_files"
          isPending={true}
          result={{ file_type: 'outline' }}
        />
      )
      expect(screen.getByText('Query Outline')).toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_call"
          toolName="batch_replace_files"
          isPending={true}
          result={{ old_text: '林默', new_text: '沈砚', file_types: ['draft'], dry_run: true }}
        />
      )
      expect(screen.getByText(/Draft/)).toBeInTheDocument()
    })

    it('renders edit_file success with details', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: 'file-1',
              details: [
                { op: 'replace', old_preview: 'old text', new_preview: 'new text' },
              ],
            },
          }}
        />
      )
      expect(screen.getByText('Edit success')).toBeInTheDocument()
    })

    it('says when an edit rewrote text the author wrote and opens the details', () => {
      const { container } = render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: 'file-1',
              author_edits_touched: [{ before: '你也配问这个？', after: '你也配？' }],
              details: [
                // Fuzzy match: the card shows the text that was actually replaced, not the model's quote.
                { op: 'replace', old_preview: '你配吗。', matched_original: '你也配问这个？', new_preview: '你也配？' },
              ],
            },
          }}
        />
      )
      expect(screen.getByText('Changed text you edited yourself')).toBeInTheDocument()
      expect(container.querySelector('details')?.open).toBe(true)
      expect(screen.getByText('你也配问这个？')).toBeInTheDocument()
      expect(screen.queryByText('你配吗。')).not.toBeInTheDocument()
    })

    it('shows a rename as old and new titles, without any file body', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="rename_file"
          result={{ data: { id: 'f1', title: '第一卷 雨夜', previous_title: '合集', mutation_applied: true } }}
        />
      )
      expect(screen.getByText('Renamed 合集 to 第一卷 雨夜')).toBeInTheDocument()
    })

    it('offers one-click restore on a soft-deleted file card and marks it restored', async () => {
      const user = userEvent.setup()
      const onRestoreDeletion = vi.fn().mockResolvedValue(undefined)
      render(
        <ToolResultCard
          type="tool_result"
          toolName="delete_file"
          chatMessageId="msg-del-1"
          toolUseId="call-del-1"
          onRestoreDeletion={onRestoreDeletion}
          result={{ data: { id: 'file-1', title: '废稿', file_type: 'draft', restored: false, mutation_applied: true } }}
        />,
      )
      expect(screen.getByText('Deleted 「废稿」')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'chat:tool.restoreDeleted' }))
      await waitFor(() =>
        expect(onRestoreDeletion).toHaveBeenCalledWith('file-1', {
          chatMessageId: 'msg-del-1',
          toolUseId: 'call-del-1',
        }),
      )
      expect(await screen.findByText('chat:tool.deletionRestored')).toBeInTheDocument()
    })

    it('keeps the restore button available when restore rejects', async () => {
      const user = userEvent.setup()
      const onRestoreDeletion = vi.fn().mockRejectedValue(new Error('conflict'))
      render(
        <ToolResultCard
          type="tool_result"
          toolName="delete_file"
          onRestoreDeletion={onRestoreDeletion}
          result={{ data: { id: 'file-1', restored: false, mutation_applied: true } }}
        />,
      )
      await user.click(screen.getByRole('button', { name: 'chat:tool.restoreDeleted' }))
      await waitFor(() => {
        expect(onRestoreDeletion).toHaveBeenCalledTimes(1)
        expect(screen.getByRole('button', { name: 'chat:tool.restoreDeleted' })).toBeEnabled()
      })
    })

    it('says when an edit put back a paragraph the author deleted, not that it changed their text', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: 'file-1',
              author_edits_touched: [{ before: '', after: '她把信烧了。', restored_deleted: true, deleted_in_version: 2 }],
              details: [{ op: 'append', new_preview: '她把信烧了。' }],
            },
          }}
        />
      )
      expect(screen.getByText('Put back a paragraph you deleted; you can undo this')).toBeInTheDocument()
      expect(screen.queryByText('Changed text you edited yourself')).not.toBeInTheDocument()
    })

    it('says when a parallel edit_file subtask rewrote text the author wrote', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            data: {
              total_tasks: 2,
              completed: 2,
              failed: 0,
              tasks: [
                { id: 't1', type: 'edit_file', status: 'completed', description: '第2集',
                  result: { status: 'success', data: { author_edits_touched: [{ before: 'a', after: 'b' }] } } },
                { id: 't2', type: 'edit_file', status: 'completed', description: '第3集',
                  result: { status: 'success', data: {} } },
              ],
            },
          }}
        />
      )
      expect(screen.getAllByText('Changed text you edited yourself')).toHaveLength(1)
    })

    it('shows a stale-content refusal as redoing on the author\'s text, not as a failure', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ status: 'error', error_type: 'stale_content', error: 'internal text for the model' }}
        />
      )
      expect(screen.queryByText(/failed/i)).not.toBeInTheDocument()
      expect(screen.queryByText('internal text for the model')).not.toBeInTheDocument()
    })

    it('keeps an ordinary edit card collapsed without the note', () => {
      const { container } = render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ data: { id: 'file-1', details: [{ op: 'append', text_preview: '续写' }] } }}
        />
      )
      expect(screen.queryByText('Changed text you edited yourself')).not.toBeInTheDocument()
      expect(container.querySelector('details')?.open).toBe(false)
    })

    it.each([
      ['edit_file', { id: 'file-1', details: [] }],
      ['write_chapter', { id: 'file-1', action: 'replaced', body_saved: true, mutation_applied: true, chapter: 2 }],
    ])('offers no undo on a %s card that has no saved message yet', (toolName, data) => {
      const undo = { before_version_number: 7, expected_after_updated_at: '2026-10-06T12:34:56.000Z' }
      const { rerender } = render(
        <ToolResultCard type="tool_result" toolName={toolName} result={{ data: { ...data, undo } }} onUndo={vi.fn()} toolUseId="tool-1" />
      )
      // 还在流式中：没有落库的消息 id，撤销记录存不下来，刷新后会丢。
      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()

      rerender(
        <ToolResultCard
          type="tool_result"
          toolName={toolName}
          result={{ data: { ...data, undo } }}
          onUndo={vi.fn()}
          toolUseId="tool-1"
          chatMessageId="assistant-1"
        />
      )
      expect(screen.getByRole('button', { name: /undo/i })).toBeInTheDocument()
    })

    it('shows undo only for an edit_file result with immutable provenance', async () => {
      const user = userEvent.setup()
      const onUndo = vi.fn()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: 'file-1',
              undo: {
                before_version_number: 7,
                expected_after_updated_at: '2026-10-06T12:34:56.000Z',
              },
              details: [{ op: 'replace', old_preview: 'old', new_preview: 'new' }],
            },
          }}
          onUndo={onUndo}
          toolUseId="tool-edit-1"
          chatMessageId="assistant-1"
        />
      )

      const undoButton = screen.getByRole('button', { name: /undo/i })
      await user.click(undoButton)

      expect(onUndo).toHaveBeenCalledWith({
        fileId: 'file-1',
        beforeVersionNumber: 7,
        expectedAfterUpdatedAt: '2026-10-06T12:34:56.000Z',
        chatMessageId: 'assistant-1',
        toolUseId: 'tool-edit-1',
      })
    })

    it('names its tool call and saved message when undoing, so the server can mark this card', async () => {
      const user = userEvent.setup()
      const onUndo = vi.fn()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ data: { id: 'file-1', undo: { before_version_number: 7, expected_after_updated_at: '2026-10-06T12:34:56.000Z' }, details: [] } }}
          onUndo={onUndo}
          toolUseId="tool-edit-1"
          chatMessageId="assistant-1"
        />
      )

      await user.click(screen.getByRole('button', { name: /undo/i }))

      expect(onUndo).toHaveBeenCalledWith({
        fileId: 'file-1',
        beforeVersionNumber: 7,
        expectedAfterUpdatedAt: '2026-10-06T12:34:56.000Z',
        chatMessageId: 'assistant-1',
        toolUseId: 'tool-edit-1',
      })
    })

    it.each([
      ['recorded on the card after a refresh', true, false],
      ['undone in this chat', false, true],
    ])('shows an undone edit as undone without another undo (%s)', async (_label, recorded, live) => {
      const { UndoneEditsContext, undoneEditKey } = await import('../../lib/undoneEdits')
      const target = { fileId: 'file-1', beforeVersionNumber: 7, expectedAfterUpdatedAt: '2026-10-06T12:34:56.000Z' }
      const undone = new Set(live ? [undoneEditKey(target)] : [])
      render(
        <UndoneEditsContext.Provider value={undone}>
          <ToolResultCard
            type="tool_result"
            toolName="edit_file"
            result={{
              data: {
                id: 'file-1',
                undo: {
                  before_version_number: 7,
                  expected_after_updated_at: '2026-10-06T12:34:56.000Z',
                  ...(recorded ? { undone: true } : {}),
                },
                details: [],
              },
            }}
            onUndo={vi.fn()}
          />
        </UndoneEditsContext.Provider>
      )

      expect(screen.getByText('chat:tool.edit_undone')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    })

    it.each([
      ['missing descriptor', undefined],
      ['non-object descriptor', 'legacy'],
      ['non-positive version', { before_version_number: 0, expected_after_updated_at: '2026-10-06T12:34:56.000Z' }],
      ['fractional version', { before_version_number: 1.5, expected_after_updated_at: '2026-10-06T12:34:56.000Z' }],
      ['unsafe integer version', { before_version_number: Number.MAX_SAFE_INTEGER + 1, expected_after_updated_at: '2026-10-06T12:34:56.000Z' }],
      ['invalid timestamp', { before_version_number: 1, expected_after_updated_at: 'not-a-date' }],
      ['non-ISO timestamp', { before_version_number: 1, expected_after_updated_at: '0' }],
      ['nonexistent calendar date', { before_version_number: 1, expected_after_updated_at: '2026-02-31T12:00:00Z' }],
      ['invalid clock time', { before_version_number: 1, expected_after_updated_at: '2026-10-06T24:00:00Z' }],
      ['invalid offset hour', { before_version_number: 1, expected_after_updated_at: '2026-10-06T12:00:00+24:00' }],
      ['invalid offset minute', { before_version_number: 1, expected_after_updated_at: '2026-10-06T12:00:00+05:60' }],
    ])('hides undo for %s', (_label, undo) => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{ data: { id: 'file-1', undo, details: [] } }}
          onUndo={vi.fn()}
        />
      )

      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    })

    it('preserves an exact valid offset and microsecond token', async () => {
      const user = userEvent.setup()
      const onUndo = vi.fn()
      const token = '2026-10-06T12:34:56.123456+05:30'
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: 'file-1',
              undo: {
                before_version_number: 9,
                expected_after_updated_at: token,
              },
              details: [],
            },
          }}
          onUndo={onUndo}
          toolUseId="tool-edit-1"
          chatMessageId="assistant-1"
        />
      )

      await user.click(screen.getByRole('button', { name: /undo/i }))
      expect(onUndo).toHaveBeenCalledWith({
        fileId: 'file-1',
        beforeVersionNumber: 9,
        expectedAfterUpdatedAt: token,
        chatMessageId: 'assistant-1',
        toolUseId: 'tool-edit-1',
      })
    })

    it('hides undo when the file id is unusable', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: '   ',
              undo: {
                before_version_number: 1,
                expected_after_updated_at: '2026-10-06T12:34:56.000Z',
              },
              details: [],
            },
          }}
          onUndo={vi.fn()}
        />
      )

      expect(screen.queryByRole('button', { name: /undo/i })).not.toBeInTheDocument()
    })

    it('displays edit details when expanded', async () => {
      const user = userEvent.setup()
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: 'file-1',
              details: [
                {
                  op: 'replace',
                  old_preview: 'Old text that was replaced',
                  new_preview: 'New text that was added',
                },
              ],
            },
          }}
        />
      )

      // Click to expand details
      const summary = screen.getByText(/View edit details/)
      await user.click(summary)

      // Check that edit details are shown
      expect(screen.getByText(/Old text that was replaced/)).toBeInTheDocument()
      expect(screen.getByText(/New text that was added/)).toBeInTheDocument()
    })

    it('renders update_project success', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="update_project"
          result={{
            data: {
              updated_fields: ['summary', 'current_phase'],
            },
          }}
        />
      )
      expect(screen.getByText('Status updated')).toBeInTheDocument()
    })

    it('renders update_project success with nested project_status payload', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="update_project"
          result={{
            data: {
              project_status: {
                updated_fields: ['writing_style'],
              },
            },
          }}
        />
      )
      expect(screen.getByText('Status updated')).toBeInTheDocument()
      expect(screen.getByText('(Writing style)')).toBeInTheDocument()
    })
  })

  describe('Tool Result (error state)', () => {
    it('renders error state', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          error="Failed to create file"
        />
      )
      expect(screen.getByText(/Create file failed/)).toBeInTheDocument()
    })

    it('shows user message from result', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          error="System error"
          result={{ user_message: 'File already exists' }}
        />
      )
      expect(screen.getByText('File already exists')).toBeInTheDocument()
    })

    it('never shows the raw error when there is no user message', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          error="Edit 0: 锚点匹配到多个位置（id=file-9），请提供 occurrence=N"
        />
      )
      expect(screen.queryByText(/occurrence=N/)).not.toBeInTheDocument()
      expect(screen.getByText('chat:tool.failureHint.generic')).toBeInTheDocument()
    })

    it('falls back to the localized hint on non-Chinese UIs, since user_message is Chinese', () => {
      i18nStub.language = 'en'
      try {
        render(
          <ToolResultCard
            type="tool_result"
            toolName="edit_file"
            error="raw"
            result={{ data: { error_type: 'ambiguous_match', user_message: '要改的这句话出现了好几次' } }}
          />
        )
        expect(screen.queryByText('要改的这句话出现了好几次')).not.toBeInTheDocument()
        expect(screen.getByText('chat:tool.failureHint.ambiguous')).toBeInTheDocument()
      } finally {
        i18nStub.language = 'zh'
      }
    })

    it('reads user_message nested under data (stream adapter shape)', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          error="raw"
          result={{ data: { error_type: 'ambiguous_match', user_message: '要改的这句话出现了好几次' } }}
        />
      )
      expect(screen.getByText('要改的这句话出现了好几次')).toBeInTheDocument()
    })

    it.each([
      ['anchor_not_found', 'chat:tool.failureHint.notFound'],
      ['ambiguous_match', 'chat:tool.failureHint.ambiguous'],
      ['file_not_found', 'chat:tool.failureHint.fileMissing'],
      ['tool_not_found', 'chat:tool.failureHint.generic'],
      ['malformed_tool_result', 'chat:tool.failureHint.generic'],
    ])('maps error_type %s to a friendly hint', (errorType, expected) => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          error="Internal detail"
          result={{ error_type: errorType }}
        />
      )
      expect(screen.getByText(expected)).toBeInTheDocument()
      expect(screen.queryByText('Internal detail')).not.toBeInTheDocument()
    })

    it('says the AI kept the author\'s edits, in the UI language, instead of reporting a failure', () => {
      i18nStub.language = 'en'
      try {
        render(
          <ToolResultCard
            type="tool_result"
            toolName="edit_file"
            error="raw instructions for the model"
            result={{
              error_type: 'author_edit_protected',
              title: '第3章 三十年',
              user_message: '《第3章 三十年》有你手动改过的内容，AI 没有动它；需要改的话会先问你。',
            }}
          />
        )
        expect(screen.getByText('Kept edits in 第3章 三十年')).toBeInTheDocument()
        expect(screen.getByText('chat:tool.skippedTitle.authorEdited')).toBeInTheDocument()
        expect(screen.queryByText(/failed/)).not.toBeInTheDocument()
        expect(screen.queryByText(/手动改过/)).not.toBeInTheDocument()
      } finally {
        i18nStub.language = 'zh'
      }
    })

    it('shows the backend sentence for a kept author edit on Chinese UIs', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="delete_file"
          error="raw"
          result={{ data: { error_type: 'author_edit_protected', user_message: '《第3章》有你手动改过的内容，AI 没有动它；需要改的话会先问你。' } }}
        />
      )
      expect(screen.getByText('《第3章》有你手动改过的内容，AI 没有动它；需要改的话会先问你。')).toBeInTheDocument()
      expect(screen.getByText('chat:tool.skippedTitle.authorEdited')).toBeInTheDocument()
    })

    it.each([
      [{ error_type: 'author_edit_protected' }, 'chat:tool.failureHint.authorEditedUntitled', 'chat:tool.skippedTitle.authorEdited'],
      [{ error_type: 'author_edit_protection_unavailable' }, 'chat:tool.failureHint.protectionUnavailable', 'chat:tool.skippedTitle.protectionUnavailable'],
      [{ error_type: 'clarify_first' }, 'chat:tool.failureHint.clarifyFirst', 'chat:tool.skippedTitle.clarifyFirst'],
    ])('maps the intentional skip %j to its own hint and title', (result, hint, title) => {
      render(<ToolResultCard type="tool_result" toolName="edit_file" error="x" result={result} />)
      expect(screen.getByText(hint)).toBeInTheDocument()
      expect(screen.getByText(title)).toBeInTheDocument()
      expect(screen.queryByText(/failed/)).not.toBeInTheDocument()
    })

    it('names the file for a repeated read when the title is known', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="query_files"
          error="内容就在上文的工具结果里"
          result={{ error_type: 'repeated_read', title: '第一章' }}
        />
      )
      expect(screen.getByText('Already read 第一章')).toBeInTheDocument()
    })

    it('labels unknown tools generically instead of leaking the internal name', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="rewrite_chapter"
          error="x"
        />
      )
      expect(screen.getByText(/chat:tool.generic failed/)).toBeInTheDocument()
      expect(screen.queryByText(/rewrite_chapter/)).not.toBeInTheDocument()
    })
  })

  describe('File type icons', () => {
    it('shows outline icon', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{
            data: {
              title: 'Outline',
              file_type: 'outline',
              content: 'Content',
            },
          }}
        />
      )
      expect(screen.getByText('Outline')).toBeInTheDocument()
    })

    it('shows draft icon', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{
            data: {
              title: 'Draft',
              file_type: 'draft',
              content: 'Content',
            },
          }}
        />
      )
      expect(screen.getByText('Draft')).toBeInTheDocument()
    })

    it('shows character icon', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{
            data: {
              title: 'Character',
              file_type: 'character',
              content: 'Content',
            },
          }}
        />
      )
      expect(screen.getByText('Character')).toBeInTheDocument()
    })

    it('shows lore icon', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{
            data: {
              title: 'Lore',
              file_type: 'lore',
              content: 'Content',
            },
          }}
        />
      )
      expect(screen.getByText('Lore')).toBeInTheDocument()
    })

    it.each([
      ['script', 'Script'],
      ['document', 'Document'],
    ])('labels created %s files', (fileType, label) => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{ data: { title: 'Episode 1', file_type: fileType, content: 'Content' } }}
        />
      )
      expect(screen.getByText(label, { exact: false })).toBeInTheDocument()
    })
  })

  it('returns null for unknown types', () => {
    const { container } = render(
      <ToolResultCard type={'unknown' as 'tool_call' | 'tool_result' | 'conflict'} />
    )
    expect(container.firstChild).toBeNull()
  })

  describe('Tool Result (parallel_execute)', () => {
    const parallelResult = {
      execution_id: 'par-1',
      total_tasks: 2,
      completed: 1,
      failed: 1,
      any_failed: true,
      tasks: [
        {
          id: 't0',
          type: 'edit_file',
          description: '编辑第一章',
          status: 'completed',
          result: {},
          error: null,
        },
        {
          id: 't1',
          type: 'delete_file',
          description: '删除草稿',
          status: 'failed',
          result: null,
          error: 'File not found',
        },
      ],
    }

    it('renders each sub-task description', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={parallelResult}
        />
      )
      expect(screen.getByText('编辑第一章')).toBeInTheDocument()
      expect(screen.getByText('删除草稿')).toBeInTheDocument()
    })

    it('marks a failed sub-task as not completed without its raw error', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={parallelResult}
        />
      )
      expect(screen.getByText('chat:tool.taskNotCompleted')).toBeInTheDocument()
      expect(screen.queryByText('File not found')).not.toBeInTheDocument()
      expect(document.querySelector('.border-\\[hsl\\(var\\(--warning\\)\\)\\]')).not.toBeNull()
    })

    it("shows a failed sub-task's user_message when the tool provided one", () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            ...parallelResult,
            tasks: [
              parallelResult.tasks[0],
              {
                ...parallelResult.tasks[1],
                result: { status: 'error', user_message: '这个文件已经不存在了' },
              },
            ],
          }}
        />
      )
      expect(screen.getByText('这个文件已经不存在了')).toBeInTheDocument()
      expect(screen.queryByText('File not found')).not.toBeInTheDocument()
    })

    it('shows a stale-content sub-task as an intentional skip, not a failure', () => {
      const { container } = render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            ...parallelResult,
            tasks: [
              parallelResult.tasks[0],
              {
                ...parallelResult.tasks[1],
                type: 'edit_file',
                description: '改第二章',
                result: { status: 'error', error_type: 'stale_content', error: '这个文件在你读过之后又被改过' },
              },
            ],
          }}
        />
      )
      expect(screen.getByText('改第二章')).toBeInTheDocument()
      expect(screen.getByText('chat:tool.failureHint.authorChanged')).toBeInTheDocument()
      // 标题不把「没动」算成失败。
      expect(screen.getByText('chat:tool.parallel_summary_with_skipped')).toBeInTheDocument()
      expect(screen.queryByText('chat:tool.parallel_summary')).not.toBeInTheDocument()
      expect(screen.queryByText('chat:tool.taskNotCompleted')).not.toBeInTheDocument()
      expect(screen.queryByText(/这个文件在你读过之后又被改过/)).not.toBeInTheDocument()
      // 只有有意跳过时不标警告边框。
      expect(container.querySelector('.border-\\[hsl\\(var\\(--warning\\)\\)\\]')).toBeNull()
    })

    it('shows a partially applied sub-task as partial, not completed', () => {
      const { container } = render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            total_tasks: 2,
            completed: 1,
            partial: 1,
            failed: 0,
            any_failed: true,
            tasks: [
              parallelResult.tasks[0],
              { id: 't1', type: 'edit_file', description: '改第二章两处', status: 'partial', result: { status: 'partial' } },
            ],
          }}
        />
      )
      expect(screen.getByText('chat:tool.parallel_summary_with_partial')).toBeInTheDocument()
      expect(screen.getByText('chat:tool.taskPartiallyCompleted')).toBeInTheDocument()
      expect(container.querySelector('.border-\\[hsl\\(var\\(--warning\\)\\)\\]')).not.toBeNull()
    })

    it('counts partial sub-tasks from the task list when an older payload has no partial total', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            total_tasks: 1,
            completed: 0,
            failed: 0,
            tasks: [{ id: 't1', type: 'edit_file', description: '改两处', status: 'partial', result: {} }],
          }}
        />
      )
      expect(screen.getByText('chat:tool.parallel_summary_with_partial')).toBeInTheDocument()
    })

    it('does not mark an unknown sub-task status as completed', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            total_tasks: 1,
            tasks: [{ id: 't1', type: 'edit_file', description: '改两处', status: 'cancelled', result: {} }],
          }}
        />
      )
      expect(screen.getByText('chat:tool.taskOutcomeUnknown')).toBeInTheDocument()
    })

    it('renders all-success runs without an error row', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            execution_id: 'par-2',
            total_tasks: 1,
            completed: 1,
            failed: 0,
            any_failed: false,
            tasks: [
              {
                id: 't0',
                type: 'query_files',
                description: '检索资料',
                status: 'completed',
                result: {},
                error: null,
              },
            ],
          }}
        />
      )
      expect(screen.getByText('检索资料')).toBeInTheDocument()
    })

    it('labels sub-tasks with the localized task name instead of the raw type', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            total_tasks: 2,
            tasks: [
              { id: 't0', type: 'query_files', description: '读取第5章当前全文', status: 'completed', result: {} },
              { id: 't1', type: 'internal_probe', description: '核对设定', status: 'completed', result: {} },
            ],
          }}
        />
      )
      expect(screen.getByText('Query files:')).toBeInTheDocument()
      expect(screen.getByText('读取第5章当前全文')).toBeInTheDocument()
      expect(screen.getByText('核对设定')).toBeInTheDocument()
      expect(screen.queryByText(/\[query_files\]/)).not.toBeInTheDocument()
      expect(screen.queryByText(/internal_probe/)).not.toBeInTheDocument()
    })

    it('strips internal ids and raw fields from sub-task descriptions', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="parallel_execute"
          result={{
            total_tasks: 1,
            tasks: [
              {
                id: 't0',
                type: 'edit_file',
                description: '[edit_file] 修改第2章(id=68212e4b-fe8e-4238-ae5b-a57233195b69) word_count=2434',
                status: 'completed',
                result: {},
              },
            ],
          }}
        />
      )
      expect(screen.getByText('修改第2章 2434 chars')).toBeInTheDocument()
      expect(screen.queryByText(/68212e4b/)).not.toBeInTheDocument()
    })
  })

  describe('skill tools', () => {
    it('labels a pending load_skill call with the skill name', () => {
      render(
        <ToolResultCard type="tool_call" toolName="load_skill" isPending={true} result={{ name: '悬念大师' }} />
      )
      expect(screen.getByText('Load skill')).toBeInTheDocument()
      expect(screen.getByText(/「悬念大师」/)).toBeInTheDocument()
      expect(screen.queryByText('load_skill')).not.toBeInTheDocument()
    })

    it('labels a pending read_skill_resource call with skill and path', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="read_skill_resource"
          isPending={true}
          result={{ name: '悬念大师', path: 'references/hooks.md' }}
        />
      )
      expect(screen.getByText('Read skill resource')).toBeInTheDocument()
      expect(screen.getByText(/「悬念大师」 · references\/hooks\.md/)).toBeInTheDocument()
    })

    it('renders load_skill success with the skill name only', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="load_skill"
          result={{ skill_id: 's-1', skill_name: '悬念大师', source: 'user', instructions_chars: 120, resources: [] }}
        />
      )
      expect(screen.getByText('Load skill complete')).toBeInTheDocument()
      expect(screen.getByText('「悬念大师」')).toBeInTheDocument()
    })

    it('renders read_skill_resource success with skill name and path', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="read_skill_resource"
          result={{ skill_name: '悬念大师', path: 'references/hooks.md', size: 12 }}
        />
      )
      expect(screen.getByText('Read skill resource complete')).toBeInTheDocument()
      expect(screen.getByText('「悬念大师」 · references/hooks.md')).toBeInTheDocument()
    })
  })

  describe('mobile layout and touch-target polish', () => {
    it('wraps icon and title together inside the unified file button without underline', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="write_chapter"
          result={{ data: { id: 'ch-7', title: '第七章 开窍', file_type: 'draft', chapter: 7, action: 'created' } }}
          onOpenFile={vi.fn()}
        />
      )
      const fileButton = screen.getByRole('button', { name: '第七章 开窍' })
      expect(fileButton.querySelector('svg')).not.toBeNull()
      expect(fileButton.className).toContain('items-center')
      expect(fileButton.className).toContain('min-h-11')
      expect(fileButton.className).toContain('-my-2')
      expect(fileButton.className).not.toContain('underline')
    })

    it('indents edit_file details summary to pl-6 with rotating chevron and 44px mobile touch target', () => {
      const { container } = render(
        <ToolResultCard
          type="tool_result"
          toolName="edit_file"
          result={{
            data: {
              id: 'file-1',
              title: '第七章 开窍',
              file_type: 'draft',
              details: [{ op: 'replace', old_preview: 'a', new_preview: 'b' }],
            },
          }}
        />
      )
      const details = container.querySelector('details')
      const summary = container.querySelector('summary')
      const chevron = summary?.querySelector('svg')
      expect(details).toHaveClass('group')
      expect(summary?.className).toContain('pl-6')
      expect(summary?.className).toContain('min-h-11')
      expect(chevron?.getAttribute('class')).toContain('group-open:rotate-90')
      expect(screen.getByText('第七章 开窍')).toBeInTheDocument()
    })
  })
})
