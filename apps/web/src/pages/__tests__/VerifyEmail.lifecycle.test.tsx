import { StrictMode } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
type VerificationStatusResponse = { email_verified: boolean; resend_cooldown_seconds: number; verification_code_ttl_seconds: number };

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(), verify: vi.fn(), resend: vi.fn(), status: vi.fn(),
}));
vi.mock('react-router-dom', () => ({ useNavigate: () => mocks.navigate }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, opts?: { count?: number; time?: string }) =>
    key === 'auth:verifyEmail.resendButtonWithCount' ? 'resend:' + opts?.count :
      key === 'auth:verifyEmail.codeExpiring' ? 'ttl:' + opts?.time : key }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ verifyEmail: mocks.verify, resendVerification: mocks.resend }),
}));
vi.mock('../../lib/api', () => ({ authApi: { checkVerification: mocks.status } }));
vi.mock('../../components/PublicHeader', () => ({ PublicHeader: () => null }));
import VerifyEmail from '../VerifyEmail';

const status = (cooldown: number, ttl = 300): VerificationStatusResponse => ({
  email_verified: false, resend_cooldown_seconds: cooldown, verification_code_ttl_seconds: ttl,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function page(strict: boolean, email = 'a@example.test') {
  const content = <VerifyEmail email={email} planIntent="pro" />;
  return strict ? <StrictMode>{content}</StrictMode> : content;
}
function paste() {
  fireEvent.paste(screen.getAllByRole('textbox')[0]!, {
    clipboardData: { getData: () => '123456' },
  });
}
async function tick(ms: number) { await act(async () => { vi.advanceTimersByTime(ms); }); }

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  mocks.status.mockResolvedValue(status(0));
  mocks.verify.mockImplementation(() => new Promise<void>(() => undefined));
  mocks.resend.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup(); vi.clearAllTimers(); vi.useRealTimers();
});

