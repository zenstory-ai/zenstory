import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import zhAuth from '../../../public/locales/zh/auth.json'
import { ApiError } from '../../lib/apiClient'

const { mockNavigate, mockLocationState, mockRequestCode, mockConfirm, mockToastSuccess, flags } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockLocationState: { current: null as { email?: string } | null },
  mockRequestCode: vi.fn(),
  mockConfirm: vi.fn(),
  mockToastSuccess: vi.fn(),
  flags: { forgotPasswordEnabled: true },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => ({ pathname: '/forgot-password', search: '', hash: '', state: mockLocationState.current, key: 'default' }),
    Link: ({ to, children }: { to: string; children?: React.ReactNode }) => <a href={to}>{children}</a>,
    Navigate: ({ to }: { to: string }) => <div data-testid="navigate">{to}</div>,
  }
})

// Resolve keys against the real zh bundle so the page shows the shipped copy.
const lookup = (key: string): string | undefined => {
  const [ns, path] = key.split(':')
  if (ns !== 'auth') return undefined
  const value = path.split('.').reduce<unknown>(
    (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
    zhAuth,
  )
  return typeof value === 'string' ? value : undefined
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'zh' },
    t: (key: string, options?: string | Record<string, unknown>) => {
      const fallback = typeof options === 'string' ? options : (options?.defaultValue as string | undefined)
      const template = lookup(key) ?? fallback ?? key
      const vars = typeof options === 'object' && options ? options : {}
      return template.replace(/\{\{(\w+)\}\}/g, (_match, name: string) => String(vars[name] ?? ''))
    },
  }),
}))

vi.mock('../../lib/passwordResetApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/passwordResetApi')>('../../lib/passwordResetApi')
  return {
    ...actual,
    passwordResetApi: { requestCode: mockRequestCode, confirm: mockConfirm },
  }
})

vi.mock('../../lib/toast', () => ({
  toast: { success: mockToastSuccess, error: vi.fn(), info: vi.fn() },
}))

vi.mock('../../components/PublicHeader', () => ({
  PublicHeader: () => <div data-testid="public-header">Header</div>,
}))

vi.mock('../../components/Logo', () => ({
  LogoMark: () => <div data-testid="logo-mark">Logo</div>,
}))

vi.mock('../../config/auth', () => ({
  authConfig: {
    get forgotPasswordEnabled() {
      return flags.forgotPasswordEnabled
    },
  },
}))

import ForgotPassword from '../ForgotPassword'

const EMAIL = 'writer@example.com'

async function sendCodeFor(email = EMAIL) {
  fireEvent.change(screen.getByLabelText('注册邮箱'), { target: { value: `  ${email} ` } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '发送验证码' }))
  })
}

function fillResetForm(code: string, password: string, confirm = password) {
  fireEvent.change(screen.getByLabelText('验证码'), { target: { value: code } })
  fireEvent.change(screen.getByLabelText('新密码'), { target: { value: password } })
  fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: confirm } })
}

