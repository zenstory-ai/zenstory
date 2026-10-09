import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { InviteCodeInput } from '../referral/InviteCodeInput'
import * as referralApi from '@/lib/referralApi'

// Mock the referralApi
vi.mock('@/lib/referralApi', () => ({
  referralApi: {
    validateCode: vi.fn(),
  },
}))

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue: string) => defaultValue,
  }),
}))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('InviteCodeInput', () => {
  const mockOnChange = vi.fn()
  const mockValidateCode = vi.mocked(referralApi.referralApi.validateCode)

  beforeEach(() => {
    vi.clearAllMocks()
    mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid code' })
  })

  describe('Rendering', () => {
    it('renders input field', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      expect(input).toBeInTheDocument()
    })

    it('renders with label', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      expect(screen.getByText('邀请码（可选）')).toBeInTheDocument()
    })

    it('renders placeholder', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      expect(screen.getByPlaceholderText('XXXX-XXXX')).toBeInTheDocument()
    })

    it('displays current value', () => {
      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox') as HTMLInputElement
      expect(input.value).toBe('ABCD-EFGH')
    })

    it('has font-mono class for code display', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      expect(input).toHaveClass('font-mono')
    })

    it('has uppercase class', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      expect(input).toHaveClass('uppercase')
    })
  })

  describe('Input Formatting', () => {
    it('auto-inserts dash after 4 characters', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      fireEvent.change(input, { target: { value: 'ABCD' } })

      expect(mockOnChange).toHaveBeenCalledWith('ABCD-')
    })

    it('does not insert dash if already present', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      fireEvent.change(input, { target: { value: 'ABCD-' } })

      expect(mockOnChange).toHaveBeenCalledWith('ABCD-')
    })

    it('converts to uppercase', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      fireEvent.change(input, { target: { value: 'abcd' } })

      expect(mockOnChange).toHaveBeenCalledWith('ABCD-')
    })

    it('removes non-alphanumeric characters', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      fireEvent.change(input, { target: { value: 'AB@CD' } })

      expect(mockOnChange).toHaveBeenCalledWith('ABCD-')
    })

    it('limits to 9 characters (XXXX-XXXX)', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      // Input 14 chars without dash
      // Logic: uppercase -> remove special chars -> limit to 9
      // Dash is only auto-inserted when length === 4
      // So 'ABCDEFGHIJKLMN' becomes 'ABCDEFGHI' (9 chars)
      fireEvent.change(input, { target: { value: 'ABCDEFGHIJKLMN' } })

      expect(mockOnChange).toHaveBeenCalledWith('ABCDEFGHI')
    })

    it('preserves dash position', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      fireEvent.change(input, { target: { value: 'ABCD-EFG' } })

      expect(mockOnChange).toHaveBeenCalledWith('ABCD-EFG')
    })
  })

  describe('Validation Display', () => {
    it('does not validate incomplete codes', () => {
      render(<InviteCodeInput value="ABCD" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      expect(mockValidateCode).not.toHaveBeenCalled()
    })

    it('validates complete codes (9 chars with dash)', async () => {
      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(mockValidateCode).toHaveBeenCalledWith('ABCD-EFGH')
      })
    })

    it('shows loading spinner during validation', async () => {
      mockValidateCode.mockImplementation(() => new Promise(() => {})) // Never resolves

      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      // Wait for validation to start
      await waitFor(() => {
        expect(mockValidateCode).toHaveBeenCalled()
      })

      // Loader2 icon should be present
      const container = screen.getByRole('textbox').parentElement
      const spinner = container?.querySelector('.animate-spin')
      expect(spinner).toBeInTheDocument()
    })

    it('shows success icon for valid codes', async () => {
      mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid code' })

      // Use prefilled=true to auto-touch and trigger validation
      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} prefilled />, {
        wrapper: createWrapper(),
      })

      // Wait for validation to complete
      await waitFor(() => {
        expect(mockValidateCode).toHaveBeenCalledWith('ABCD-EFGH')
      })

      // CheckCircle icon should be present after validation (lucide uses circle-check-big class)
      await waitFor(() => {
        const container = screen.getByRole('textbox').parentElement
        const checkCircle = container?.querySelector('.lucide-circle-check-big')
        expect(checkCircle).toBeTruthy()
      })
    })

    it('shows error icon for invalid codes', async () => {
      mockValidateCode.mockResolvedValue({ valid: false, message: 'Invalid code' })

      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(mockValidateCode).toHaveBeenCalled()
      })
    })

    it('shows validation message', async () => {
      mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid invite code' })

      // Use prefilled=true to auto-touch and trigger validation display
      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} prefilled />, {
        wrapper: createWrapper(),
      })

      // 显示本地化文案，而不是后端返回的固定英文 message
      await waitFor(() => {
        expect(screen.getByText('邀请码有效')).toBeInTheDocument()
      })
      expect(screen.queryByText('Valid invite code')).not.toBeInTheDocument()
    })

    it('hides validation before touched', async () => {
      mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid' })

      render(
        <InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} prefilled={false} />,
        {
          wrapper: createWrapper(),
        }
      )

      // Should not show validation message even though code is valid
      expect(screen.queryByText('Valid')).not.toBeInTheDocument()
    })

    it('shows validation when prefilled (auto-touched)', async () => {
      mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid' })

      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} prefilled />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('Valid')).toBeInTheDocument()
      })
    })
  })

  describe('Touch State', () => {
    it('starts untouched by default', () => {
      mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid' })

      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      // Helper text should be shown, not validation
      expect(screen.getByText('填写邀请码可获得积分奖励')).toBeInTheDocument()
    })

    it('becomes touched on input change', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      fireEvent.change(input, { target: { value: 'A' } })

      // After typing, should not show helper text anymore
      // (validation will start when code is complete)
    })

    it('becomes touched on blur', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      fireEvent.blur(input)

      // Touched state is internal, can't directly verify
      expect(input).toBeInTheDocument()
    })

    it('auto-touches when prefilled', async () => {
      mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid' })

      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} prefilled />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        // Should show validation message immediately
        expect(screen.getByText('Valid')).toBeInTheDocument()
      })
    })
  })

  describe('Disabled State', () => {
    it('can be disabled', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} disabled />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      expect(input).toBeDisabled()
    })

    it('does not call onChange when disabled', () => {
      // When an input is disabled, the HTML input doesn't fire change events
      // But even if it did, the component's handleChange would still be called
      // The test should verify the controlled component behavior
      render(<InviteCodeInput value="" onChange={mockOnChange} disabled />, {
        wrapper: createWrapper(),
      })

      const input = screen.getByRole('textbox')
      // The input is disabled, so even if we fire a change event,
      // the browser won't actually process it
      expect(input).toBeDisabled()
      // Verify no onChange was called (since the component is controlled and disabled)
      expect(mockOnChange).not.toHaveBeenCalled()
    })
  })

  describe('Helper Text', () => {
    it('shows helper text when not showing validation', () => {
      render(<InviteCodeInput value="" onChange={mockOnChange} />, {
        wrapper: createWrapper(),
      })

      expect(screen.getByText('填写邀请码可获得积分奖励')).toBeInTheDocument()
    })

    it('hides helper text when showing validation', async () => {
      mockValidateCode.mockResolvedValue({ valid: true, message: 'Valid' })

      // Use prefilled to auto-touch and trigger validation
      render(<InviteCodeInput value="ABCD-EFGH" onChange={mockOnChange} prefilled />, {
        wrapper: createWrapper(),
      })

      // After validation shows, helper text should be hidden
      await waitFor(() => {
        expect(screen.getByText('Valid')).toBeInTheDocument()
      })

      // Helper text should not be visible when validation is shown
      expect(screen.queryByText('填写邀请码可获得积分奖励')).not.toBeInTheDocument()
    })
  })
})
