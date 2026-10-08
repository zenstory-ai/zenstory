import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { FileSearchInput } from '../FileSearchInput'

describe('FileSearchInput', () => {
  const mockOnChange = vi.fn()
  const mockOnClear = vi.fn()
  const mockOnFocus = vi.fn()
  const mockOnBlur = vi.fn()

  const defaultProps = {
    value: '',
    onChange: mockOnChange,
    onClear: mockOnClear,
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Rendering', () => {
    it('renders input field', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      expect(input).toBeInTheDocument()
    })

    it('renders with placeholder text', () => {
      render(<FileSearchInput {...defaultProps} placeholder="Search files..." />)

      const input = screen.getByPlaceholderText('Search files...')
      expect(input).toBeInTheDocument()
    })

    it('renders search icon', () => {
      render(<FileSearchInput {...defaultProps} />)

      const container = screen.getByRole('searchbox').parentElement
      const searchIcon = container?.querySelector('svg')
      expect(searchIcon).toBeInTheDocument()
    })

    it('does not render clear button when value is empty', () => {
      render(<FileSearchInput {...defaultProps} value="" />)

      const clearButton = screen.queryByLabelText('清空搜索')
      expect(clearButton).not.toBeInTheDocument()
    })

    it('renders clear button when value is not empty', () => {
      render(<FileSearchInput {...defaultProps} value="test" />)

      const clearButton = screen.getByLabelText('清空搜索')
      expect(clearButton).toBeInTheDocument()
    })

    it('applies custom className', () => {
      render(<FileSearchInput {...defaultProps} className="custom-class" />)

      const container = screen.getByRole('searchbox').parentElement
      expect(container).toHaveClass('custom-class')
    })

    it('has correct aria-label', () => {
      render(<FileSearchInput {...defaultProps} placeholder="Find files" />)

      const input = screen.getByLabelText('Find files')
      expect(input).toBeInTheDocument()
    })

    it('uses default aria-label when no placeholder', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByLabelText('Search')
      expect(input).toBeInTheDocument()
    })
  })

  describe('Input Focus/Blur', () => {
    it('calls onFocus when input is focused', () => {
      render(<FileSearchInput {...defaultProps} onFocus={mockOnFocus} />)

      const input = screen.getByRole('searchbox')
      fireEvent.focus(input)

      expect(mockOnFocus).toHaveBeenCalledTimes(1)
    })

    it('calls onBlur when input loses focus', () => {
      render(<FileSearchInput {...defaultProps} onBlur={mockOnBlur} />)

      const input = screen.getByRole('searchbox')
      fireEvent.blur(input)

      expect(mockOnBlur).toHaveBeenCalledTimes(1)
    })

    it('auto focuses when autoFocus is true', () => {
      render(<FileSearchInput {...defaultProps} autoFocus />)

      const input = screen.getByRole('searchbox')
      expect(input).toHaveFocus()
    })

    it('does not auto focus by default', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      expect(input).not.toHaveFocus()
    })
  })

  describe('Value Changes', () => {
    it('displays the current value', () => {
      render(<FileSearchInput {...defaultProps} value="hello" />)

      const input = screen.getByRole('searchbox') as HTMLInputElement
      expect(input.value).toBe('hello')
    })

    it('calls onChange when value changes', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      fireEvent.change(input, { target: { value: 'test' } })

      expect(mockOnChange).toHaveBeenCalledWith('test')
    })

    it('calls onChange for each character typed', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      fireEvent.change(input, { target: { value: 'a' } })
      fireEvent.change(input, { target: { value: 'ab' } })
      fireEvent.change(input, { target: { value: 'abc' } })

      expect(mockOnChange).toHaveBeenCalledTimes(3)
      expect(mockOnChange).toHaveBeenNthCalledWith(1, 'a')
      expect(mockOnChange).toHaveBeenNthCalledWith(2, 'ab')
      expect(mockOnChange).toHaveBeenNthCalledWith(3, 'abc')
    })

    it('handles empty value change', () => {
      render(<FileSearchInput {...defaultProps} value="test" />)

      const input = screen.getByRole('searchbox')
      fireEvent.change(input, { target: { value: '' } })

      expect(mockOnChange).toHaveBeenCalledWith('')
    })
  })

  describe('Clear Button', () => {
    it('calls onClear when clear button is clicked', () => {
      render(<FileSearchInput {...defaultProps} value="test" />)

      const clearButton = screen.getByLabelText('清空搜索')
      fireEvent.click(clearButton)

      expect(mockOnClear).toHaveBeenCalledTimes(1)
    })

    it('clear button disappears after clearing', () => {
      const { rerender } = render(<FileSearchInput {...defaultProps} value="test" />)

      const clearButton = screen.getByLabelText('清空搜索')
      expect(clearButton).toBeInTheDocument()

      // Simulate parent clearing the value
      rerender(<FileSearchInput {...defaultProps} value="" />)

      expect(screen.queryByLabelText('清空搜索')).not.toBeInTheDocument()
    })

    it('clear button is clickable with touch', () => {
      render(<FileSearchInput {...defaultProps} value="test" />)

      const clearButton = screen.getByLabelText('清空搜索')
      expect(clearButton).toHaveClass('touch-manipulation')
    })
  })

  describe('IME Composition', () => {
    it('does not send composition-confirming keys to navigation or window listeners', () => {
      const onKeyDown = vi.fn()
      const windowKeyDown = vi.fn()
      window.addEventListener('keydown', windowKeyDown)
      try {
        render(<FileSearchInput {...defaultProps} onKeyDown={onKeyDown} />)
        const input = screen.getByRole('searchbox')
        fireEvent.compositionStart(input)
        fireEvent.keyDown(input, { key: 'Enter' })
        fireEvent.compositionEnd(input)
        fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
        fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
        expect(onKeyDown).not.toHaveBeenCalled()
        expect(windowKeyDown).not.toHaveBeenCalled()
        fireEvent.keyDown(input, { key: 'Enter' })
        expect(onKeyDown).toHaveBeenCalledTimes(1)
        expect(windowKeyDown).toHaveBeenCalledTimes(1)
      } finally {
        window.removeEventListener('keydown', windowKeyDown)
      }
    })

    it('updates value during IME composition', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')

      // Simulate IME composition
      fireEvent.change(input, { target: { value: 'zhong' } })
      fireEvent.change(input, { target: { value: '中' } })

      expect(mockOnChange).toHaveBeenCalledWith('zhong')
      expect(mockOnChange).toHaveBeenCalledWith('中')
    })

    it('handles rapid input changes', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')

      // Rapid input
      for (let i = 0; i < 10; i++) {
        fireEvent.change(input, { target: { value: 't'.repeat(i + 1) } })
      }

      expect(mockOnChange).toHaveBeenCalledTimes(10)
    })
  })

  describe('Styling', () => {
    it('has correct input styling', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      expect(input).toHaveClass('w-full')
      expect(input).toHaveClass('rounded-lg')
    })

    it('shows focus styles when focused', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      fireEvent.focus(input)

      expect(input).toHaveClass('focus:outline-none')
    })

    it('has proper padding for icons', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      expect(input).toHaveClass('pl-9')
      expect(input).toHaveClass('pr-8')
    })
  })

  describe('Accessibility', () => {
    it('has searchbox role', () => {
      render(<FileSearchInput {...defaultProps} />)

      const input = screen.getByRole('searchbox')
      expect(input).toBeInTheDocument()
    })

    it('clear button has accessible label', () => {
      render(<FileSearchInput {...defaultProps} value="test" />)

      const clearButton = screen.getByLabelText('清空搜索')
      expect(clearButton).toBeInTheDocument()
    })

    it('search icon is aria-hidden', () => {
      render(<FileSearchInput {...defaultProps} />)

      const container = screen.getByRole('searchbox').parentElement
      const searchIcon = container?.querySelector('svg')
      expect(searchIcon).toHaveAttribute('aria-hidden', 'true')
    })

    it('supports keyboard navigation to clear button', () => {
      render(<FileSearchInput {...defaultProps} value="test" />)

      const clearButton = screen.getByLabelText('清空搜索')
      expect(clearButton).toHaveAttribute('type', 'button')
    })
  })

  describe('Controlled Component', () => {
    it('reflects external value changes', () => {
      const { rerender } = render(<FileSearchInput {...defaultProps} value="initial" />)

      const input = screen.getByRole('searchbox') as HTMLInputElement
      expect(input.value).toBe('initial')

      rerender(<FileSearchInput {...defaultProps} value="updated" />)
      expect(input.value).toBe('updated')
    })

    it('does not update value internally', () => {
      render(<FileSearchInput {...defaultProps} value="fixed" />)

      const input = screen.getByRole('searchbox')
      fireEvent.change(input, { target: { value: 'changed' } })

      // Value should still be "fixed" since parent controls it
      const inputElement = screen.getByRole('searchbox') as HTMLInputElement
      expect(inputElement.value).toBe('fixed')
    })
  })
})
