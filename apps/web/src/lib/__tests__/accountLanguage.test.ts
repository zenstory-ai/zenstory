import { afterEach, describe, expect, it, vi } from 'vitest';
import i18next, { type i18n as I18n } from 'i18next';

const { put, warn } = vi.hoisted(() => ({ put: vi.fn(), warn: vi.fn() }));
vi.mock('../apiClient', () => ({ api: { put } }));
vi.mock('../logger', () => ({ logger: { warn } }));

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

  it('opens a new device in the account language without overwriting it', async () => {
    put.mockResolvedValue({});
    const i18n = await instance('zh');
    const onSwitched = vi.fn();

    const stop = syncAccountLanguage(i18n, 'en', onSwitched);
    await vi.waitFor(() => expect(i18n.language).toBe('en'));

    expect(onSwitched).not.toHaveBeenCalled();
    expect(put).not.toHaveBeenCalled();
    stop();
  });

  it('saves a language the user switches to while signed in, and stops after sign-out', async () => {
    put.mockResolvedValue({});
    const i18n = await instance('en');
    const onSwitched = vi.fn();
    const stop = syncAccountLanguage(i18n, 'en', onSwitched);

    await i18n.changeLanguage('zh');
    expect(onSwitched).toHaveBeenCalledWith('zh');
    await vi.waitFor(() => expect(put).toHaveBeenCalledWith('/api/auth/me/preferences', { ui_language: 'zh' }));

    stop();
    await i18n.changeLanguage('en');
    expect(put).toHaveBeenCalledTimes(1);
  });

  it.each([null, undefined, 'fr'])('backfills an empty or unsupported account language (%s) immediately', async (missing) => {
    put.mockResolvedValue({});
    const i18n = await instance('en-US');
    const onSwitched = vi.fn();

    const stop = syncAccountLanguage(i18n, missing, onSwitched);
    expect(onSwitched).toHaveBeenCalledWith('en');
    await vi.waitFor(() => expect(put).toHaveBeenCalledWith('/api/auth/me/preferences', { ui_language: 'en' }));

    await i18n.changeLanguage('zh');
    expect(onSwitched).toHaveBeenCalledWith('zh');
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls.map(call => call[1])).toEqual([{ ui_language: 'en' }, { ui_language: 'zh' }]);
    stop();
  });

  it('serializes backfill ahead of a subsequent language switch in the same queue', async () => {
    let finishBackfill: () => void = () => {};
    put.mockImplementationOnce(() => new Promise<void>(resolve => { finishBackfill = resolve; }));
    put.mockResolvedValue({});
    const i18n = await instance('en');
    const stop = syncAccountLanguage(i18n, null);

    await i18n.changeLanguage('zh');
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    finishBackfill();
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls.map(call => call[1])).toEqual([{ ui_language: 'en' }, { ui_language: 'zh' }]);
    stop();
  });

  it('logs a warning and does not throw when saving the backfilled language fails', async () => {
    const error = new Error('network down');
    put.mockRejectedValueOnce(error);
    put.mockResolvedValue({});
    const i18n = await instance('en');
    const onSwitched = vi.fn();

    const stop = syncAccountLanguage(i18n, null, onSwitched);
    expect(onSwitched).toHaveBeenCalledWith('en');
    await vi.waitFor(() =>
      expect(warn).toHaveBeenCalledWith('[AccountLanguage] Failed to save the interface language', error),
    );

    // Queue remains healthy for later switches.
    await i18n.changeLanguage('zh');
    await vi.waitFor(() => expect(put).toHaveBeenCalledWith('/api/auth/me/preferences', { ui_language: 'zh' }));
    stop();
  });

  it('sends quick back-and-forth switches in order, so the last one wins', async () => {
    let finishFirst: () => void = () => {};
    put.mockImplementationOnce(() => new Promise<void>(resolve => { finishFirst = resolve; }));
    put.mockResolvedValue({});
    const i18n = await instance('en');
    const stop = syncAccountLanguage(i18n, 'en');

    await i18n.changeLanguage('zh');
    await i18n.changeLanguage('en');
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    finishFirst();
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    expect(put.mock.calls.map(call => call[1])).toEqual([{ ui_language: 'zh' }, { ui_language: 'en' }]);
    stop();
  });
});
