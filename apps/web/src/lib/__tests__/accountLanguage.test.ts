import { afterEach, describe, expect, it, vi } from 'vitest';
import i18next, { type i18n as I18n } from 'i18next';

const { put } = vi.hoisted(() => ({ put: vi.fn() }));
vi.mock('../apiClient', () => ({ api: { put } }));

import { syncAccountLanguage } from '../accountLanguage';

async function instance(language: string): Promise<I18n> {
  const i18n = i18next.createInstance();
  await i18n.init({ lng: language, supportedLngs: ['zh', 'en'], resources: {} });
  return i18n;
}

describe('interface language on the account', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('opens a new device in the account language without saving it back', async () => {
    put.mockResolvedValue({});
    const i18n = await instance('zh');

    const stop = syncAccountLanguage(i18n, 'en');
    await vi.waitFor(() => expect(i18n.language).toBe('en'));

    expect(put).not.toHaveBeenCalled();
    stop();
  });

  it('saves a language the user switches to while signed in, and stops after sign-out', async () => {
    put.mockResolvedValue({});
    const i18n = await instance('en');
    const stop = syncAccountLanguage(i18n, 'en');

    await i18n.changeLanguage('zh');
    expect(put).toHaveBeenCalledWith('/api/auth/me/preferences', { ui_language: 'zh' });

    stop();
    await i18n.changeLanguage('en');
    expect(put).toHaveBeenCalledTimes(1);
  });

  it('leaves an account without a language alone until the user switches', async () => {
    put.mockResolvedValue({});
    const i18n = await instance('en');

    const stop = syncAccountLanguage(i18n, null);
    expect(i18n.language).toBe('en');
    expect(put).not.toHaveBeenCalled();

    await i18n.changeLanguage('zh');
    expect(put).toHaveBeenCalledWith('/api/auth/me/preferences', { ui_language: 'zh' });
    stop();
  });
});