describe.each([false, true])('VerifyEmail lifetime strict=%s', strict => {
  it('does not begin verification after leaving before automatic submit delay', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(page(strict)); });
    paste();
    view.unmount();
    await tick(100);
    expect(mocks.verify).not.toHaveBeenCalled();
  });

  it('coalesces ordinary repeated full-code paste into one pending verification', async () => {
    await act(async () => { render(page(strict)); });
    paste(); paste();
    await tick(100);
    expect(mocks.verify).toHaveBeenCalledTimes(1);
    expect(mocks.verify).toHaveBeenCalledWith('a@example.test', '123456');
    screen.getAllByRole('textbox').forEach(input => expect(input).toBeDisabled());
  });

  it('does not let A status overwrite B cooldown and expiry', async () => {
    const a = deferred<VerificationStatusResponse>(), b = deferred<VerificationStatusResponse>();
    mocks.status.mockImplementation((email: string) => email === 'a@example.test' ? a.promise : b.promise);
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(page(strict)); });
    await act(async () => { view.rerender(page(strict, 'b@example.test')); });
    await act(async () => { b.resolve(status(12, 120)); });
    expect(screen.getByRole('button', { name: 'resend:12' })).toBeDisabled();
    expect(screen.getByText('ttl:2:00')).toBeInTheDocument();
    await act(async () => { a.resolve(status(900, 900)); });
    expect(screen.getByRole('button', { name: 'resend:12' })).toBeDisabled();
    expect(screen.getByText('ttl:2:00')).toBeInTheDocument();
  });

  it('cancels pending automatic verification when the page changes email', async () => {
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(page(strict)); });
    paste();
    await act(async () => { view.rerender(page(strict, 'b@example.test')); });
    await tick(100);
    expect(mocks.verify).not.toHaveBeenCalled();
  });


  it('does not submit an old complete code after ordinary digit deletion', async () => {
    await act(async () => { render(page(strict)); });
    paste();
    fireEvent.change(screen.getAllByRole('textbox')[0]!, { target: { value: '' } });
    await tick(100);
    expect(mocks.verify).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'] as const)('keeps B operation owned when pending A verification %s', async outcome => {
    const a = deferred<void>(), b = deferred<void>();
    mocks.verify.mockImplementation((email: string) => email === 'a@example.test' ? a.promise : b.promise);
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(page(strict)); });
    paste(); await tick(100);
    await act(async () => { view.rerender(page(strict, 'b@example.test')); });
    screen.getAllByRole('textbox').forEach(input => expect(input).toBeEnabled());
    paste(); await tick(100);
    expect(mocks.verify).toHaveBeenCalledWith('b@example.test', '123456');
    await act(async () => {
      if (outcome === 'resolve') a.resolve(undefined); else a.reject(new Error('old A failed'));
    });
    screen.getAllByRole('textbox').forEach(input => expect(input).toBeDisabled());
    expect(screen.queryByText('old A failed')).not.toBeInTheDocument();
    expect(screen.queryByText('auth:verifyEmail.successTitle')).not.toBeInTheDocument();
    await tick(1500);
    expect(mocks.navigate).not.toHaveBeenCalled();
    await act(async () => { b.resolve(undefined); });
    expect(screen.getByText('auth:verifyEmail.successTitle')).toBeInTheDocument();
    await tick(1500);
    expect(mocks.navigate).toHaveBeenCalledOnce();
  });

  it.each(['resolve', 'reject'] as const)('ignores old resend status %s without changing pending B or fallback', async outcome => {
    const aStatus = deferred<VerificationStatusResponse>(), bVerify = deferred<void>();
    let aRequests = 0;
    mocks.status.mockImplementation((email: string) => {
      if (email !== 'a@example.test') return Promise.resolve(status(12, 120));
      aRequests++;
      return aRequests <= (strict ? 2 : 1) ? Promise.resolve(status(0)) : aStatus.promise;
    });
    mocks.verify.mockReturnValue(bVerify.promise);
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(page(strict)); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.resendButton' })); });
    expect(aRequests).toBe(strict ? 3 : 2);
    await act(async () => { view.rerender(page(strict, 'b@example.test')); });
    screen.getAllByRole('textbox').forEach(input => expect(input).toBeEnabled());
    paste(); await tick(100);
    expect(mocks.verify).toHaveBeenCalledWith('b@example.test', '123456');
    await act(async () => {
      if (outcome === 'resolve') aStatus.resolve(status(900, 900));
      else aStatus.reject(new Error('old status failed'));
    });
    screen.getAllByRole('textbox').forEach(input => expect(input).toBeDisabled());
    expect(screen.getByRole('button', { name: 'resend:12' })).toBeDisabled();
    expect(screen.getByText('ttl:2:00')).toBeInTheDocument();
    expect(screen.queryByText('old status failed')).not.toBeInTheDocument();
    expect(screen.getAllByRole('textbox').map(input => (input as HTMLInputElement).value)).toEqual(['1','2','3','4','5','6']);
    await act(async () => { bVerify.reject(new Error('current B failed')); });
    expect(screen.getByText('current B failed')).toBeInTheDocument();
    screen.getAllByRole('textbox').forEach(input => expect(input).toBeEnabled());
  });

  it('releases failed verification for an ordinary retry and preserves paid success navigation', async () => {
    const first = deferred<void>();
    mocks.verify.mockReturnValueOnce(first.promise).mockResolvedValueOnce(undefined);
    await act(async () => { render(page(strict)); });
    paste(); await tick(100);
    await act(async () => { first.reject(new Error('offline code denied')); });
    expect(screen.getByText('offline code denied')).toBeInTheDocument();
    screen.getAllByRole('textbox').forEach(input => expect(input).toBeEnabled());
    paste(); await tick(100);
    expect(mocks.verify).toHaveBeenCalledTimes(2);
    expect(screen.getByText('auth:verifyEmail.successTitle')).toBeInTheDocument();
    await tick(1500);
    expect(mocks.navigate).toHaveBeenCalledOnce();
    expect(mocks.navigate).toHaveBeenCalledWith('/dashboard/billing?plan=pro');
  });

  it('preserves resend fallback and permits a later verification', async () => {
    mocks.status.mockResolvedValueOnce(status(0));
    // StrictMode calls the initial status effect twice.
    if (strict) mocks.status.mockResolvedValueOnce(status(0));
    mocks.status.mockRejectedValue(new Error('offline status unavailable'));
    mocks.verify.mockResolvedValue(undefined);
    await act(async () => { render(page(strict)); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'auth:verifyEmail.resendButton' })); });
    expect(mocks.resend).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'resend:60' })).toBeDisabled();
    expect(screen.getByText('ttl:5:00')).toBeInTheDocument();
    paste(); await tick(100);
    expect(mocks.verify).toHaveBeenCalledTimes(1);
  });
});
