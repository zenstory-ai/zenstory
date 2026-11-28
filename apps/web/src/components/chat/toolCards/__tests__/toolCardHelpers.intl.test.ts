import { createInstance } from 'i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import enChat from '../../../../../public/locales/en/chat.json';
import zhChat from '../../../../../public/locales/zh/chat.json';
import {
  classifyToolFailure,
  formatWordCount,
  resolveToolFailure,
  type TranslateFn,
} from '../toolCardHelpers';
import { formatChatSessionTitle } from '../../chatHistoryHelpers';
import { formatDailyResetLocalTime } from '../../../../lib/dateUtils';
import { passwordResetApi } from '../../../../lib/passwordResetApi';
import i18n from '../../../../lib/i18n';

async function makeTranslator(lng: 'en' | 'zh') {
  const instance = createInstance();
  await instance.init({
    lng,
    fallbackLng: lng,
    interpolation: { escapeValue: false },
    resources: {
      en: { chat: enChat },
      zh: { chat: zhChat },
    },
  });
  return instance.t.bind(instance);
}

describe('toolCardHelpers international failure coverage & word unit parity', () => {
  const originalLanguage = i18n.language;

  afterEach(() => {
    Object.defineProperty(i18n, 'language', { value: originalLanguage, configurable: true });
    vi.restoreAllMocks();
  });

  const NEW_ERROR_TYPES: Array<[string, string]> = [
    ['source_text_not_in_author_message', 'sourceTextNotInMessage'],
    ['invalid_source_text', 'invalidSourceText'],
    ['source_text_conflict', 'sourceTextConflict'],
    ['invalid_source_text_file_type', 'invalidSourceTextFileType'],
    ['source_text_context_missing', 'sourceTextContextMissing'],
    ['tool_failure_circuit_open', 'toolFailureCircuitOpen'],
    ['update_plan_rejected', 'updatePlanRejected'],
    ['chapter_body_requires_write_chapter', 'chapterBodyRequiresWriteChapter'],
    ['invalid_tool_input_json', 'invalidToolInputJson'],
    ['file_body_pending', 'pendingFileBody'],
    ['pending_empty_file_unwritten', 'pendingFileBody'],
    ['chapter_written_this_turn', 'chapterWrittenThisTurn'],
    ['wall_clock_wrap_up', 'wallClockWrapUp'],
    ['edit_repeat_limit', 'editRepeatLimit'],
  ];

  it.each(NEW_ERROR_TYPES)(
    'maps error_type %s to kind %s with crisp localized en and zh hints and never leaks raw error text',
    async (errorType, expectedKind) => {
      expect(classifyToolFailure(errorType)).toBe(expectedKind);

      const enT = await makeTranslator('en');
      const zhT = await makeTranslator('zh');
      const payload = {
        error_type: errorType,
        error: 'INTERNAL_RAW_TRACEBACK_DO_NOT_DISPLAY',
        user_message: '仅限中文界面展示的提示',
      };

      Object.defineProperty(i18n, 'language', { value: 'en', configurable: true });
      const enResolved = resolveToolFailure(payload, enT as unknown as TranslateFn);
      expect(enResolved.kind).toBe(expectedKind);
      expect(enResolved.message).not.toContain('INTERNAL_RAW_TRACEBACK_DO_NOT_DISPLAY');
      expect(enResolved.message).not.toContain('仅限中文');
      expect(enResolved.message).not.toBe(enChat.tool.failureHint.generic);
      expect(enResolved.message.length).toBeGreaterThan(10);

      Object.defineProperty(i18n, 'language', { value: 'zh', configurable: true });
      const zhWithoutUserMsg = resolveToolFailure({ error_type: errorType, error: 'RAW' }, zhT as unknown as TranslateFn);
      expect(zhWithoutUserMsg.kind).toBe(expectedKind);
      expect(zhWithoutUserMsg.message).not.toContain('RAW');
      expect(zhWithoutUserMsg.message).not.toBe(zhChat.tool.failureHint.generic);
    },
  );

  it('formats word counts with words (not chars) in English and 字 in Chinese', async () => {
    const enT = await makeTranslator('en');
    const zhT = await makeTranslator('zh');

    expect(formatWordCount(420, enT as unknown as TranslateFn)).toBe('420 words');
    expect(formatWordCount(1500, enT as unknown as TranslateFn)).toBe('1.5k words');
    expect(formatWordCount(420, zhT as unknown as TranslateFn)).toBe('420 字');
    expect(formatWordCount(1500, zhT as unknown as TranslateFn)).toBe('1.5 千字');
  });
});

describe('formatChatSessionTitle legacy default localization', () => {
  it('localizes legacy Chinese default session titles on non-zh UI while keeping custom titles intact', async () => {
    const enT = await makeTranslator('en');
    const zhT = await makeTranslator('zh');

    expect(formatChatSessionTitle('AI 助手对话', enT, 'en')).toBe('AI Assistant');
    expect(formatChatSessionTitle('新对话', enT, 'en')).toBe('New Chat');
    expect(formatChatSessionTitle('AI 助手对话', enT, 'id')).toBe('AI Assistant');
    expect(formatChatSessionTitle('Chapter 3 Revision', enT, 'en')).toBe('Chapter 3 Revision');

    expect(formatChatSessionTitle('AI 助手对话', zhT, 'zh-CN')).toBe('AI 创作助手');
    expect(formatChatSessionTitle('新对话', zhT, 'zh-CN')).toBe('新对话');
    expect(formatChatSessionTitle('第三章大纲讨论', zhT, 'zh-CN')).toBe('第三章大纲讨论');
  });
});

describe('formatDailyResetLocalTime', () => {
  // Summer date so EDT (UTC-4) is deterministic
  const summerDate = new Date('2026-07-15T10:00:00Z');

  it('formats 00:00 UTC+8 in the viewer local timezone and returns null in UTC+8', () => {
    expect(formatDailyResetLocalTime('en', 'America/New_York', summerDate)).toBe('12:00 PM');
    expect(formatDailyResetLocalTime('en', 'UTC', summerDate)).toBe('4:00 PM');
    expect(formatDailyResetLocalTime('en', 'Asia/Jakarta', summerDate)).toBe('11:00 PM');
    expect(formatDailyResetLocalTime('en', 'Asia/Shanghai', summerDate)).toBeNull();
    expect(formatDailyResetLocalTime('en', 'Asia/Singapore', summerDate)).toBeNull();
  });
});

describe('passwordResetApi Accept-Language hygiene', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('defaults Accept-Language to en (never zh) when no preference is stored', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'ok' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await passwordResetApi.confirm('writer@example.com', '123456', 'secret123');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>)['Accept-Language']).toBe('en');
  });
});
