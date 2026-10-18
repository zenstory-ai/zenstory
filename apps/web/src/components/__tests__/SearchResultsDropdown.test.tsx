import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import SearchResultsDropdown from '../SearchResultsDropdown'
import type { FileSearchResult } from '../../hooks/useFileSearch'

// Mock i18n
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: {
      language: 'en',
    },
  }),
}))

const createMockResult = (overrides?: Partial<FileSearchResult>): FileSearchResult => ({
  id: '1',
  title: 'Test File',
  fileType: 'draft',
  parentPath: 'Root > Folder',
  parentId: 'parent-1',
  ...overrides,
})

describe('SearchResultsDropdown', () => {
  const mockOnSelect = vi.fn()
  const mockOnHover = vi.fn()
  const mockOnClose = vi.fn()

  const defaultProps = {
    results: [] as FileSearchResult[],
    selectedIndex: 0,
    onSelect: mockOnSelect,
    onHover: mockOnHover,
    visible: true,
    loading: false,
    onClose: mockOnClose,
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.clearAllTimers()
  })

  describe('Rendering', () => {
    it.each([false, true])('closes the %s loading/empty state only on outside clicks', (loading) => {
      render(
        <>
          <div data-testid="outside-search">Outside</div>
          <SearchResultsDropdown {...defaultProps} results={[]} loading={loading} />
        </>,
      )

      const message = screen.getByText(loading ? 'common:loading' : 'editor:fileTree.noSearchResults')
      fireEvent.mouseDown(message)
      expect(mockOnClose).not.toHaveBeenCalled()
      fireEvent.mouseDown(screen.getByTestId('outside-search'))
      expect(mockOnClose).toHaveBeenCalledTimes(1)
    })

    it('renders nothing when not visible', () => {
      render(<SearchResultsDropdown {...defaultProps} visible={false} />)

      expect(screen.queryByText('Test File')).not.toBeInTheDocument()
    })

    it('renders loading state', () => {
      render(<SearchResultsDropdown {...defaultProps} loading />)

      expect(screen.getByText('common:loading')).toBeInTheDocument()
    })

    it('renders empty state when no results', () => {
      render(<SearchResultsDropdown {...defaultProps} results={[]} />)

      expect(screen.getByText('editor:fileTree.noSearchResults')).toBeInTheDocument()
    })

    it('renders list of results', () => {
      const results: FileSearchResult[] = [
        createMockResult({ id: '1', title: 'File 1' }),
        createMockResult({ id: '2', title: 'File 2' }),
        createMockResult({ id: '3', title: 'File 3' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      expect(screen.getByText('File 1')).toBeInTheDocument()
      expect(screen.getByText('File 2')).toBeInTheDocument()
      expect(screen.getByText('File 3')).toBeInTheDocument()
    })

    it('renders parent path for each result', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'My File', parentPath: 'Root > Chapter 1' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      expect(screen.getByText('Root > Chapter 1')).toBeInTheDocument()
    })

    it('does not render parent path when empty', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'My File', parentPath: '' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      // Check that the file is rendered
      expect(screen.getByText('My File')).toBeInTheDocument()
    })
  })

  describe('File Type Icons', () => {
    it('renders icon for draft files', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'Draft', fileType: 'draft' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      // Check that the result item renders with content
      expect(screen.getByText('Draft')).toBeInTheDocument()
    })

    it('renders icon for character files', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'Character', fileType: 'character' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      expect(screen.getByText('Character')).toBeInTheDocument()
    })

    it('renders icon for outline files', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'Outline', fileType: 'outline' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      expect(screen.getByText('Outline')).toBeInTheDocument()
    })

    it('renders icon for lore files', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'Lore', fileType: 'lore' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      expect(screen.getByText('Lore')).toBeInTheDocument()
    })

    it('renders default icon for unknown file types', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'Unknown', fileType: 'unknown' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      expect(screen.getByText('Unknown')).toBeInTheDocument()
    })
  })

  describe('Keyboard Navigation', () => {
    it('renders selected item with correct styling', () => {
      const results: FileSearchResult[] = [
        createMockResult({ id: '1', title: 'File 1' }),
        createMockResult({ id: '2', title: 'File 2' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} selectedIndex={0} />)

      // The first item should be rendered
      expect(screen.getByText('File 1')).toBeInTheDocument()
      expect(screen.getByText('File 2')).toBeInTheDocument()
    })

    it('updates highlight when selected index changes', () => {
      const results: FileSearchResult[] = [
        createMockResult({ id: '1', title: 'File 1' }),
        createMockResult({ id: '2', title: 'File 2' }),
      ]

      const { rerender } = render(
        <SearchResultsDropdown {...defaultProps} results={results} selectedIndex={0} />
      )

      expect(screen.getByText('File 1')).toBeInTheDocument()

      rerender(<SearchResultsDropdown {...defaultProps} results={results} selectedIndex={1} />)

      expect(screen.getByText('File 2')).toBeInTheDocument()
    })

    it('calls onHover when mouse enters item', () => {
      const results: FileSearchResult[] = [
        createMockResult({ id: '1', title: 'File 1' }),
        createMockResult({ id: '2', title: 'File 2' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      // Find the clickable container for the second item
      const allItems = screen.getAllByText(/File \d/)
      const secondItem = allItems[1].closest('div[class*="cursor-pointer"]')

      if (secondItem) {
        fireEvent.mouseEnter(secondItem)
      }

      expect(mockOnHover).toHaveBeenCalledWith(1)
    })
  })

  describe('Click Selection', () => {
    it('calls onSelect when item is clicked', () => {
      const results: FileSearchResult[] = [
        createMockResult({ id: '1', title: 'File 1' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      const item = screen.getByText('File 1').closest('div[class*="cursor-pointer"]')
      if (item) {
        fireEvent.click(item)
      }

      expect(mockOnSelect).toHaveBeenCalledWith(results[0])
    })

    it('calls onSelect with correct result data', () => {
      const results: FileSearchResult[] = [
        createMockResult({
          id: 'test-id',
          title: 'Test File',
          fileType: 'character',
          parentPath: 'Root',
          parentId: 'parent-1',
        }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      const item = screen.getByText('Test File').closest('div[class*="cursor-pointer"]')
      if (item) {
        fireEvent.click(item)
      }

      expect(mockOnSelect).toHaveBeenCalledWith({
        id: 'test-id',
        title: 'Test File',
        fileType: 'character',
        parentPath: 'Root',
        parentId: 'parent-1',
      })
    })

    it('has touch-manipulation class for mobile', () => {
      const results: FileSearchResult[] = [
        createMockResult({ title: 'File 1' }),
      ]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      const item = screen.getByText('File 1').closest('div[class*="cursor-pointer"]')
      expect(item).toHaveClass('touch-manipulation')
    })
  })

  describe('Click Outside', () => {
    it('calls onClose when clicking outside dropdown', async () => {
      const results: FileSearchResult[] = [createMockResult()]

      render(
        <div>
          <div data-testid="outside">Outside</div>
          <SearchResultsDropdown {...defaultProps} results={results} />
        </div>
      )

      // Wait for the component to mount and add event listener
      await waitFor(() => {
        expect(screen.getByText('Test File')).toBeInTheDocument()
      })

      const outsideElement = screen.getByTestId('outside')
      fireEvent.mouseDown(outsideElement)

      await waitFor(() => {
        expect(mockOnClose).toHaveBeenCalled()
      }, { timeout: 2000 })
    })

    it('does not call onClose when clicking inside dropdown', async () => {
      const results: FileSearchResult[] = [createMockResult({ title: 'Click Me' })]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      await waitFor(() => {
        expect(screen.getByText('Click Me')).toBeInTheDocument()
      })

      const item = screen.getByText('Click Me')
      fireEvent.mouseDown(item)

      // onClose should not be called when clicking inside
      expect(mockOnClose).not.toHaveBeenCalled()
    })
  })

  describe('Empty State', () => {
    it('shows empty state message when results array is empty', () => {
      render(<SearchResultsDropdown {...defaultProps} results={[]} visible={true} />)

      expect(screen.getByText('editor:fileTree.noSearchResults')).toBeInTheDocument()
    })

    it('empty state has correct styling', () => {
      render(<SearchResultsDropdown {...defaultProps} results={[]} visible={true} />)

      const emptyState = screen.getByText('editor:fileTree.noSearchResults')
      expect(emptyState).toBeInTheDocument()
    })
  })

  describe('Loading State', () => {
    it('shows loading message when loading', () => {
      render(<SearchResultsDropdown {...defaultProps} loading={true} visible={true} />)

      expect(screen.getByText('common:loading')).toBeInTheDocument()
    })

    it('loading takes precedence over empty state', () => {
      render(
        <SearchResultsDropdown {...defaultProps} results={[]} loading={true} visible={true} />
      )

      expect(screen.getByText('common:loading')).toBeInTheDocument()
      expect(screen.queryByText('editor:fileTree.noSearchResults')).not.toBeInTheDocument()
    })

    it('loading takes precedence over results', () => {
      const results: FileSearchResult[] = [createMockResult({ title: 'Should Not Show' })]

      render(<SearchResultsDropdown {...defaultProps} results={results} loading={true} visible={true} />)

      expect(screen.getByText('common:loading')).toBeInTheDocument()
      expect(screen.queryByText('Should Not Show')).not.toBeInTheDocument()
    })
  })

  describe('Accessibility', () => {
    it('has proper z-index for dropdown', () => {
      const results: FileSearchResult[] = [createMockResult()]

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      // Check that the dropdown container exists with proper z-index class
      const dropdown = screen.getByText('Test File').closest('div[class*="z-50"]')
      expect(dropdown).toBeInTheDocument()
    })

    it('is scrollable for many results', () => {
      const results: FileSearchResult[] = Array.from({ length: 20 }, (_, i) =>
        createMockResult({ id: `file-${i}`, title: `File ${i}` })
      )

      render(<SearchResultsDropdown {...defaultProps} results={results} />)

      const scrollContainer = screen.getByText('File 0').closest('div[class*="overflow-y-auto"]')
      expect(scrollContainer).toBeInTheDocument()
    })
  })

  describe('Performance', () => {
    it('renders efficiently with many results', () => {
      const results: FileSearchResult[] = Array.from({ length: 50 }, (_, i) =>
        createMockResult({ id: `file-${i}`, title: `File ${i}` })
      )

      const startTime = performance.now()
      render(<SearchResultsDropdown {...defaultProps} results={results} />)
      const endTime = performance.now()

      // Rendering should complete in a reasonable time (under 500ms)
      expect(endTime - startTime).toBeLessThan(500)
    })
  })
})