describe('ForgotPassword', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLocationState.current = null
    flags.forgotPasswordEnabled = true
    mockRequestCode.mockResolvedValue({ message: 'ok' })
    mockConfirm.mockResolvedValue({ message: 'ok' })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('sends a code, then shows the code step with the neutral notice', async () => {
    render(<ForgotPassword />)
    expect(screen.getByText('收不到邮件？发邮件给')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'support@zenstory.ai' })).toHaveAttribute(
      'href',
      'mailto:support@zenstory.ai',
    )

    await sendCodeFor()

    expect(mockRequestCode).toHaveBeenCalledWith(EMAIL, 'zh')
    expect(screen.getByTestId('forgot-password-subtitle')).toHaveTextContent(
      '如果这个邮箱注册过 ZenStory，验证码已经发出，10 分钟内有效。',
    )
    expect(screen.getByLabelText('验证码')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重设密码' })).toBeInTheDocument()
  })

  it('lets the user resend only after the 60 second countdown', async () => {
    vi.useFakeTimers()
    render(<ForgotPassword />)
    await sendCodeFor()

    const waiting = screen.getByRole('button', { name: '60 秒后可重新发送' })
    expect(waiting).toBeDisabled()
    fireEvent.click(waiting)
    expect(mockRequestCode).toHaveBeenCalledTimes(1)

    for (let second = 0; second < 59; second += 1) {
      act(() => {
        vi.advanceTimersByTime(1000)
      })
    }
    expect(screen.getByRole('button', { name: '1 秒后可重新发送' })).toBeDisabled()
    act(() => {
      vi.advanceTimersByTime(1000)
    })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '重新发送验证码' }))
    })
    expect(mockRequestCode).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: '60 秒后可重新发送' })).toBeDisabled()
  })

  it('resets the password, confirms with a toast and returns to login', async () => {
    render(<ForgotPassword />)
    await sendCodeFor()
    fillResetForm('12a3456', 'new-secret-2')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '重设密码' }))
    })

    expect(mockConfirm).toHaveBeenCalledWith(EMAIL, '123456', 'new-secret-2')
    expect(mockToastSuccess).toHaveBeenCalledWith('密码已重设，请用新密码登录')
    expect(mockNavigate).toHaveBeenCalledWith('/login', { replace: true, state: { identifier: EMAIL } })
  })

  it('prefills email handed over from login and supports password visibility toggles', async () => {
    mockLocationState.current = { email: EMAIL }
    render(<ForgotPassword />)

    expect(screen.getByLabelText('注册邮箱')).toHaveValue(EMAIL)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '发送验证码' }))
    })

    const newPwdInput = screen.getByLabelText('新密码')
    const confirmPwdInput = screen.getByLabelText('确认新密码')
    expect(newPwdInput).toHaveAttribute('type', 'password')
    expect(confirmPwdInput).toHaveAttribute('type', 'password')

    const toggles = screen.getAllByRole('button', { name: '显示密码' })
    expect(toggles).toHaveLength(2)
    fireEvent.click(toggles[0])
    expect(newPwdInput).toHaveAttribute('type', 'text')
    fireEvent.click(toggles[1])
    expect(confirmPwdInput).toHaveAttribute('type', 'text')
  })

  it('checks the new password locally before calling the API', async () => {
    render(<ForgotPassword />)
    await sendCodeFor()
    fillResetForm('123456', 'new-secret-2', 'new-secret-3')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '重设密码' }))
    })

    expect(mockConfirm).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(zhAuth.errors.passwordMismatch)
  })

  it('shows the server error and stays on the code step when the code is wrong', async () => {
    mockConfirm.mockRejectedValueOnce(new ApiError(400, '验证码错误或已过期，请重新输入或重新发送'))
    render(<ForgotPassword />)
    await sendCodeFor()
    fillResetForm('000000', 'new-secret-2')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '重设密码' }))
    })

    expect(screen.getByRole('alert')).toHaveTextContent('验证码错误或已过期')
    expect(mockNavigate).not.toHaveBeenCalled()
    expect(screen.getByLabelText('验证码')).toBeInTheDocument()
  })

  it.each([404, 405])('falls back to emailing support when the API answers %s', async (status) => {
    mockRequestCode.mockRejectedValueOnce(new ApiError(status, `API error: ${status}`))
    render(<ForgotPassword />)

    await sendCodeFor()

    expect(screen.getByTestId('forgot-password-subtitle')).toHaveTextContent(zhAuth.forgotPassword.unavailableSubtitle)
    expect(screen.getByRole('link', { name: '发送邮件' })).toHaveAttribute('href', 'mailto:support@zenstory.ai')
    expect(screen.queryByLabelText('注册邮箱')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('redirects back to login when the feature is disabled', () => {
    flags.forgotPasswordEnabled = false
    render(<ForgotPassword />)

    expect(screen.getByTestId('navigate')).toHaveTextContent('/login')
  })
})
