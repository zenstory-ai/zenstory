import { describe, expect, it } from 'vitest';
import { authorNotesText, withAuthorNotes, withoutNoteEntries } from '../projectNotes';

const author = '旧约定\n第二条\n\n【作者确认】\n原确认';
// Notes saved before the AI-proposal removal: the author's part, a blank line, the legacy block.
const legacy = `${author}\n\n【AI 提议·待作者确认】\n提议甲；提议乙\n提议丁`;

describe('author notes without section headers', () => {
  it('shows unlabeled then confirmed lines without headers or a legacy proposal block', () => {
    expect(authorNotesText(author)).toBe('旧约定\n第二条\n\n原确认');
    expect(authorNotesText(legacy)).toBe('旧约定\n第二条\n\n原确认');
    expect(authorNotesText('【AI 提议·待作者确认】\n甲')).toBe('');
    expect(authorNotesText('甲\n【备注·作者已忽略的 AI 提议】（不要再提出）\n乙\n【作者确认】\n丙')).toBe('甲\n丙');
    expect(authorNotesText('legacy line\n\n  second  ')).toBe('legacy line\n\n  second  ');
  });

  it.each([
    author,
    '旧约定\r\n\r\n【作者确认】\r\n原确认',
    'legacy only\n\nwith blank lines  ',
    '',
  ])('an untouched textarea never changes the stored notes: %j', stored => {
    expect(withAuthorNotes(stored, authorNotesText(stored))).toBe(stored);
  });

  it('an edit drops a legacy proposal block instead of writing it back', () => {
    const next = withAuthorNotes(legacy, '旧约定\n第二条\n新约定\n\n原确认');
    expect(next).toBe('旧约定\n第二条\n新约定\n\n【作者确认】\n原确认');
    expect(withAuthorNotes(legacy, '')).toBe('');
  });

  it('turns an edited confirmed line into an unlabeled one and keeps typed order stable', () => {
    const edited = '旧约定\n\n原确认改\n末尾新行\n';
    const next = withAuthorNotes(author, edited);
    expect(next).toBe(edited);
    expect(authorNotesText(next)).toBe(edited);
    // A typed line after a confirmed one stays where the author typed it.
    const appended = withAuthorNotes(author, authorNotesText(author) + '\n新行');
    expect(appended).toBe(`${author}\n【未标来源】\n新行`);
    expect(authorNotesText(appended)).toBe(authorNotesText(author) + '\n新行');
  });

  it('restores a confirmed line typed back from the saved baseline', () => {
    const broken = withAuthorNotes(author, '旧约定\n第二条\n\n原确认x');
    expect(broken).not.toContain('【作者确认】');
    expect(withAuthorNotes(broken, '旧约定\n第二条\n\n原确认', author)).toBe(author);
  });

  it.each(['【AI 提议】', '【AI提议 草稿】', '【作者确认】', '  【备注·作者已确认】（x）', '【未标来源】'])(
    'a typed header-shaped line %j stays author text and promotes or hides nothing', header => {
      const edited = `旧约定\n${header}\n女主姓林\n\n原确认`;
      const next = withAuthorNotes(author, edited, author);
      expect(authorNotesText(next)).toBe(edited);
      expect(next).toContain('旧约定\n\u200b');
      expect(next.split('\n').filter(line => line === '【作者确认】')).toHaveLength(1);
      // Escaping is reversible, also for a line that already starts with the escape character.
      const escapedTyped = `\u200b${header}`;
      expect(authorNotesText(withAuthorNotes(author, escapedTyped))).toBe(escapedTyped);
    });

  it('typing back the saved text of a non-canonical layout returns the saved notes unchanged', () => {
    for (const saved of ['【备注·作者已确认】（作者保存）\n原确认', 'A\n【作者已确认】\nB\n\n\n']) {
      const typed = withAuthorNotes(saved, authorNotesText(saved) + 'X', saved);
      expect(typed).not.toBe(saved);
      expect(withAuthorNotes(typed, authorNotesText(saved), saved)).toBe(saved);
    }
  });
});

describe('undoing entries the chat AI appended', () => {
  const stored = '旧约定\n\n【作者确认】\n原确认\n【未标来源】\n女主要到第五章才出场\n主角左手有旧伤，阴天会疼';

  it('removes exactly the appended lines and the header they needed', () => {
    expect(withoutNoteEntries(stored, ['女主要到第五章才出场', '主角左手有旧伤，阴天会疼'])).toEqual({
      text: '旧约定\n\n【作者确认】\n原确认',
      removed: ['女主要到第五章才出场', '主角左手有旧伤，阴天会疼'],
      missing: [],
    });
    expect(withoutNoteEntries('甲\n乙', ['乙'])).toEqual({ text: '甲', removed: ['乙'], missing: [] });
  });

  it('keeps an edited entry and the header that still has entries', () => {
    const edited = stored.replace('主角左手有旧伤，阴天会疼', '主角左手有旧伤，雨天会疼');
    expect(withoutNoteEntries(edited, ['女主要到第五章才出场', '主角左手有旧伤，阴天会疼'])).toEqual({
      text: '旧约定\n\n【作者确认】\n原确认\n【未标来源】\n主角左手有旧伤，雨天会疼',
      removed: ['女主要到第五章才出场'],
      missing: ['主角左手有旧伤，阴天会疼'],
    });
  });

  it('removes the last copy only, never a header or a confirmed line by a different text', () => {
    expect(withoutNoteEntries('乙\n甲\n乙', ['乙']).text).toBe('乙\n甲');
    expect(withoutNoteEntries(stored, ['【作者确认】', '原确认 ']).removed).toEqual(['原确认 ']);
    expect(withoutNoteEntries(stored, ['不存在'])).toEqual({ text: stored, removed: [], missing: ['不存在'] });
  });

  it('keeps every other author byte: unrelated empty headers, trailing whitespace, line endings', () => {
    // An empty 【未标来源】 header the AI did not introduce stays, and so does the author's tail.
    const unrelated = '【未标来源】\n【作者确认】\n原确认\n【未标来源】\n新条目\n作者的尾巴  \n\n';
    expect(withoutNoteEntries(unrelated, ['新条目']).text)
      .toBe('【未标来源】\n【作者确认】\n原确认\n【未标来源】\n作者的尾巴  \n\n');
    expect(withoutNoteEntries('甲\r\n乙\n丙', ['乙']).text).toBe('甲\r\n丙');
    expect(withoutNoteEntries('甲\r\n乙\n丙', ['丙']).text).toBe('甲\r\n乙');
  });
});
