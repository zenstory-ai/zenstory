import { describe, expect, it } from 'vitest';
import { authorNotesText, withAuthorNotes } from '../projectNotes';

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
