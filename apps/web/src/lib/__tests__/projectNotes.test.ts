import { describe, expect, it } from 'vitest';
import {
  adoptProjectProposals,
  authorNotesText,
  ignoreProjectProposals,
  pendingProposalEntries,
  withAuthorNotes,
} from '../projectNotes';

// Shape produced by the server compose: unlabeled, blank, confirmed block, blank, proposals block.
const composed = '旧约定\n第二条\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n提议甲；提议乙；提议丙\n提议丁';
const adoptAll = (notes: string) => adoptProjectProposals(notes, pendingProposalEntries(notes));

describe('pending AI proposal entries', () => {
  it('splits proposal lines like the server, keeping stored text and order', () => {
    expect(pendingProposalEntries(composed)).toEqual(['提议甲', '提议乙', '提议丙', '提议丁']);
    expect(pendingProposalEntries('【AI 提议·待作者确认】\n a ; b \n；')).toEqual(['a', 'b']);
    expect(pendingProposalEntries('旧约定\n【作者确认】\n甲；乙')).toEqual([]);
    expect(pendingProposalEntries(null)).toEqual([]);
  });

  it('drops parts the server entry key treats as empty (punctuation, bullet or self label only)', () => {
    expect(pendingProposalEntries('【AI 提议·待作者确认】\n女主姓林；——\n…；。\n1.；【已确认】；- \n2. 第二条'))
      .toEqual(['女主姓林', '2. 第二条']);
    expect(adoptProjectProposals('【AI 提议·待作者确认】\n女主姓林；——', ['——'])).toBeNull();
  });
});

describe('human adoption of project-note proposals', () => {
  it('adopts one entry of a multi-entry line into the confirmed section, keeping the rest pending', () => {
    expect(adoptProjectProposals(composed, ['提议乙']))
      .toBe('旧约定\n第二条\n\n【作者确认】\n原确认\n提议乙\n\n【AI 提议·待作者确认】\n提议甲；提议丙\n提议丁');
  });

  it('keeps an ASCII separator line on its own separator', () => {
    expect(adoptProjectProposals('【作者确认】\n原确认\n\n【AI 提议·待作者确认】\na;b;c', ['a', 'c']))
      .toBe('【作者确认】\n原确认\na;c\n\n【AI 提议·待作者确认】\nb');
  });

  it('creates the confirmed section before the proposals when absent', () => {
    expect(adoptProjectProposals('旧约定\n\n【AI 提议·待作者确认】\n甲；乙', ['甲']))
      .toBe('旧约定\n\n【作者确认】\n甲\n\n【AI 提议·待作者确认】\n乙');
    expect(adoptProjectProposals('旧约定\n\n【AI 提议·待作者确认】\n甲；乙', ['甲', '乙']))
      .toBe('旧约定\n\n【作者确认】\n甲；乙');
  });

  it('adopting everything removes the proposals header and keeps complete semicolon groups', () => {
    expect(adoptAll('旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n第一组；保留补充\n第二组；保留结尾'))
      .toBe('旧约定\n\n【作者确认】\n原确认\n第一组；保留补充\n第二组；保留结尾');
  });

  it('recognizes the server-rendered header variants and keeps CRLF line endings', () => {
    expect(adoptAll('【备注·未标来源】（旧内容）\r\n旧约定\r\n【备注·作者已确认】（作者保存）\r\n原确认\r\n【备注·ai 提议·待作者确认】（不是设定）\r\n待采纳'))
      .toBe('【备注·未标来源】（旧内容）\r\n旧约定\r\n【备注·作者已确认】（作者保存）\r\n原确认\r\n待采纳');
    expect(ignoreProjectProposals('旧约定\r\n\r\n【AI 提议·待作者确认】\r\n甲；乙', ['乙']))
      .toBe('旧约定\r\n\r\n【AI 提议·待作者确认】\r\n甲');
  });

  it('preserves author indentation and trailing spaces, moving whole proposal lines as written', () => {
    const author = '  旧约定  \n\n【作者确认】\n\t原确认  \n\n  第二段\n\n';
    expect(adoptAll(author + '【AI 提议·待作者确认】\n  新提议  \n\n补充'))
      .toBe('  旧约定  \n\n【作者确认】\n\t原确认  \n\n  第二段\n  新提议  \n补充');
  });

  it('ignores entries by dropping them, and the header with the last one', () => {
    expect(ignoreProjectProposals(composed, ['提议甲', '提议丙']))
      .toBe('旧约定\n第二条\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n提议乙\n提议丁');
    expect(ignoreProjectProposals(composed, pendingProposalEntries(composed)))
      .toBe('旧约定\n第二条\n\n【作者确认】\n原确认');
  });

  it.each(['', '作者正文提到了 AI 提议，但没有分区', '【作者确认】\n已有约定', '【AI 提议·待作者确认】\n\n【作者确认】\n已有约定'])('offers nothing to adopt for absent or empty proposals: %s', notes => {
    expect(adoptAll(notes)).toBeNull();
  });

  it('returns null when none of the given entries is still pending (exact text only)', () => {
    expect(adoptProjectProposals(composed, ['原确认', '提议'])).toBeNull();
    expect(ignoreProjectProposals(composed, ['提议甲。'])).toBeNull();
    expect(adoptProjectProposals(composed, [' 提议甲 '])).not.toBeNull();
  });
});

