import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import {
  MaterialAttachmentProvider,
  MAX_ATTACHED_MATERIALS,
  useMaterialAttachment,
} from '../MaterialAttachmentContext'

function MaterialAttachmentConsumer() {
  const {
    attachedMaterials,
    attachedFileIds,
    attachedLibraryMaterials,
    addMaterial,
    removeMaterial,
    clearMaterials,
    isAtLimit,
  } = useMaterialAttachment()

  return (
    <div>
      <div data-testid="attached-count">{attachedMaterials.length}</div>
      <div data-testid="attached-file-ids">{attachedFileIds.join(',')}</div>
      <div data-testid="attached-library-count">{attachedLibraryMaterials.length}</div>
      <div data-testid="is-at-limit">{String(isAtLimit)}</div>
      <button type="button" onClick={() => addMaterial('file-1', 'Draft one')}>
        add-file
      </button>
      <button
        type="button"
        onClick={() =>
          addMaterial('library-1', 'Library item', {
            novelId: 9,
            entityType: 'character',
            entityId: 12,
          })
        }
      >
        add-library
      </button>
      <button type="button" onClick={() => removeMaterial('file-1')}>
        remove-file
      </button>
      <button type="button" onClick={clearMaterials}>
        clear
      </button>
    </div>
  )
}

describe('MaterialAttachmentContext', () => {
  it('adds file and library materials while deriving IDs and library references', () => {
    render(
      <MaterialAttachmentProvider>
        <MaterialAttachmentConsumer />
      </MaterialAttachmentProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'add-file' }))
    fireEvent.click(screen.getByRole('button', { name: 'add-library' }))

    expect(screen.getByTestId('attached-count')).toHaveTextContent('2')
    expect(screen.getByTestId('attached-file-ids')).toHaveTextContent('file-1')
    expect(screen.getByTestId('attached-library-count')).toHaveTextContent('1')
  })

  it('prevents duplicates and enforces the attachment limit', () => {
    const { result } = renderHook(() => useMaterialAttachment(), {
      wrapper: ({ children }) => <MaterialAttachmentProvider>{children}</MaterialAttachmentProvider>,
    })

    act(() => {
      expect(result.current.addMaterial('file-1', 'Draft one')).toBe(true)
    })
    act(() => {
      expect(result.current.addMaterial('file-1', 'Draft one')).toBe(false)
    })

    for (let index = 2; index <= MAX_ATTACHED_MATERIALS; index += 1) {
      act(() => {
        expect(result.current.addMaterial(`file-${index}`, `Draft ${index}`)).toBe(true)
      })
    }

    expect(result.current.isAtLimit).toBe(true)
    act(() => {
      expect(result.current.addMaterial('file-overflow', 'Overflow')).toBe(false)
    })
    expect(result.current.attachedMaterials).toHaveLength(MAX_ATTACHED_MATERIALS)
  })

  it('prevents duplicate library materials even when callers use different display IDs', () => {
    const { result } = renderHook(() => useMaterialAttachment(), {
      wrapper: ({ children }) => <MaterialAttachmentProvider>{children}</MaterialAttachmentProvider>,
    })

    const librarySource = {
      novelId: 9,
      entityType: 'stories',
      entityId: 12,
    }

    act(() => {
      expect(result.current.addMaterial('material-1000', 'Story arc', librarySource)).toBe(true)
    })
    act(() => {
      expect(result.current.addMaterial('material-2000', 'Story arc', librarySource)).toBe(false)
    })

    expect(result.current.attachedMaterials).toHaveLength(1)
    expect(result.current.attachedLibraryMaterials).toEqual([
      {
        novel_id: 9,
        entity_type: 'stories',
        entity_id: 12,
      },
    ])
  })

  it('removes and clears attached materials', () => {
    render(
      <MaterialAttachmentProvider>
        <MaterialAttachmentConsumer />
      </MaterialAttachmentProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'add-file' }))
    fireEvent.click(screen.getByRole('button', { name: 'remove-file' }))
    expect(screen.getByTestId('attached-count')).toHaveTextContent('0')

    fireEvent.click(screen.getByRole('button', { name: 'add-file' }))
    fireEvent.click(screen.getByRole('button', { name: 'add-library' }))
    fireEvent.click(screen.getByRole('button', { name: 'clear' }))
    expect(screen.getByTestId('attached-count')).toHaveTextContent('0')
  })

  it('throws when used outside its provider', () => {
    expect(() => renderHook(() => useMaterialAttachment())).toThrow(
      'useMaterialAttachment must be used within a MaterialAttachmentProvider',
    )
  })
})
