import { describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', async importOriginal => ({
  ...(await importOriginal<typeof import('../apiClient')>()),
  tryRefreshToken: vi.fn(),
  validateToken: vi.fn(),
}));

vi.mock('../logger', () => ({
  logger: {
    log: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

import { clearAuthStorage, tryRefreshToken, validateToken } from '../apiClient';
import { handleSsoRedirect, isValidRedirectUrl } from '../ssoRedirect';

describe('isValidRedirectUrl', () => {
  it('accepts zenstory subdomain redirect', () => {
    expect(isValidRedirectUrl('https://app.zenstory.ai/projects')).toBe(true);
  });

  it('accepts zenstory subdomains', () => {
    expect(isValidRedirectUrl('https://app.zenstory.ai/workspace')).toBe(true);
  });

  it('rejects non-http protocols', () => {
    expect(isValidRedirectUrl('ftp://zenstory.ai/resource')).toBe(false);
  });

  it('rejects redirects with userinfo', () => {
    expect(isValidRedirectUrl('https://user@zenstory.ai/callback')).toBe(false);
  });

  it('rejects non-whitelisted domains', () => {
    expect(isValidRedirectUrl('https://evil.example.com/callback')).toBe(false);
  });
});

describe('handleSsoRedirect', () => {
  it('preserves credentials when refresh fails transiently', async () => {
    localStorage.setItem('access_token', 'expired-access');
    localStorage.setItem('refresh_token', 'refresh-token');
    vi.mocked(validateToken).mockResolvedValue({ valid: false, isNetworkError: false });
    vi.mocked(tryRefreshToken).mockResolvedValue(false);

    await expect(handleSsoRedirect('https://app.zenstory.ai/workspace')).resolves.toMatchObject({
      success: false,
      clearAuth: false,
      reason: 'network_error',
    });
    expect(localStorage.getItem('refresh_token')).toBe('refresh-token');
  });

  it('presents own definitive denial without redundantly clearing already-empty credentials', async () => {
    localStorage.setItem('access_token', 'expired-access');
    localStorage.setItem('refresh_token', 'refresh-token');
    vi.mocked(validateToken).mockResolvedValue({ valid: false, isNetworkError: false });
    vi.mocked(tryRefreshToken).mockImplementation(async () => {
      clearAuthStorage('refresh_failed');
      return false;
    });

    await expect(handleSsoRedirect('https://app.zenstory.ai/workspace')).resolves.toMatchObject({
      success: false,
      clearAuth: false,
      reason: 'session_expired',
    });
  });
});