describe('author notes without section headers', () => {
  it('shows unlabeled then confirmed lines without headers or the proposals block', () => {
    expect(authorNotesText(composed)).toBe('旧约定\n第二条\n\n原确认');
    expect(authorNotesText('【AI 提议·待作者确认】\n甲')).toBe('');
    expect(authorNotesText('legacy line\n\n  second  ')).toBe('legacy line\n\n  second  ');
  });

  it.each([
    composed,
    '旧约定\n\n【作者确认】\n原确认',
    '【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n甲',
    '【AI 提议·待作者确认】\n甲',
    'legacy only\n\nwith blank lines  ',
    '旧约定\r\n\r\n【作者确认】\r\n原确认\r\n\r\n【AI 提议·待作者确认】\r\n甲',
    '',
  ])('an untouched textarea never changes the stored notes: %j', stored => {
    expect(withAuthorNotes(stored, authorNotesText(stored))).toBe(stored);
  });

  it('rebuilds canonical server text after an edit and keeps the proposals unchanged', () => {
    expect(withAuthorNotes(composed, '旧约定\n第二条\n新约定\n\n原确认'))
      .toBe('旧约定\n第二条\n新约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n提议甲；提议乙；提议丙\n提议丁');
  });

  it('turns an edited confirmed line into an unlabeled one and keeps typed order stable', () => {
    const edited = '旧约定\n\n原确认改\n末尾新行\n';
    const next = withAuthorNotes(composed, edited);
    expect(next).toBe('旧约定\n\n原确认改\n末尾新行\n\n\n【AI 提议·待作者确认】\n提议甲；提议乙；提议丙\n提议丁');
    expect(authorNotesText(next)).toBe(edited);
    // A typed line after a confirmed one stays where the author typed it.
    const appended = withAuthorNotes(composed, authorNotesText(composed) + '\n新行');
    expect(appended).toContain('【作者确认】\n原确认\n【未标来源】\n新行\n\n【AI 提议');
    expect(authorNotesText(appended)).toBe(authorNotesText(composed) + '\n新行');
  });

  it('restores a confirmed line typed back from the saved baseline', () => {
    const broken = withAuthorNotes(composed, '旧约定\n第二条\n\n原确认x');
    expect(broken).not.toContain('【作者确认】');
    expect(withAuthorNotes(broken, '旧约定\n第二条\n\n原确认', composed)).toBe(composed);
  });

  it.each(['【AI 提议】', '【AI提议 草稿】', '【作者确认】', '  【备注·作者已确认】（x）', '【未标来源】'])(
    'a typed header-shaped line %j stays author text and promotes or demotes nothing', header => {
      const stored = '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n甲';
      const edited = `旧约定\n${header}\n女主姓林\n\n原确认`;
      const next = withAuthorNotes(stored, edited, stored);
      expect(authorNotesText(next)).toBe(edited);
      expect(pendingProposalEntries(next)).toEqual(['甲']);
      expect(next).toContain('旧约定\n\u200b');
      expect(next).toContain('【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n甲');
      expect(next.split('\n').filter(line => line === '【作者确认】')).toHaveLength(1);
      // Escaping is reversible, also for a line that already starts with the escape character.
      const escapedTyped = `\u200b${header}`;
      expect(authorNotesText(withAuthorNotes(stored, escapedTyped))).toBe(escapedTyped);
    });

  it('typing back the saved text of a non-canonical layout returns the saved notes unchanged', () => {
    for (const legacy of [
      'A\n【AI 提议·待作者确认】\nP',
      '【备注·作者已确认】（作者保存）\n原确认\n【AI 提议】\nP',
      'A\n【作者已确认】\nB\n\n\n【AI 提议·待作者确认】\nP',
    ]) {
      const typed = withAuthorNotes(legacy, authorNotesText(legacy) + 'X', legacy);
      expect(typed).not.toBe(legacy);
      expect(withAuthorNotes(typed, authorNotesText(legacy), legacy)).toBe(legacy);
    }
    // Different proposals than the baseline (e.g. a newer AI write) are not reverted.
    const base = 'A\n【AI 提议·待作者确认】\nP';
    const newer = withAuthorNotes('A\n\n【AI 提议·待作者确认】\nP；Q', 'AX', base);
    expect(withAuthorNotes(newer, 'A', base)).toBe('A\n\n【AI 提议·待作者确认】\nP；Q');
  });

  it('clearing the author text keeps only the proposals', () => {
    expect(withAuthorNotes(composed, '')).toBe('【AI 提议·待作者确认】\n提议甲；提议乙；提议丙\n提议丁');
    expect(withAuthorNotes('旧约定', '')).toBe('');
  });
});
