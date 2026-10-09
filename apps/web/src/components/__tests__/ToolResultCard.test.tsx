import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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
  if (key === 'chat:tool.failed' && options?.label !== undefined) {
    return `${options.label} failed`
  }
  if (key === 'chat:tool.failureHint.repeatRead' && options?.title !== undefined) {
    return `Already read ${options.title}`
  }
  if (key === 'chat:response.conflicts_detected' && options?.count !== undefined) {
    return `Conflicts detected`
  }

  const translations: Record<string, string> = {
    'chat:tool.created': 'Created',
    'chat:tool.updated': 'Updated',
    'chat:tool.deleted': 'Deleted',
    'chat:tool.files_found': 'Files found',
    'chat:tool.failed': 'Tool failed',
    'chat:tool.no_files_found': 'No files found',
    'chat:tool.edit_success': 'Edit success',
    'chat:tool.view_edit_details': 'View edit details',
    'chat:tool.status_updated': 'Status updated',
    'chat:tool.processing_ellipsis': 'Processing...',
    'chat:tool.create_file': 'Create file',
    'chat:tool.update_file': 'Update file',
    'chat:tool.edit_file': 'Edit file',
    'chat:tool.delete_file': 'Delete file',
    'chat:tool.query_files': 'Query files',
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
    'chat:response.generated_result': 'Generated result',
    'chat:response.used_snippets': 'Used snippets',
    'chat:response.conflicts_detected': 'Conflicts detected',
    'chat:response.reasoning_label': 'Reasoning',
    'chat:action.insert': 'Insert',
    'chat:action.replace': 'Replace',
    'chat:action.new_snippet': 'New snippet',
    'chat:action.reference_only': 'Reference only',
    'chat:action.apply': 'Apply',
    'chat:conflict.suggestions_label': 'Suggestions',
    'chat:conflict.fix_suggestions': 'Fix suggestions',
    'chat:conflict.location': 'Location',
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
      expect(screen.getByText('Processing...')).toBeInTheDocument()
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

    it('displays query description for query_files', () => {
      render(
        <ToolResultCard
          type="tool_call"
          toolName="query_files"
          isPending={true}
          result={{ file_type: 'character' }}
        />
      )
      // Query description is shown
    })
  })

  describe('Tool Result (success state)', () => {
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

    it('shows content length for created files', () => {
      render(
        <ToolResultCard
          type="tool_result"
          toolName="create_file"
          result={{
            data: {
              title: 'New File',
              file_type: 'draft',
              content: 'A'.repeat(1500),
            },
          }}
        />
      )
      // Content length is displayed
      expect(screen.getByText(/1.5k/)).toBeInTheDocument()
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
        />
      )

      const undoButton = screen.getByRole('button', { name: /undo/i })
      await user.click(undoButton)

      expect(onUndo).toHaveBeenCalledWith({
        fileId: 'file-1',
        beforeVersionNumber: 7,
        expectedAfterUpdatedAt: '2026-10-06T12:34:56.000Z',
      })
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
        />
      )

      await user.click(screen.getByRole('button', { name: /undo/i }))
      expect(onUndo).toHaveBeenCalledWith({
        fileId: 'file-1',
        beforeVersionNumber: 9,
        expectedAfterUpdatedAt: token,
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

  describe('Conflict rendering', () => {
    it('renders conflict with high severity', () => {
      render(
        <ToolResultCard
          type="conflict"
          result={{
            conflict: {
              type: 'consistency',
              severity: 'high',
              title: 'Critical Issue',
              description: 'This is a critical issue',
              suggestions: ['Fix it now'],
              references: [],
            },
          }}
        />
      )
      expect(screen.getByText('Critical Issue')).toBeInTheDocument()
      expect(screen.getByText('This is a critical issue')).toBeInTheDocument()
    })

    it('renders conflict with medium severity', () => {
      render(
        <ToolResultCard
          type="conflict"
          result={{
            conflict: {
              type: 'consistency',
              severity: 'medium',
              title: 'Warning',
              description: 'This is a warning',
              suggestions: ['Consider fixing'],
              references: [],
            },
          }}
        />
      )
      expect(screen.getByText('Warning')).toBeInTheDocument()
    })

    it('renders conflict with low severity', () => {
      render(
        <ToolResultCard
          type="conflict"
          result={{
            conflict: {
              type: 'consistency',
              severity: 'low',
              title: 'Suggestion',
              description: 'This is a suggestion',
              suggestions: ['Optional improvement'],
              references: [],
            },
          }}
        />
      )
      expect(screen.getByText('Suggestion')).toBeInTheDocument()
    })

    it('displays conflict suggestions', () => {
      render(
        <ToolResultCard
          type="conflict"
          result={{
            conflict: {
              type: 'consistency',
              severity: 'high',
              title: 'Issue',
              description: 'Description',
              suggestions: ['Suggestion 1', 'Suggestion 2', 'Suggestion 3'],
              references: [],
            },
          }}
        />
      )
      expect(screen.getByText('Suggestion 1')).toBeInTheDocument()
      expect(screen.getByText('Suggestion 2')).toBeInTheDocument()
      expect(screen.getByText('Suggestion 3')).toBeInTheDocument()
    })
  })

  describe('Response rendering', () => {
    it('renders full response with text', () => {
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Generated response text',
            apply_action: 'insert',
          }}
        />
      )
      expect(screen.getByText('Generated response text')).toBeInTheDocument()
    })

    it('shows apply button when apply_action is not reference_only', async () => {
      const user = userEvent.setup()
      const onApply = vi.fn()
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Response',
            apply_action: 'insert',
          }}
          onApply={onApply}
        />
      )

      const applyButton = screen.getByRole('button', { name: /insert/i })
      await user.click(applyButton)

      expect(onApply).toHaveBeenCalled()
    })

    it('hides apply button when apply_action is reference_only', () => {
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Response',
            apply_action: 'reference_only',
          }}
          onApply={vi.fn()}
        />
      )

      expect(screen.queryByRole('button', { name: /insert/i })).not.toBeInTheDocument()
    })

    it('shows retry button', async () => {
      const user = userEvent.setup()
      const onRetry = vi.fn()
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Response',
            apply_action: 'insert',
          }}
          onRetry={onRetry}
        />
      )

      const retryButton = screen.getByRole('button', { name: /retry/i })
      await user.click(retryButton)

      expect(onRetry).toHaveBeenCalled()
    })

    it('shows copy button', async () => {
      const user = userEvent.setup()
      const onCopy = vi.fn()
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Response',
            apply_action: 'insert',
          }}
          onCopy={onCopy}
        />
      )

      const copyButton = screen.getByRole('button', { name: /copy/i })
      await user.click(copyButton)

      expect(onCopy).toHaveBeenCalled()
    })

    it('displays used snippets', () => {
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Response',
            apply_action: 'insert',
            used_snippets: [
              { id: 'snippet-1', title: 'Snippet 1', content: 'Content 1' },
              { id: 'snippet-2', title: 'Snippet 2', content: 'Content 2' },
            ],
          }}
        />
      )
      expect(screen.getByText('Used snippets')).toBeInTheDocument()
      expect(screen.getByText('Snippet 1')).toBeInTheDocument()
      expect(screen.getByText('Snippet 2')).toBeInTheDocument()
    })

    it('displays conflicts in response', () => {
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Response',
            apply_action: 'insert',
            conflicts: [
              {
                type: 'consistency',
                severity: 'high',
                title: 'Conflict 1',
                description: 'Description 1',
                suggestions: [],
                references: [],
              },
            ],
          }}
        />
      )
      expect(screen.getByText('Conflicts detected')).toBeInTheDocument()
      expect(screen.getByText('Conflict 1')).toBeInTheDocument()
    })

    it('displays reasoning', () => {
      render(
        <ToolResultCard
          type="response"
          response={{
            text: 'Response',
            apply_action: 'insert',
            reasoning: 'This is the reasoning for the response',
          }}
        />
      )
      expect(screen.getByText('Reasoning')).toBeInTheDocument()
      expect(screen.getByText('This is the reasoning for the response')).toBeInTheDocument()
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
})
